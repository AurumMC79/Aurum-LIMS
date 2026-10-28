"""Dialogs for registering and handling samples."""

from __future__ import annotations

import json

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from PySide6.QtGui import QColor

from aurum_lims.db.models import Sample, SampleType, Specification, User
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import esign, sample_ids, samples, sample_types, storage, testing
from aurum_lims.services.sample_types import CATEGORY_FIELDS, FIELD_LABELS, Category
from aurum_lims.services.samples import (
    DISPOSAL_METHODS,
    PRIORITIES,
    RECEIPT_CONDITIONS,
    Receipt,
    SampleDetails,
)
from aurum_lims.ui.common import (
    Card,
    ask_reason,
    edit_to_utc,
    error_label,
    fill_location_combo,
    format_local,
    muted_label,
    now_edit,
    password_field,
    show_label_message,
)

UNITS = ["g", "mg", "kg", "mL", "L", "µL", "units", "tablets", "capsules", "vials", "bottles", "swabs", "plates"]


def _buttons(dialog: QDialog, ok_text: str, on_ok) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = buttons.button(QDialogButtonBox.Ok)
    ok.setText(ok_text)
    ok.setProperty("primary", True)
    buttons.accepted.connect(on_ok)
    buttons.rejected.connect(dialog.reject)
    return buttons


def _text(widget) -> str | None:
    value = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
    return value.strip() or None


def _notes_box(height: int = 60) -> QPlainTextEdit:
    box = QPlainTextEdit()
    box.setFixedHeight(height)
    return box


def _location_choices(db: Database) -> list[tuple[int, str]]:
    with db.session() as session:
        return storage.active_choices(session)


class QuantityRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("e.g. 250")
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems([""] + UNITS)
        layout.addWidget(self.amount, 2)
        layout.addWidget(self.unit, 1)

    def values(self) -> tuple[str | None, str | None]:
        return _text(self.amount), self.unit.currentText().strip() or None

    def set_values(self, amount: str | None, unit: str | None) -> None:
        self.amount.setText(amount or "")
        self.unit.setCurrentText(unit or "")


class SampleFieldsForm(QWidget):
    """The descriptive fields of a sample; the top section changes with the category."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.category: Category | None = None
        self.category_edits: dict[str, QLineEdit] = {}
        self._kept: dict[str, str] = {}

        # One form for everything so all fields line up; the category's rows sit at the top.
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.form = form
        self.quantity = QuantityRow()
        self.container = QLineEdit()
        self.container.setPlaceholderText("e.g. 250 mL amber glass bottle")
        self.priority = QComboBox()
        self.priority.addItems(PRIORITIES)
        self.has_due = QCheckBox("Due date")
        self.due = QDateEdit(QDate.currentDate().addDays(7))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("yyyy-MM-dd")
        self.due.setEnabled(False)
        self.has_due.toggled.connect(self.due.setEnabled)
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_due)
        due_row.addWidget(self.due)
        due_row.addStretch()
        self.has_sampled = QCheckBox("Sampled at")
        self.sampled_at = now_edit()
        self.sampled_at.setEnabled(False)
        self.has_sampled.toggled.connect(self.sampled_at.setEnabled)
        sampled_row = QHBoxLayout()
        sampled_row.addWidget(self.has_sampled)
        sampled_row.addWidget(self.sampled_at)
        sampled_row.addStretch()
        self.sampled_by = QLineEdit()
        self.sampled_by.setPlaceholderText("Name of the person who took the sample")
        self.notes = _notes_box()
        form.addRow("Quantity", self.quantity)
        form.addRow("Container", self.container)
        form.addRow("Priority", self.priority)
        form.addRow("", due_row)
        form.addRow("", sampled_row)
        form.addRow("Sampled by", self.sampled_by)
        form.addRow("Notes", self.notes)

    def set_category(self, category: Category) -> None:
        for name, edit in self.category_edits.items():
            self._kept[name] = edit.text()
        for _ in range(len(self.category_edits)):
            self.form.removeRow(0)
        self.category_edits = {}
        self.category = category
        for row, (name, required) in enumerate(CATEGORY_FIELDS[category]):
            edit = QLineEdit(self._kept.get(name, ""))
            self.category_edits[name] = edit
            self.form.insertRow(row, FIELD_LABELS[name] + (" *" if required else ""), edit)

    def details(self) -> SampleDetails:
        amount, unit = self.quantity.values()
        data = {name: _text(edit) for name, edit in self.category_edits.items()}
        return SampleDetails(
            **data,
            quantity=amount,
            quantity_unit=unit,
            container=_text(self.container),
            priority=self.priority.currentText(),
            due_date=self.due.date().toPython() if self.has_due.isChecked() else None,
            sampled_at=edit_to_utc(self.sampled_at) if self.has_sampled.isChecked() else None,
            sampled_by=_text(self.sampled_by),
            notes=_text(self.notes),
        )

    def load(self, details: SampleDetails) -> None:
        for name, edit in self.category_edits.items():
            edit.setText(getattr(details, name) or "")
        # Keep values of fields the category does not show, so they are not wiped on save.
        self._hidden = {
            name: getattr(details, name) for name in FIELD_LABELS if name not in self.category_edits
        }
        self.quantity.set_values(details.quantity, details.quantity_unit)
        self.container.setText(details.container or "")
        self.priority.setCurrentText(details.priority or "Normal")
        self.has_due.setChecked(details.due_date is not None)
        if details.due_date:
            self.due.setDate(QDate(details.due_date))
        self.has_sampled.setChecked(details.sampled_at is not None)
        if details.sampled_at:
            self.sampled_at.setDateTime(details.sampled_at.astimezone().replace(tzinfo=None))
        self.sampled_by.setText(details.sampled_by or "")
        self.notes.setPlainText(details.notes or "")

    def details_keeping_hidden(self) -> SampleDetails:
        details = self.details()
        for name, value in getattr(self, "_hidden", {}).items():
            setattr(details, name, value)
        return details


class ReceiptFields(QWidget):
    """Received at / storage location / condition / temperature / notes."""

    def __init__(self, locations: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.received_at = now_edit()
        self.location = QComboBox()
        fill_location_combo(self.location, locations)
        self.condition = QComboBox()
        self.condition.addItems(RECEIPT_CONDITIONS)
        self.temperature = QLineEdit()
        self.temperature.setPlaceholderText("e.g. 4 °C or ambient")
        self.notes = _notes_box(50)
        form.addRow("Received at", self.received_at)
        form.addRow("Store in *", self.location)
        form.addRow("Condition", self.condition)
        form.addRow("Temperature", self.temperature)
        form.addRow("Receipt notes", self.notes)

    def receipt(self) -> Receipt:
        return Receipt(
            location_id=self.location.currentData(),
            received_at=edit_to_utc(self.received_at),
            condition=self.condition.currentText(),
            temperature=_text(self.temperature),
            notes=_text(self.notes),
        )


class RegisterSampleDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.created: list[str] = []
        self.setWindowTitle("Register samples")
        self.setMinimumWidth(560)

        with db.session() as session:
            self.types = [(t.id, t.name, t.code, Category(t.category)) for t in sample_types.list_types(session)]
        locations = _location_choices(db)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("Content")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        top = QFormLayout()
        self.type_combo = QComboBox()
        for type_id, name, code, _ in self.types:
            self.type_combo.addItem(f"{name} ({code})", type_id)
        self.count = QSpinBox()
        self.count.setRange(1, samples.MAX_BATCH)
        self.count.setToolTip("Register several identical samples (e.g. containers of one lot) in one go.")
        self.count.setFixedWidth(80)
        self.next_id = muted_label("")
        top.addRow("Sample type *", self.type_combo)
        top.addRow("Number of samples", self.count)
        top.addRow("Next ID", self.next_id)
        layout.addLayout(top)

        self.fields = SampleFieldsForm()
        details_box = QGroupBox("Sample details")
        QVBoxLayout(details_box).addWidget(self.fields)
        layout.addWidget(details_box)

        self.receive_now = QGroupBox("Received into the lab now")
        self.receive_now.setCheckable(True)
        self.receive_now.setChecked(True)
        self.receipt = ReceiptFields(locations)
        QVBoxLayout(self.receive_now).addWidget(self.receipt)
        layout.addWidget(self.receive_now)
        if not locations:
            self.receive_now.setChecked(False)
            self.receive_now.setEnabled(False)
            self.receive_now.setTitle("Received into the lab now (add a storage location first)")

        self.error = error_label()
        outer.addWidget(self.error)
        outer.addWidget(_buttons(self, "Register", self._submit))
        screen = self.screen().availableGeometry() if self.screen() else None
        self.resize(620, min(900, screen.height() - 80) if screen else 760)

        self.type_combo.currentIndexChanged.connect(lambda *_: self._type_changed())
        if not self.types:
            show_label_message(self.error, "No active sample types. Ask an administrator to add one.")
        else:
            self._type_changed()

    def _type_changed(self) -> None:
        index = self.type_combo.currentIndex()
        if index < 0:
            return
        type_id, _, _, category = self.types[index]
        self.fields.set_category(category)
        with self.db.session() as session:
            self.next_id.setText(sample_ids.peek_next_id(session, session.get(SampleType, type_id)))

    def _submit(self) -> None:
        if not self.types:
            return
        receipt = self.receipt.receipt() if self.receive_now.isChecked() else None
        try:
            with self.db.session() as session:
                created = samples.register_samples(
                    session, self.user, self.type_combo.currentData(), self.fields.details(),
                    count=self.count.value(), receipt=receipt,
                )
                self.created = [s.sample_id for s in created]
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class ReceiveDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_pk: int, sample_id: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        self.setWindowTitle(f"Receive {sample_id}")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        self.fields = ReceiptFields(_location_choices(db))
        layout.addWidget(self.fields)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Receive", self._submit))

    def _submit(self) -> None:
        try:
            with self.db.session() as session:
                samples.receive_sample(session, self.user, self.sample_pk, self.fields.receipt())
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class TransferDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_pk: int, sample_id: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        self.setWindowTitle(f"Transfer {sample_id}")
        self.setMinimumWidth(460)
        with db.session() as session:
            people = [(u.id, f"{u.full_name} ({u.username})") for u in
                      session.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)]

        layout = QVBoxLayout(self)
        self.to_location = QRadioButton("To a storage location")
        self.to_location.setChecked(True)
        self.location = QComboBox()
        fill_location_combo(self.location, _location_choices(db))
        self.to_person = QRadioButton("To a person (e.g. an analyst taking it for testing)")
        self.person = QComboBox()
        for user_id, label in people:
            self.person.addItem(label, user_id)
        self.person.setEnabled(False)
        self.to_location.toggled.connect(self.location.setEnabled)
        self.to_person.toggled.connect(self.person.setEnabled)
        layout.addWidget(self.to_location)
        layout.addWidget(self.location)
        layout.addWidget(self.to_person)
        layout.addWidget(self.person)
        form = QFormLayout()
        self.when = now_edit()
        self.notes = _notes_box(50)
        form.addRow("Transferred at", self.when)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Transfer", self._submit))

    def _submit(self) -> None:
        kwargs = (
            {"to_location_id": self.location.currentData()} if self.to_location.isChecked()
            else {"to_holder_id": self.person.currentData()}
        )
        if self.to_location.isChecked() and kwargs["to_location_id"] is None:
            show_label_message(self.error, "Choose a storage location.")
            return
        try:
            with self.db.session() as session:
                samples.transfer_sample(session, self.user, self.sample_pk, occurred_at=edit_to_utc(self.when),
                                        notes=_text(self.notes), **kwargs)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class AliquotDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_pk: int, sample_id: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        self.created: list[str] = []
        self.setWindowTitle(f"Take aliquots of {sample_id}")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(f"Aliquots are named {sample_id}-A1, {sample_id}-A2, … and keep the parent's details."))
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, samples.MAX_ALIQUOTS)
        self.count.setValue(2)
        self.count.setFixedWidth(80)
        self.quantity = QuantityRow()
        self.location = QComboBox()
        fill_location_combo(self.location, _location_choices(db))
        self.notes = _notes_box(50)
        form.addRow("Number of aliquots", self.count)
        form.addRow("Quantity each", self.quantity)
        form.addRow("Store in *", self.location)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Create aliquots", self._submit))

    def _submit(self) -> None:
        amount, unit = self.quantity.values()
        try:
            with self.db.session() as session:
                kids = samples.create_aliquots(
                    session, self.user, self.sample_pk, count=self.count.value(),
                    location_id=self.location.currentData(), quantity=amount, quantity_unit=unit,
                    notes=_text(self.notes),
                )
                self.created = [k.sample_id for k in kids]
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class DisposeDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_pk: int, sample_id: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk, self.sample_id = db, user, sample_pk, sample_id
        self.setWindowTitle(f"Dispose of {sample_id}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.method = QComboBox()
        self.method.addItems(DISPOSAL_METHODS)
        self.when = now_edit()
        self.notes = _notes_box(50)
        form.addRow("Method", self.method)
        form.addRow("Disposed at", self.when)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)

        sign = QGroupBox("Electronic signature")
        sign_layout = QVBoxLayout(sign)
        sign_layout.addWidget(muted_label(
            f"By entering your password you electronically sign this record as:\n"
            f"“{esign.SignatureMeaning.PERFORMED}” {user.full_name} — disposal of {sample_id}."
        ))
        self.password = password_field("Your password")
        sign_layout.addWidget(self.password)
        layout.addWidget(sign)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Sign and dispose", self._submit))

    def _submit(self) -> None:
        try:
            with self.db.session() as session:
                result = samples.dispose_sample(
                    session, self.user, self.sample_pk, method=self.method.currentText(),
                    password=self.password.text(), occurred_at=edit_to_utc(self.when), notes=_text(self.notes),
                )
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.password.clear()
        if not result.ok:
            show_label_message(self.error, result.message)
            return
        self.accept()


class EditSampleDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_pk: int, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        with db.session() as session:
            sample = session.get(Sample, sample_pk)
            category = Category(session.get(SampleType, sample.sample_type_id).category)
            details = samples.details_of(sample)
            self.sample_id = sample.sample_id
        self.setWindowTitle(f"Correct details of {self.sample_id}")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label("Corrections are recorded in the audit trail with the old and new values."))
        self.fields = SampleFieldsForm()
        self.fields.set_category(category)
        self.fields.load(details)
        layout.addWidget(self.fields)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addWidget(_buttons(self, "Save", self._submit))

    def _submit(self) -> None:
        reason = ask_reason(self, "Correct sample details", f"Correcting {self.sample_id}.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                samples.update_details(session, self.user, self.sample_pk, self.fields.details_keeping_hidden(), reason)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class SampleDetailDialog(QDialog):
    """Read-only view of a sample with its full chain of custody."""

    CUSTODY_COLUMNS = ["Occurred", "Recorded", "Event", "By", "From", "To", "Details / notes"]

    def __init__(self, db: Database, user: SessionUser, sample_pk: int, parent=None):
        super().__init__(parent)
        self.setMinimumSize(900, 620)
        with db.session() as session:
            sample = samples.get_sample(session, user, sample_pk)
            sample_type = session.get(SampleType, sample.sample_type_id)
            location_paths = storage.paths(session)
            names = {u.id: u.full_name for u in session.query(User)}
            history = samples.custody_history(session, user, sample_pk)
            signatures = esign.signatures_for(session, "sample", sample.sample_id)
            parent_id = session.get(Sample, sample.parent_id).sample_id if sample.parent_id else None
            children = [c.sample_id for c in session.query(Sample).filter(Sample.parent_id == sample.id).order_by(Sample.id)]
            spec = session.get(Specification, sample.specification_id) if sample.specification_id else None
            spec_label = f"{spec.code} v{spec.version} – {spec.name}" if spec else None
            tests = [
                (a, testing.reportable_results(session, a.id)) for a in testing.analyses_for_sample(session, user, sample.id)
            ]
        self.setWindowTitle(f"Sample {sample.sample_id}")

        def where(location_id, holder_id):
            if location_id:
                return location_paths.get(location_id, "?")
            if holder_id:
                return f"With {names.get(holder_id, '?')}"
            return ""

        layout = QVBoxLayout(self)
        title = QLabel(f"{sample.sample_id}  ·  {sample_type.name}  ·  {sample.status}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        info = Card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        rows = [(FIELD_LABELS[name], getattr(sample, name)) for name in FIELD_LABELS if getattr(sample, name)]
        rows += [
            ("Quantity", f"{sample.quantity} {sample.quantity_unit}" if sample.quantity else None),
            ("Container", sample.container),
            ("Priority", sample.priority),
            ("Due date", sample.due_date.isoformat() if sample.due_date else None),
            ("Sampled", " by ".join(x for x in (format_local(sample.sampled_at) if sample.sampled_at else None, sample.sampled_by) if x) or None),
            ("Registered", f"{format_local(sample.created_at)} by {names.get(sample.created_by_id, '?')}"),
            ("Received", f"{format_local(sample.received_at)} by {names.get(sample.received_by_id, '?')}" if sample.received_at else None),
            ("Condition on receipt", " · ".join(x for x in (sample.receipt_condition, sample.receipt_temperature) if x) or None),
            ("Current location", where(sample.location_id, sample.holder_id) or None),
            ("Disposed", f"{format_local(sample.disposed_at)} by {names.get(sample.disposed_by_id, '?')} ({sample.disposal_method})" if sample.disposed_at else None),
            ("Aliquot of", parent_id),
            ("Aliquots", ", ".join(children) or None),
            ("Specification", spec_label),
            ("Notes", sample.notes),
        ]
        rows = [(label, value) for label, value in rows if value]
        for i, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setObjectName("Muted")
            val = QLabel(str(value))
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(key, i // 2, (i % 2) * 2)
            grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
        info.body.addLayout(grid)
        layout.addWidget(info)

        layout.addWidget(QLabel("<b>Chain of custody</b>"))
        table = QTableWidget(len(history), len(self.CUSTODY_COLUMNS))
        table.setHorizontalHeaderLabels(self.CUSTODY_COLUMNS)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        for r, event in enumerate(history):
            extra = json.loads(event.details) if event.details else {}
            detail_text = "; ".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in extra.items() if v)
            if event.notes:
                detail_text = f"{detail_text} — {event.notes}" if detail_text else event.notes
            values = [
                format_local(event.occurred_at), format_local(event.recorded_at), event.event,
                names.get(event.user_id, event.username),
                where(event.from_location_id, event.from_holder_id), where(event.to_location_id, event.to_holder_id),
                detail_text,
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(r, c, item)
        layout.addWidget(table, 1)

        if tests:
            layout.addWidget(QLabel("<b>Tests and results</b>"))
            test_table = QTableWidget(0, 5)
            test_table.setHorizontalHeaderLabels(["Test", "Status", "Result", "Specification", "Conformance"])
            test_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            test_table.verticalHeader().hide()
            test_table.setAlternatingRowColors(True)
            test_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            test_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            for analysis, results in tests:
                lines = results or [None]
                for res in lines:
                    r = test_table.rowCount()
                    test_table.insertRow(r)
                    values = [f"{analysis.method_code} v{analysis.method_version}", analysis.status]
                    if res is None:
                        values += ["", "", ""]
                    else:
                        values += [f"{res.parameter_name}: {res.reported_value} {res.unit or ''}".strip(),
                                   res.spec_text or "", res.conformance]
                    for c, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        if c == 4 and value == "Fail":
                            item.setForeground(QColor("#b42318"))
                        elif c == 4 and value == "Pass":
                            item.setForeground(QColor("#067647"))
                        test_table.setItem(r, c, item)
            layout.addWidget(test_table, 1)

        if signatures:
            sig_text = "\n".join(
                f"{s.meaning} {s.full_name} ({s.username}) on {format_local(s.signed_at, with_seconds=True)}"
                + (f" — {s.reason}" if s.reason else "")
                for s in signatures
            )
            layout.addWidget(QLabel("<b>Electronic signatures</b>"))
            layout.addWidget(muted_label(sig_text))

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


def show_created(parent: QWidget, title: str, ids: list[str]) -> None:
    if not ids:
        return
    listing = "\n".join(ids[:30]) + (f"\n…and {len(ids) - 30} more" if len(ids) > 30 else "")
    QMessageBox.information(parent, title, f"{len(ids)} sample(s) created:\n\n{listing}")
