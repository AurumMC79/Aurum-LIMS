"""OOS page: out-of-specification investigations."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.db.models import Sample, User
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import oos
from aurum_lims.services.esign import SignatureMeaning
from aurum_lims.ui.common import Card, error_label, format_local, muted_label, primary_button, show_label_message
from aurum_lims.ui.sign_dialog import SignDialog


class OOSDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, oos_id: int, parent=None):
        super().__init__(parent)
        self.db, self.user, self.oos_id = db, user, oos_id
        self.setMinimumSize(640, 600)
        with db.session() as session:
            record = oos.get(session, user, oos_id)
            sample_id = session.get(Sample, record.sample_id).sample_id
            names = {u.id: u.full_name for u in session.query(User)}
            self.number, self.status = record.number, record.status
            info = [
                ("Sample", sample_id), ("Result", f"{record.parameter_name} = {record.result_value}"),
                ("Limit", record.spec_text or ""), ("Opened", f"{format_local(record.opened_at)} by "
                                                              f"{names.get(record.opened_by_id, '?')}"),
                ("Analyst's comment", record.description),
            ]
            if record.closed_at:
                info.append(("Closed", f"{format_local(record.closed_at)} by {names.get(record.closed_by_id, '?')} "
                                       f"— {record.conclusion}"))
            phase, notes, root = record.phase, record.investigation_notes or "", record.root_cause or ""
        self.setWindowTitle(f"{self.number} – {self.status}")
        is_open = self.status == oos.OPEN
        can_edit = is_open and (user.can(Permission.EDIT_RECORDS) or user.can(Permission.REVIEW_RECORDS))

        layout = QVBoxLayout(self)
        card = Card(f"{self.number} · {self.status}")
        form = QFormLayout()
        for label, value in info:
            v = QLabel(value)
            v.setWordWrap(True)
            form.addRow(label, v)
        card.body.addLayout(form)
        layout.addWidget(card)
        edit = QFormLayout()
        self.phase = QComboBox()
        self.phase.addItems(oos.PHASES)
        self.phase.setCurrentText(phase)
        self.notes = QPlainTextEdit(notes)
        self.notes.setPlaceholderText("Laboratory checks: calculations, instrument, standards, sample prep, retests…")
        self.root_cause = QPlainTextEdit(root)
        self.root_cause.setPlaceholderText("Root cause / justification")
        for w in (self.phase, self.notes, self.root_cause):
            w.setEnabled(can_edit)
        edit.addRow("Phase", self.phase)
        edit.addRow("Investigation notes", self.notes)
        edit.addRow("Root cause", self.root_cause)
        layout.addLayout(edit, 1)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        if can_edit:
            save = QPushButton("Save notes")
            save.clicked.connect(lambda *_: self._save())
            buttons.addWidget(save)
        if is_open and user.can(Permission.REVIEW_RECORDS):
            close_button = primary_button("Close investigation…")
            close_button.setDefault(False)
            close_button.clicked.connect(lambda *_: self._close_investigation())
            buttons.addWidget(close_button)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)

    def _save(self, quiet: bool = False) -> bool:
        try:
            with self.db.session() as session:
                oos.update(session, self.user, self.oos_id, phase=self.phase.currentText(),
                           investigation_notes=self.notes.toPlainText(), root_cause=self.root_cause.toPlainText())
        except AurumError as exc:
            if quiet and "Nothing was changed" in str(exc):
                return True
            show_label_message(self.error, str(exc))
            return False
        show_label_message(self.error, "" if quiet else "")
        return True

    def _close_investigation(self) -> None:
        if not self._save(quiet=True):
            return
        extra = QWidget()
        form = QFormLayout(extra)
        form.setContentsMargins(0, 0, 0, 0)
        conclusion = QComboBox()
        conclusion.addItems(oos.CONCLUSIONS)
        form.addRow("Conclusion", conclusion)
        form.addRow(muted_label(f"Root cause: {self.root_cause.toPlainText().strip() or '(not entered yet)'}"))

        def action(password):
            with self.db.session() as session:
                return oos.close(session, self.user, self.oos_id, conclusion=conclusion.currentText(),
                                 root_cause=self.root_cause.toPlainText(), password=password)

        statement = (f"By entering your password you electronically sign:\n“{SignatureMeaning.APPROVED}” "
                     f"{self.user.full_name} — closure of {self.number}.")
        if SignDialog(self, f"Close {self.number}", statement, action, "Sign and close", extra).exec() == QDialog.Accepted:
            self.accept()


class OOSView(QWidget):
    title = "OOS"
    COLUMNS = ["Number", "Sample", "Result", "Limit", "Phase", "Status", "Conclusion", "Opened", "By"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "An investigation opens automatically when a test is signed with a result outside its limit. "
            "Record the laboratory investigation here; QA closes it with a conclusion and an electronic signature."
        ))
        toolbar = QHBoxLayout()
        self.open_button = primary_button("Open")
        self.open_button.setDefault(False)
        self.include_closed = QCheckBox("Include closed")
        self.count = muted_label("")
        toolbar.addWidget(self.open_button)
        toolbar.addStretch()
        toolbar.addWidget(self.include_closed)
        toolbar.addWidget(self.count)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._open())
        self.table.itemSelectionChanged.connect(lambda: self.open_button.setEnabled(self._selected() is not None))
        layout.addWidget(self.table, 1)
        self.open_button.clicked.connect(lambda *_: self._open())
        self.include_closed.toggled.connect(lambda *_: self.refresh())
        self.open_button.setEnabled(False)

    def refresh(self) -> None:
        with self.db.session() as session:
            self.rows = oos.list_investigations(session, self.user, include_closed=self.include_closed.isChecked())
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            values = [row.number, row.sample_id, f"{row.parameter_name} = {row.result_value}", row.spec_text or "",
                      row.phase.split(" – ")[0], row.status, row.conclusion or "", format_local(row.opened_at),
                      row.opened_by]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 5:
                    item.setForeground(QColor("#b42318" if row.status == oos.OPEN else "#067647"))
                self.table.setItem(r, c, item)
        self.count.setText(f"{len(self.rows)} investigation(s)")

    def _selected(self):
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else None

    def _open(self) -> None:
        row = self._selected()
        if row is not None:
            OOSDialog(self.db, self.user, row.id, self).exec()
            self.refresh()
