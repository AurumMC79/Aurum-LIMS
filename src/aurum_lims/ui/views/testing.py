"""Testing page: the worklist of open tests."""

from __future__ import annotations

from datetime import date

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import testing
from aurum_lims.services.testing import AnalysisRow, AnalysisStatus
from aurum_lims.ui.common import (
    allow,
    GridTable,
    ask_reason,
    danger_button,
    format_local,
    muted_label,
    primary_button,
    show_error,
)
from aurum_lims.ui.testing_dialogs import ResultEntryDialog


class TestingView(QWidget):
    __test__ = False
    title = "Testing"
    COLUMNS = ["Sample ID", "Material / point", "Test", "Status", "Assigned to", "Priority", "Due", "Added"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows: list[AnalysisRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "Open tests, urgent and earliest due first. Tests are added to samples on the Samples page (Tests…)."
        ))
        toolbar = QHBoxLayout()
        self.enter_button = primary_button("Enter results")
        self.enter_button.setDefault(False)
        self.cancel_button = danger_button("Cancel test")
        allow(self.cancel_button, user.can(Permission.EDIT_RECORDS))
        if not user.can(Permission.EDIT_RECORDS):
            self.enter_button.setText("View results")
        self.mine = QCheckBox("Only tests assigned to me")
        self.status = QComboBox()
        self.status.addItem("Pending and in progress", None)
        for status in AnalysisStatus:
            self.status.addItem(status.value, status.value)
        self.count = muted_label("")
        for widget in (self.enter_button, self.cancel_button):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        toolbar.addWidget(self.mine)
        toolbar.addWidget(self.status)
        toolbar.addWidget(self.count)
        layout.addLayout(toolbar)
        self.table = GridTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self._enter())
        layout.addWidget(self.table, 1)
        self.enter_button.clicked.connect(lambda *_: self._enter())
        self.cancel_button.clicked.connect(lambda *_: self._cancel())
        self.mine.toggled.connect(lambda *_: self.refresh())
        self.status.currentIndexChanged.connect(lambda *_: self.refresh())
        self._update_buttons()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.rows = testing.worklist(session, self.user, mine_only=self.mine.isChecked(),
                                         status=self.status.currentData())
        today = date.today()
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            values = [row.sample_id, row.material or "", f"{row.method_code} v{row.method_version} – {row.method_name}",
                      row.status, row.assigned_to or "", row.priority,
                      row.due_date.isoformat() if row.due_date else "", format_local(row.created_at)]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if (c == 5 and row.priority == "Urgent") or (c == 6 and row.due_date and row.due_date < today):
                    item.setForeground(QColor("#b42318"))
                if c == 3 and row.status == AnalysisStatus.RETURNED:
                    item.setForeground(QColor("#b54708"))
                    item.setToolTip("Returned by the reviewer for correction")
                self.table.setItem(r, c, item)
        self.count.setText(f"{len(self.rows)} test(s)")
        self._update_buttons()

    def _selected(self) -> AnalysisRow | None:
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else None

    def _update_buttons(self) -> None:
        row = self._selected()
        self.enter_button.setEnabled(row is not None)
        self.cancel_button.setEnabled(row is not None and row.status in testing.OPEN_ANALYSIS)

    def _enter(self) -> None:
        row = self._selected()
        if row is not None:
            ResultEntryDialog(self.db, self.user, row.id, self).exec()
            self.refresh()

    def _cancel(self) -> None:
        row = self._selected()
        if row is None:
            return
        reason = ask_reason(self, "Cancel test", f"Cancel {row.method_code} on {row.sample_id}?")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                testing.cancel_analysis(session, self.user, row.id, reason)
        except AurumError as exc:
            show_error(self, exc)
        self.refresh()
