"""Samples page: find samples and act on them (receive, transfer, aliquot, dispose...)."""

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
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import samples, sample_types, storage
from aurum_lims.services.samples import ON_HAND_STATUSES, SampleStatus, SampleSummary
from aurum_lims.services.testing import ASSIGNABLE
from aurum_lims.ui.testing_dialogs import AssignTestsDialog
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
from aurum_lims.ui.sample_dialogs import (
    AliquotDialog,
    DisposeDialog,
    EditSampleDialog,
    ReceiveDialog,
    RegisterSampleDialog,
    SampleDetailDialog,
    TransferDialog,
    show_created,
)

STATUS_COLORS = {
    SampleStatus.REGISTERED: "#8a6d15",
    SampleStatus.RECEIVED: "#067647",
    SampleStatus.DISPOSED: "#8b98a8",
    SampleStatus.CANCELLED: "#8b98a8",
}


class SamplesView(QWidget):
    title = "Samples"
    COLUMNS = ["Sample ID", "Type", "Material / point", "Lot / batch", "Status", "Location / holder",
               "Priority", "Due", "Registered"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows: list[SampleSummary] = []
        self.location_paths: dict[int, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self.register_button = primary_button("Register samples")
        self.details_button = QPushButton("Details")
        self.tests_button = QPushButton("Tests…")
        self.tests_button.setToolTip("Choose the specification and add tests to this sample")
        self.receive_button = QPushButton("Receive")
        self.transfer_button = QPushButton("Transfer")
        self.aliquot_button = QPushButton("Aliquots")
        self.edit_button = QPushButton("Correct details")
        self.dispose_button = danger_button("Dispose")
        self.cancel_button = danger_button("Cancel registration")
        allow(self.register_button, user.can(Permission.CREATE_RECORDS))
        allow(self.aliquot_button, user.can(Permission.CREATE_RECORDS))
        allow(self.tests_button, user.can(Permission.CREATE_RECORDS) and user.can(Permission.EDIT_RECORDS))
        for button in (self.receive_button, self.transfer_button, self.edit_button, self.dispose_button,
                       self.cancel_button):
            allow(button, user.can(Permission.EDIT_RECORDS))
        for button in (self.register_button, self.details_button, self.tests_button, self.receive_button,
                       self.transfer_button,
                       self.aliquot_button, self.edit_button, self.dispose_button, self.cancel_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        filters = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search sample ID, material, lot, supplier, sampling point…")
        self.search_box.returnPressed.connect(self.refresh)
        self.type_filter = QComboBox()
        self.status_filter = QComboBox()
        self.status_filter.addItem("All open statuses", None)
        for status in SampleStatus:
            self.status_filter.addItem(status.value, status.value)
        self.show_closed = QCheckBox("Include disposed / cancelled")
        self.count_label = muted_label("")
        filters.addWidget(self.search_box, 2)
        filters.addWidget(self.type_filter, 1)
        filters.addWidget(self.status_filter, 1)
        filters.addWidget(self.show_closed)
        filters.addWidget(self.count_label)
        layout.addLayout(filters)

        self.table = GridTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self._details())
        layout.addWidget(self.table)

        self.register_button.clicked.connect(lambda *_: self._register())
        self.details_button.clicked.connect(lambda *_: self._details())
        self.tests_button.clicked.connect(lambda *_: self._tests())
        self.receive_button.clicked.connect(lambda *_: self._open(ReceiveDialog))
        self.transfer_button.clicked.connect(lambda *_: self._open(TransferDialog))
        self.aliquot_button.clicked.connect(lambda *_: self._aliquot())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.dispose_button.clicked.connect(lambda *_: self._open(DisposeDialog))
        self.cancel_button.clicked.connect(lambda *_: self._cancel())
        self.type_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        self.status_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        self.show_closed.toggled.connect(lambda *_: self.refresh())
        self._load_types()
        self._update_buttons()

    def _load_types(self) -> None:
        self.type_filter.blockSignals(True)
        current = self.type_filter.currentData()
        self.type_filter.clear()
        self.type_filter.addItem("All sample types", None)
        with self.db.session() as session:
            for t in sample_types.list_types(session, include_inactive=True):
                self.type_filter.addItem(t.name, t.id)
                if t.id == current:
                    self.type_filter.setCurrentIndex(self.type_filter.count() - 1)
        self.type_filter.blockSignals(False)

    def refresh(self) -> None:
        if not hasattr(self, "table"):
            return
        self._load_types()
        selected = self._selected()
        with self.db.session() as session:
            self.location_paths = storage.paths(session)
            self.rows = samples.search(
                session, self.user, text=self.search_box.text().strip() or None,
                status=self.status_filter.currentData(), sample_type_id=self.type_filter.currentData(),
                include_closed=self.show_closed.isChecked(),
            )
        today = date.today()
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            where = self.location_paths.get(row.location_id, "") if row.location_id else (
                f"With {row.holder_name}" if row.holder_name else "")
            values = [
                row.sample_id, row.type_name, row.material_name or "", row.lot_number or "", row.status, where,
                row.priority, row.due_date.isoformat() if row.due_date else "", format_local(row.created_at),
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 4 and row.status in STATUS_COLORS:
                    item.setForeground(QColor(STATUS_COLORS[row.status]))
                if c == 6 and row.priority == "Urgent":
                    item.setForeground(QColor("#b42318"))
                if c == 7 and row.due_date and row.due_date < today and row.status not in (
                        SampleStatus.DISPOSED, SampleStatus.CANCELLED):
                    item.setForeground(QColor("#b42318"))
                    item.setToolTip("Overdue")
                self.table.setItem(r, c, item)
        if selected:
            for r, row in enumerate(self.rows):
                if row.id == selected.id:
                    self.table.selectRow(r)
        self.count_label.setText(f"{len(self.rows)} sample(s)")
        self._update_buttons()

    def _selected(self) -> SampleSummary | None:
        model = self.table.selectionModel()
        indexes = model.selectedRows() if model else []
        if not indexes or indexes[0].row() >= len(self.rows):
            return None
        return self.rows[indexes[0].row()]

    def _update_buttons(self) -> None:
        row = self._selected()
        status = row.status if row else None
        on_hand = status in ON_HAND_STATUSES
        self.details_button.setEnabled(row is not None)
        self.tests_button.setEnabled(status in ASSIGNABLE)
        self.receive_button.setEnabled(status == SampleStatus.REGISTERED)
        self.transfer_button.setEnabled(on_hand)
        self.aliquot_button.setEnabled(on_hand)
        self.dispose_button.setEnabled(on_hand)
        self.cancel_button.setEnabled(status == SampleStatus.REGISTERED)
        self.edit_button.setEnabled(row is not None and status not in (SampleStatus.DISPOSED, SampleStatus.CANCELLED))

    # ----- actions -----
    def _register(self) -> None:
        dialog = RegisterSampleDialog(self.db, self.user, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            show_created(self, "Samples registered", dialog.created)

    def _details(self) -> None:
        row = self._selected()
        if row is not None and SampleDetailDialog(self.db, self.user, row.id, self).exec() == QDialog.Accepted:
            self.refresh()  # reopened for amendment

    def _tests(self) -> None:
        row = self._selected()
        if row is not None and AssignTestsDialog(self.db, self.user, row.id, self).exec() == QDialog.Accepted:
            self.refresh()

    def _open(self, dialog_class) -> None:
        row = self._selected()
        if row is not None and dialog_class(self.db, self.user, row.id, row.sample_id, self).exec() == QDialog.Accepted:
            self.refresh()

    def _aliquot(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = AliquotDialog(self.db, self.user, row.id, row.sample_id, self)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            show_created(self, "Aliquots created", dialog.created)

    def _edit(self) -> None:
        row = self._selected()
        if row is not None and EditSampleDialog(self.db, self.user, row.id, self).exec() == QDialog.Accepted:
            self.refresh()

    def _cancel(self) -> None:
        row = self._selected()
        if row is None:
            return
        reason = ask_reason(self, "Cancel registration",
                            f"Cancel {row.sample_id}? Use this when a registered sample never arrives.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                samples.cancel_sample(session, self.user, row.id, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()
