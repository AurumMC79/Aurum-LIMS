"""Instruments page: register, calibration / maintenance status and history."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import instruments
from aurum_lims.services.instruments import CATEGORIES, CHECK_EVENTS, RESULTS, EventType, InstrumentDetails
from aurum_lims.ui.common import (
    GridTable,
    ask_reason,
    error_label,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)

RED, AMBER, GREEN, GREY = "#b42318", "#b54708", "#067647", "#8b98a8"


def date_edit(value: date | None = None) -> QDateEdit:
    edit = QDateEdit(QDate(value) if value else QDate.currentDate())
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    return edit


def _table(columns: list[str], stretch: int) -> QTableWidget:
    table = GridTable(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    return table


class InstrumentDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {existing.code}" if existing else "New instrument")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.code = QLineEdit(existing.code if existing else "")
        self.code.setPlaceholderText("Asset / equipment ID, e.g. HPLC-01")
        self.code.setEnabled(existing is None)
        self.name = QLineEdit(existing.name if existing else "")
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        if existing:
            self.category.setCurrentText(existing.category)
        self.manufacturer = QLineEdit(existing.manufacturer or "" if existing else "")
        self.model = QLineEdit(existing.model or "" if existing else "")
        self.serial = QLineEdit(existing.serial_number or "" if existing else "")
        self.location = QLineEdit(existing.location or "" if existing else "")
        self.cal_interval = QSpinBox()
        self.cal_interval.setRange(0, 3650)
        self.cal_interval.setSpecialValueText("none")
        self.cal_interval.setSuffix(" days")
        self.cal_interval.setValue((existing.calibration_interval_days or 0) if existing else 365)
        self.maint_interval = QSpinBox()
        self.maint_interval.setRange(0, 3650)
        self.maint_interval.setSpecialValueText("none")
        self.maint_interval.setSuffix(" days")
        self.maint_interval.setValue((existing.maintenance_interval_days or 0) if existing else 0)
        self.notes = QPlainTextEdit(existing.notes or "" if existing else "")
        self.notes.setFixedHeight(50)
        form.addRow("Instrument ID *", self.code)
        form.addRow("Name *", self.name)
        form.addRow("Category", self.category)
        form.addRow("Manufacturer", self.manufacturer)
        form.addRow("Model", self.model)
        form.addRow("Serial number", self.serial)
        form.addRow("Location", self.location)
        form.addRow("Calibration every", self.cal_interval)
        form.addRow("Maintenance every", self.maint_interval)
        if existing is None:
            self.has_cal_due = QCheckBox("Current calibration valid until")
            self.cal_due = date_edit(date.today() + timedelta(days=180))
            self.has_maint_due = QCheckBox("Current maintenance valid until")
            self.maint_due = date_edit(date.today() + timedelta(days=180))
            for check, edit in ((self.has_cal_due, self.cal_due), (self.has_maint_due, self.maint_due)):
                edit.setEnabled(False)
                check.toggled.connect(edit.setEnabled)
                row = QHBoxLayout()
                row.addWidget(check)
                row.addWidget(edit)
                row.addStretch()
                form.addRow("", row)
            form.addRow("", muted_label("Enter the dates on the instrument's current calibration / maintenance "
                                        "labels. Without them the instrument cannot be used until one is recorded."))
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check(self) -> None:
        if not self.name.text().strip() or not self.code.text().strip():
            show_label_message(self.error, "Instrument ID and name are required.")
            return
        self.accept()

    def details(self) -> InstrumentDetails:
        return InstrumentDetails(
            name=self.name.text(), category=self.category.currentText(), manufacturer=self.manufacturer.text(),
            model=self.model.text(), serial_number=self.serial.text(), location=self.location.text(),
            calibration_interval_days=self.cal_interval.value() or None,
            maintenance_interval_days=self.maint_interval.value() or None, notes=self.notes.toPlainText(),
        )

    def due_dates(self) -> dict:
        return {
            "calibration_due": self.cal_due.date().toPython() if self.has_cal_due.isChecked() else None,
            "maintenance_due": self.maint_due.date().toPython() if self.has_maint_due.isChecked() else None,
        }


class EventDialog(QDialog):
    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self.setWindowTitle(f"Record event – {instrument.code}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.event_type = QComboBox()
        for event in EventType:
            self.event_type.addItem(event.value, event.value)
        self.performed_on = date_edit()
        self.performed_on.setMaximumDate(QDate.currentDate())
        self.result = QComboBox()
        self.result.addItems(RESULTS)
        self.performed_by = QLineEdit()
        self.performed_by.setPlaceholderText("Person or service company")
        self.certificate = QLineEdit()
        self.certificate.setPlaceholderText("Certificate / report number")
        self.has_next = QCheckBox("Next due (otherwise calculated from the interval)")
        self.next_due = date_edit(date.today() + timedelta(days=365))
        self.next_due.setEnabled(False)
        self.has_next.toggled.connect(self.next_due.setEnabled)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(60)
        form.addRow("Event", self.event_type)
        form.addRow("Date performed", self.performed_on)
        form.addRow("Result", self.result)
        form.addRow("Performed by", self.performed_by)
        form.addRow("Certificate", self.certificate)
        form.addRow("", self.has_next)
        form.addRow("", self.next_due)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        self.hint = muted_label("")
        layout.addWidget(self.hint)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.event_type.currentIndexChanged.connect(lambda *_: self._sync())
        self.result.currentIndexChanged.connect(lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        event = EventType(self.event_type.currentData())
        check = event in CHECK_EVENTS
        self.result.setEnabled(check)
        dated = event in (EventType.CALIBRATION, EventType.MAINTENANCE)
        self.has_next.setEnabled(dated)
        self.next_due.setEnabled(dated and self.has_next.isChecked())
        if check and self.result.currentText() == "Fail":
            self.hint.setText("A failed result takes the instrument out of service.")
        elif event in (EventType.OUT_OF_SERVICE, EventType.RETURNED, EventType.RETIRED, EventType.REPAIR):
            self.hint.setText("Describe the reason in the notes.")
        else:
            self.hint.setText("")

    def values(self) -> dict:
        event = EventType(self.event_type.currentData())
        return {
            "event_type": event,
            "performed_on": self.performed_on.date().toPython(),
            "result": self.result.currentText() if event in CHECK_EVENTS else None,
            "performed_by": self.performed_by.text(),
            "certificate_ref": self.certificate.text(),
            "next_due": self.next_due.date().toPython() if self.has_next.isChecked() and self.has_next.isEnabled()
            else None,
            "notes": self.notes.toPlainText(),
        }


class InstrumentsView(QWidget):
    title = "Instruments"
    COLUMNS = ["ID", "Name", "Category", "Location", "Status", "Calibration due", "Maintenance due", "Usable"]
    HISTORY_COLUMNS = ["Date", "Event", "Result", "Performed by", "Certificate", "Next due", "Notes", "Recorded by"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows = []
        can_manage = user.can(Permission.MANAGE_MASTER_DATA)
        can_record = can_manage or user.can(Permission.EDIT_RECORDS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "An instrument can be recorded on a test only while it is in service and its calibration and maintenance "
            "are not overdue. Failed calibrations or checks take it out of service automatically."
        ))
        toolbar = QHBoxLayout()
        self.new_button = primary_button("New instrument")
        self.new_button.setDefault(False)
        self.edit_button = QPushButton("Edit")
        self.event_button = QPushButton("Record calibration / event…")
        self.new_button.setVisible(can_manage)
        self.edit_button.setVisible(can_manage)
        self.event_button.setVisible(can_record)
        self.show_retired = QCheckBox("Show retired")
        for b in (self.new_button, self.edit_button, self.event_button):
            toolbar.addWidget(b)
        toolbar.addStretch()
        toolbar.addWidget(self.show_retired)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)
        self.table = _table(self.COLUMNS, 1)
        splitter.addWidget(self.table)
        history_box = QWidget()
        history_layout = QVBoxLayout(history_box)
        history_layout.setContentsMargins(0, 8, 0, 0)
        self.history_title = QLabel("<b>History</b>")
        history_layout.addWidget(self.history_title)
        self.history = _table(self.HISTORY_COLUMNS, 6)
        history_layout.addWidget(self.history)
        splitter.addWidget(history_box)
        splitter.setSizes([380, 260])
        layout.addWidget(splitter, 1)

        self.new_button.clicked.connect(lambda *_: self._new())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.event_button.clicked.connect(lambda *_: self._event())
        self.show_retired.toggled.connect(lambda *_: self.refresh())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda *_: self._event() if can_record else None)
        self._selection_changed()

    def refresh(self) -> None:
        selected = self._selected()
        with self.db.session() as session:
            self.rows = [(i, instruments.problems(i)) for i in
                         instruments.list_instruments(session, include_retired=self.show_retired.isChecked())]
        today = date.today()
        soon = today + timedelta(days=14)
        self.table.setRowCount(len(self.rows))
        for r, (inst, problems) in enumerate(self.rows):
            values = [inst.code, inst.name, inst.category, inst.location or "", inst.status,
                      inst.calibration_due.isoformat() if inst.calibration_due else "—",
                      inst.maintenance_due.isoformat() if inst.maintenance_due else "—",
                      "No – " + "; ".join(p.replace(f"{inst.code} ", "") for p in problems) if problems else "Yes"]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                due = inst.calibration_due if c == 5 else inst.maintenance_due if c == 6 else None
                if due and due < today:
                    item.setForeground(QColor(RED))
                elif due and due <= soon:
                    item.setForeground(QColor(AMBER))
                if c == 7:
                    item.setForeground(QColor(RED if problems else GREEN))
                if c == 4 and inst.status != instruments.InstrumentStatus.IN_SERVICE:
                    item.setForeground(QColor(RED if inst.status == "Out of service" else GREY))
                self.table.setItem(r, c, item)
        if selected:
            for r, (inst, _) in enumerate(self.rows):
                if inst.id == selected.id:
                    self.table.selectRow(r)
        self._selection_changed()

    def _selected(self):
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()][0] if idx and idx[0].row() < len(self.rows) else None

    def _selection_changed(self) -> None:
        inst = self._selected()
        self.edit_button.setEnabled(inst is not None)
        self.event_button.setEnabled(inst is not None and inst.status != instruments.InstrumentStatus.RETIRED)
        if inst is None:
            self.history_title.setText("<b>History</b> – select an instrument")
            self.history.setRowCount(0)
            return
        with self.db.session() as session:
            events = instruments.history(session, inst.id)
        details = " · ".join(x for x in (inst.manufacturer, inst.model,
                                         f"S/N {inst.serial_number}" if inst.serial_number else None) if x)
        self.history_title.setText(f"<b>History – {inst.code}</b>  {details}")
        self.history.setRowCount(len(events))
        for r, e in enumerate(events):
            values = [e.performed_on.isoformat(), e.event_type, e.result or "", e.performed_by or "",
                      e.certificate_ref or "", e.next_due.isoformat() if e.next_due else "", e.notes or "",
                      e.recorded_by]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 2 and value:
                    item.setForeground(QColor(RED if value == "Fail" else GREEN))
                self.history.setItem(r, c, item)

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    def _new(self) -> None:
        dialog = InstrumentDialog(self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: instruments.create_instrument(s, self.user, dialog.code.text(), dialog.details(),
                                                                 **dialog.due_dates())):
                return

    def _edit(self) -> None:
        inst = self._selected()
        if inst is None:
            return
        dialog = InstrumentDialog(self, inst)
        # If saving fails (or the reason is cancelled) the window comes back with what was typed.
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit instrument", f"Changing {inst.code}.")
            if reason is not None and self._run(
                    lambda s: instruments.update_instrument(s, self.user, inst.id, dialog.details(), reason)):
                return

    def _event(self) -> None:
        inst = self._selected()
        if inst is None:
            return
        dialog = EventDialog(inst, self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: instruments.record_event(s, self.user, inst.id, **dialog.values())):
                return
