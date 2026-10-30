"""The intake form designer: add, arrange and set up the fields of the Sample Intaking form."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import intake
from aurum_lims.services.intake import BUILTINS, CUSTOM_TYPES, FieldSpec, FieldType
from aurum_lims.ui.common import ask_reason, error_label, format_local, muted_label, show_label_message
from aurum_lims.ui.intake_form import IntakeFormWidget
from aurum_lims.ui.scan_settings import ScanSettingsDialog


class FormDesignerDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, sample_types, locations, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.sample_types, self.locations = sample_types, locations
        with db.session() as session:
            row, fields = intake.current_form(session)
            self.history = intake.history(session)
            self.scan = intake.current_scan(session)
        self.fields: list[FieldSpec] = fields
        self.original = [replace(f) for f in fields]
        self._loading = False
        self.setWindowTitle("Design the Sample Intaking form")
        self.resize(1240, 800)
        layout = QVBoxLayout(self)
        version = (f"Current form: version {row.version}, saved {format_local(row.created_at)}" if row else
                   "Current form: the built-in starting form (not saved yet)")
        layout.addWidget(muted_label(
            f"{version}. Built-in fields fill in the sample record (sample type, material, lot, quantity, dates, "
            "receipt); custom fields capture anything else and are kept with the sample. Saving creates a new "
            "version; samples already logged keep exactly what was entered on the form they were submitted with."))

        splitter = QSplitter()
        # ----- left: field list -----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Fields, in order</b>"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda *_: self._select())
        left_layout.addWidget(self.list, 1)
        add_row = QHBoxLayout()
        self.builtin_button = QToolButton()
        self.builtin_button.setText("Add built-in field ▾")
        self.builtin_button.setPopupMode(QToolButton.InstantPopup)
        self.builtin_menu = QMenu(self)
        self.builtin_menu.aboutToShow.connect(self._fill_builtin_menu)
        self.builtin_button.setMenu(self.builtin_menu)
        custom = QPushButton("Add custom field")
        custom.clicked.connect(lambda *_: self._add(FieldSpec("", "New field", FieldType.TEXT.value)))
        section = QPushButton("Add section heading")
        section.clicked.connect(lambda *_: self._add(FieldSpec("", "New section", FieldType.HEADING.value)))
        for widget in (self.builtin_button, custom, section):
            add_row.addWidget(widget)
        left_layout.addLayout(add_row)
        move_row = QHBoxLayout()
        for text, handler in (("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                              ("Remove", self._remove)):
            button = QPushButton(text)
            button.clicked.connect(lambda *_, h=handler: h())
            move_row.addWidget(button)
        left_layout.addLayout(move_row)
        reset = QPushButton("Start again from the built-in form")
        reset.clicked.connect(lambda *_: self._reset())
        self.scan_button = QPushButton()
        self.scan_button.clicked.connect(lambda *_: self._scan_settings())
        left_layout.addWidget(self.scan_button)
        left_layout.addWidget(reset)
        self._scan_label()
        splitter.addWidget(left)

        # ----- middle: properties of the selected field -----
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(8, 0, 8, 0)
        middle_layout.addWidget(QLabel("<b>Selected field</b>"))
        props = QFormLayout()
        self.kind_note = muted_label("")
        self.label_edit = QLineEdit()
        self.type_combo = QComboBox()
        for t in CUSTOM_TYPES:
            self.type_combo.addItem(t.label, t.value)
        self.required = QCheckBox("Must be filled in")
        self.help_edit = QLineEdit()
        self.help_edit.setPlaceholderText("Shown when pointing at the field")
        self.choices_edit = QPlainTextEdit()
        self.choices_edit.setPlaceholderText("One choice per line")
        self.choices_edit.setFixedHeight(110)
        self.default_edit = QLineEdit()
        self.default_edit.setPlaceholderText("Filled in on a new form (optional)")
        props.addRow("", self.kind_note)
        props.addRow("Label", self.label_edit)
        props.addRow("Type", self.type_combo)
        props.addRow("", self.required)
        props.addRow("Help text", self.help_edit)
        self.choices_label = QLabel("Choices")
        props.addRow(self.choices_label, self.choices_edit)
        props.addRow("Default value", self.default_edit)
        middle_layout.addLayout(props)
        middle_layout.addStretch()
        if self.history:
            middle_layout.addWidget(QLabel("<b>Earlier versions</b>"))
            middle_layout.addWidget(muted_label("\n".join(
                f"v{h.version} · {format_local(h.created_at)} · {h.created_by}: {h.change_note}"
                for h in self.history[:8])))
        splitter.addWidget(middle)

        # ----- right: live preview -----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("<b>Preview</b> – exactly how the form will look"))
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        right_layout.addWidget(self.preview_scroll, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 380, 540])
        layout.addWidget(splitter, 1)

        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save as new version")
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for signal in (self.label_edit.textEdited, self.help_edit.textEdited, self.default_edit.textEdited):
            signal.connect(lambda *_: self._apply())
        self.choices_edit.textChanged.connect(lambda: self._apply())
        self.required.toggled.connect(lambda *_: self._apply())
        self.type_combo.currentIndexChanged.connect(lambda *_: self._apply())
        self._refresh_list(0)

    # ----- list -----
    def _item_text(self, f: FieldSpec) -> str:
        kind = FieldType(f.type)
        if kind is FieldType.HEADING:
            return f"━━ {f.label}"
        tag = "built-in" if f.builtin else kind.label.lower()
        return f"    {f.label}{' *' if f.required else ''}   [{tag}]"

    def _refresh_list(self, select: int | None = None) -> None:
        self._loading = True
        current = self.list.currentRow() if select is None else select
        self.list.clear()
        for f in self.fields:
            item = QListWidgetItem(self._item_text(f))
            if FieldType(f.type) is FieldType.HEADING:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
        self._loading = False
        self.list.setCurrentRow(min(max(current, 0), len(self.fields) - 1))
        self._select()
        self._preview()

    def _fill_builtin_menu(self) -> None:
        self.builtin_menu.clear()
        used = {f.key for f in self.fields}
        for key, info in BUILTINS.items():
            if key not in used:
                action = self.builtin_menu.addAction(info.label)
                action.triggered.connect(lambda *_, k=key: self._add(intake.builtin_field(k)))
        if self.builtin_menu.isEmpty():
            self.builtin_menu.addAction("(all built-in fields are on the form)").setEnabled(False)

    def _add(self, f: FieldSpec) -> None:
        if not f.builtin:
            taken = {x.key for x in self.fields}
            prefix = "h_" if f.type == FieldType.HEADING else ""
            f.key = intake.make_key(f.label, taken) if not prefix else intake.make_key(f.label, taken).replace(
                "c_", "h_", 1)
        at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else len(self.fields)
        self.fields.insert(at, f)
        self._refresh_list(at)
        self.label_edit.setFocus()
        self.label_edit.selectAll()

    def _move(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < len(self.fields):
            return
        self.fields[row], self.fields[target] = self.fields[target], self.fields[row]
        self._refresh_list(target)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        if self.fields[row].key == "sample_type":
            show_label_message(self.error, "The sample type field must stay on the form.")
            return
        del self.fields[row]
        self._refresh_list(row)

    def _reset(self) -> None:
        if QMessageBox.question(self, "Start again", "Replace the design with the built-in starting form? Nothing "
                                "is saved until you choose Save as new version.") == QMessageBox.Yes:
            self.fields = [replace(f) for f in intake.DEFAULT_FORM]
            self._refresh_list(0)

    # ----- properties -----
    def _current(self) -> FieldSpec | None:
        row = self.list.currentRow()
        return self.fields[row] if 0 <= row < len(self.fields) else None

    def _select(self) -> None:
        f = self._current()
        self._loading = True
        enabled = f is not None
        for widget in (self.label_edit, self.type_combo, self.required, self.help_edit, self.choices_edit,
                       self.default_edit):
            widget.setEnabled(enabled)
        if f is None:
            self._loading = False
            return
        kind = FieldType(f.type)
        heading = kind is FieldType.HEADING
        self.kind_note.setText(
            "Section heading – organises the form, holds no value." if heading else
            f"Built-in field: fills in the sample's {BUILTINS[f.key].label.lower()}." if f.builtin else
            "Custom field: kept with the sample's intake record.")
        self.label_edit.setText(f.label)
        self.type_combo.setCurrentIndex(max(self.type_combo.findData(f.type), 0))
        self.type_combo.setEnabled(not f.builtin and not heading)
        self.required.setChecked(f.required)
        self.required.setEnabled(not heading and f.key != "sample_type")
        self.help_edit.setText(f.help)
        self.help_edit.setEnabled(not heading)
        dropdown = kind is FieldType.DROPDOWN
        system_choices = f.builtin and f.key in ("sample_type", "location_id")
        self.choices_edit.setPlainText("\n".join(f.choices))
        self.choices_edit.setEnabled(dropdown and not f.builtin)
        self.choices_label.setText("Choices" if not system_choices else "Choices (from the system)")
        self.default_edit.setText(f.default)
        self.default_edit.setEnabled(not heading and not system_choices and kind not in (FieldType.DATE,
                                                                                        FieldType.DATETIME))
        self._loading = False

    def _apply(self) -> None:
        if self._loading:
            return
        f = self._current()
        if f is None:
            return
        f.label = self.label_edit.text()
        if not f.builtin and f.type != FieldType.HEADING:
            f.type = self.type_combo.currentData()
        f.required = self.required.isChecked() or f.key == "sample_type"
        f.help = self.help_edit.text()
        if not f.builtin:
            f.choices = [c for c in self.choices_edit.toPlainText().splitlines() if c.strip()]
        f.default = self.default_edit.text()
        row = self.list.currentRow()
        self.list.item(row).setText(self._item_text(f))
        self.choices_edit.setEnabled(f.type == FieldType.DROPDOWN and not f.builtin)
        self._preview()

    def _preview(self) -> None:
        holder = QWidget()
        holder.setObjectName("Content")
        inner = QVBoxLayout(holder)
        inner.addWidget(IntakeFormWidget(self.fields, self.sample_types, self.locations))
        inner.addStretch()
        self.preview_scroll.setWidget(holder)

    # ----- barcode scanning -----
    def _scan_label(self) -> None:
        self.scan_button.setText("Barcode scanning… (" + ("on" if self.scan.enabled else "off") + ")")

    def _scan_settings(self) -> None:
        dialog = ScanSettingsDialog(self, self.fields, self.scan)
        if dialog.exec() == QDialog.Accepted:
            self.scan = dialog.settings()
            self._scan_label()

    # ----- save -----
    def _save(self) -> None:
        try:
            fields = intake.validate_form([replace(f) for f in self.fields])
            intake.validate_scan(self.scan, fields)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        note = ask_reason(self, "Save intake form", "Describe what changed in the form (kept with the version).")
        if note is None:
            return
        try:
            with self.db.session() as session:
                intake.save_form(session, self.user, fields, note, self.scan)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()

