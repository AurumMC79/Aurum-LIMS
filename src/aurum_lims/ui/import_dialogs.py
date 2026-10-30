"""Instrument data import: setting up import templates and importing a file into a test."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import instrument_import as ii
from aurum_lims.services.instrument_import import DELIMITERS, ImportResult, RuleSpec, TakeMode, TemplateSpec
from aurum_lims.services.methods import ParameterSpec
from aurum_lims.ui.common import (
    GridTable,
    ask_reason,
    error_label,
    format_local,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)

FILE_FILTER = "Instrument exports (*.csv *.txt *.tsv *.dat *.prn);;All files (*)"


def _read_bytes(parent: QWidget, title: str) -> tuple[str, bytes] | None:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", FILE_FILTER)
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        show_error(parent, exc, "Could not read the file")
        return None
    if len(data) > ii.MAX_FILE_BYTES:
        show_error(parent, f"The file is larger than {ii.MAX_FILE_BYTES // (1024 * 1024)} MB.", "File too large")
        return None
    return Path(path).name, data


def result_text(result: ImportResult, parameters: list[ParameterSpec]) -> str:
    """Values found, then warnings and problems, as HTML for a label."""
    names = {p.key: p.name for p in parameters}
    lines = []
    for (key, rep), value in sorted(result.values.items()):
        reps = next((p.replicates for p in parameters if p.key == key), 1)
        lines.append(f"{names.get(key, key)}{f' (replicate {rep})' if reps > 1 else ''}: <b>{value}</b>")
    if not lines:
        lines.append("<i>No values found.</i>")
    lines += [f"<span style='color:#6b7785'>{n}</span>" for n in result.notes]
    lines += [f"<span style='color:#8a6d15'>⚠ {w}</span>" for w in result.warnings]
    lines += [f"<span style='color:#b42318'>✖ {e}</span>" for e in result.errors]
    return "<br/>".join(lines)


class ImportTemplatesDialog(QDialog):
    """The import templates of one method (Admin / QA)."""

    COLUMNS = ["Name", "Parameters read", "Status", "Last changed"]

    def __init__(self, db: Database, user: SessionUser, method_code: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.method_code = db, user, method_code
        self.rows = []
        self.setWindowTitle(f"Import templates – {method_code}")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(
            "An import template tells Aurum LIMS how to read an instrument's CSV / TXT export for this method: "
            "which column holds each value and which rows belong to the sample. Analysts then choose "
            "Import from file… when entering results."))
        self.table = GridTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        new = primary_button("New template")
        new.setDefault(False)
        new.clicked.connect(lambda *_: self._new())
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.active_button = QPushButton("Deactivate")
        self.active_button.clicked.connect(lambda *_: self._toggle())
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (new, self.edit_button, self.active_button):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.rows = ii.templates_for(session, self.method_code, include_inactive=True)
            names = {p.key: p.name for p in ii.importable_parameters(session, self.method_code)}
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            spec = TemplateSpec.from_row(row)
            values = [row.name, ", ".join(names.get(rule.parameter_key, rule.parameter_key) for rule in spec.rules),
                      "Active" if row.is_active else "Inactive", format_local(row.updated_at or row.created_at)]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not row.is_active:
                    item.setForeground(QColor("#8b98a8"))
                self.table.setItem(r, c, item)
        self._update_buttons()

    def _selected(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) and self.table.selectedItems() else None

    def _update_buttons(self) -> None:
        row = self._selected()
        self.edit_button.setEnabled(row is not None)
        self.active_button.setEnabled(row is not None)
        self.active_button.setText("Activate" if row is not None and not row.is_active else "Deactivate")

    def _new(self) -> None:
        if TemplateEditorDialog(self.db, self.user, self.method_code, parent=self).exec() == QDialog.Accepted:
            self.refresh()

    def _edit(self) -> None:
        row = self._selected()
        if row is not None and TemplateEditorDialog(self.db, self.user, self.method_code, row.id,
                                                    TemplateSpec.from_row(row), self).exec() == QDialog.Accepted:
            self.refresh()

    def _toggle(self) -> None:
        row = self._selected()
        if row is None:
            return
        activate = not row.is_active
        reason = ask_reason(self, "Import template", f"{'Activate' if activate else 'Deactivate'} '{row.name}'.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                ii.set_active(session, self.user, row.id, activate, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()


class TemplateEditorDialog(QDialog):
    RULE_COLUMNS = ["Parameter", "Value column", "Use rows where column…", "…contains", "How to use the rows"]

    def __init__(self, db: Database, user: SessionUser, method_code: str, template_id: int | None = None,
                 spec: TemplateSpec | None = None, parent=None):
        super().__init__(parent)
        self.db, self.user, self.method_code, self.template_id = db, user, method_code, template_id
        self.example: bytes | None = None
        self.headers: list[str] = []
        with db.session() as session:
            self.parameters = ii.importable_parameters(session, method_code)
        spec = spec or TemplateSpec("", method_code)
        self.setWindowTitle(f"{'Edit' if template_id else 'New'} import template – {method_code}")
        self.resize(1180, 760)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit(spec.name)
        self.name.setPlaceholderText("e.g. Empower assay export, Balance printout")
        self.header_row = QSpinBox()
        self.header_row.setRange(0, 100)
        self.header_row.setValue(spec.header_row)
        self.header_row.setSpecialValueText("No headings")
        self.header_row.setFixedWidth(130)
        self.delimiter = QComboBox()
        for value, label in DELIMITERS.items():
            self.delimiter.addItem(label, value)
        self.delimiter.setCurrentIndex(max(self.delimiter.findData(spec.delimiter), 0))
        self.decimal_comma = QCheckBox("Numbers use a decimal comma (e.g. 25,30)")
        self.decimal_comma.setChecked(spec.decimal_comma)
        form.addRow("Name *", self.name)
        form.addRow("Column headings are on line", self.header_row)
        form.addRow("Separator", self.delimiter)
        form.addRow("", self.decimal_comma)
        layout.addLayout(form)

        layout.addWidget(muted_label(
            "One rule per value to read. 'Use rows where column … contains' picks the rows: write {sample_id} for "
            "the sample being tested (e.g. Sample Name contains {sample_id}), or fixed text such as STD for the "
            "standard injections. Leave it empty to use every row."))
        self.rules = GridTable(0, len(self.RULE_COLUMNS))
        self.rules.setHorizontalHeaderLabels(self.RULE_COLUMNS)
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rules.verticalHeader().setVisible(True)
        header = self.rules.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        for column, width in enumerate([230, 180, 190, 160, 190]):
            self.rules.setColumnWidth(column, width)
        self.rules.setMinimumHeight(170)
        for rule in spec.rules or [RuleSpec("", "")]:
            self._add_rule(rule)
        layout.addWidget(self.rules)
        rule_buttons = QHBoxLayout()
        add = QPushButton("Add rule")
        add.clicked.connect(lambda *_: self._add_rule(RuleSpec("", "")))
        remove = QPushButton("Remove rule")
        remove.clicked.connect(lambda *_: self._remove_rule())
        load = QPushButton("Load example file…")
        load.clicked.connect(lambda *_: self._load_example())
        self.example_label = muted_label("No example file loaded – load one to pick columns and check the result.")
        self.preview_sample = QLineEdit()
        self.preview_sample.setPlaceholderText("Sample ID to preview, e.g. AU-2026-00001")
        self.preview_sample.setFixedWidth(260)
        self.preview_sample.textChanged.connect(lambda *_: self._preview())
        for widget in (add, remove, load):
            rule_buttons.addWidget(widget)
        rule_buttons.addWidget(self.example_label, 1)
        rule_buttons.addWidget(self.preview_sample)
        layout.addLayout(rule_buttons)

        splitter = QSplitter()
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.raw.setPlaceholderText("The first lines of the example file appear here.")
        self.found = QLabel("")
        self.found.setWordWrap(True)
        self.found.setTextFormat(Qt.RichText)
        found_box = QWidget()
        found_layout = QVBoxLayout(found_box)
        found_layout.setContentsMargins(8, 0, 0, 0)
        found_layout.addWidget(QLabel("<b>Values the template finds</b>"))
        found_layout.addWidget(self.found)
        found_layout.addStretch()
        splitter.addWidget(self.raw)
        splitter.addWidget(found_box)
        splitter.setSizes([640, 480])
        layout.addWidget(splitter, 1)

        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for signal in (self.header_row.valueChanged, self.delimiter.currentIndexChanged,
                       self.decimal_comma.toggled):
            signal.connect(lambda *_: self._reparse())

    # ----- rules table -----
    def _column_combo(self, text: str, allow_blank: bool) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        if allow_blank:
            combo.addItem("")
        combo.addItems(self.headers)
        combo.setCurrentText(text)
        combo.currentTextChanged.connect(lambda *_: self._preview())
        return combo

    def _add_rule(self, rule: RuleSpec) -> None:
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        parameter = QComboBox()
        parameter.addItem("", "")
        for p in self.parameters:
            parameter.addItem(f"{p.name} [{p.key}]" + (f" ×{p.replicates}" if p.replicates > 1 else ""), p.key)
        parameter.setCurrentIndex(max(parameter.findData(rule.parameter_key), 0))
        parameter.currentIndexChanged.connect(lambda *_: self._preview())
        match_text = QLineEdit(rule.match_text)
        match_text.setPlaceholderText("{sample_id} or STD")
        match_text.textChanged.connect(lambda *_: self._preview())
        take = QComboBox()
        for mode in TakeMode:
            take.addItem(mode.label, mode.value)
        take.setCurrentIndex(max(take.findData(rule.take), 0))
        take.currentIndexChanged.connect(lambda *_: self._preview())
        for column, widget in enumerate([parameter, self._column_combo(rule.column, False),
                                         self._column_combo(rule.match_column, True), match_text, take]):
            self.rules.setCellWidget(row, column, widget)

    def _remove_rule(self) -> None:
        row = self.rules.currentRow()
        if row >= 0:
            self.rules.removeRow(row)
            self._preview()

    def spec(self) -> TemplateSpec:
        rules = []
        for row in range(self.rules.rowCount()):
            w = [self.rules.cellWidget(row, c) for c in range(len(self.RULE_COLUMNS))]
            rules.append(RuleSpec(w[0].currentData() or "", w[1].currentText(), w[2].currentText(), w[3].text(),
                                  w[4].currentData()))
        return TemplateSpec(self.name.text(), self.method_code, self.header_row.value(),
                            self.delimiter.currentData(), self.decimal_comma.isChecked(), rules)

    # ----- example file and preview -----
    def _load_example(self) -> None:
        picked = _read_bytes(self, "Example instrument export")
        if picked is None:
            return
        name, self.example = picked
        self.example_label.setText(f"Example: {name}")
        lines = ii.sample_text(self.example, 40).splitlines()
        self.raw.setPlainText("\n".join(f"{i:>3} │ {line}" for i, line in enumerate(lines, start=1)))
        self._reparse()

    def _reparse(self) -> None:
        if self.example is None:
            return
        try:
            table = ii.parse_table(self.example, self.header_row.value(), self.delimiter.currentData())
        except AurumError as exc:
            self.headers = []
            self.found.setText(f"<span style='color:#b42318'>{exc}</span>")
            return
        self.headers = table.headers
        for row in range(self.rules.rowCount()):
            for column, blank in ((1, False), (2, True)):
                combo = self.rules.cellWidget(row, column)
                current = combo.currentText()
                combo.blockSignals(True)
                combo.clear()
                if blank:
                    combo.addItem("")
                combo.addItems(self.headers)
                combo.setCurrentText(current)
                combo.blockSignals(False)
        self._preview()

    def _preview(self) -> None:
        if self.example is None:
            return
        spec = self.spec()
        spec.rules = [r for r in spec.rules if r.parameter_key and r.column.strip()]
        try:
            _, result = ii.read_file(spec, self.example, self.parameters, self.preview_sample.text().strip())
        except AurumError as exc:
            self.found.setText(f"<span style='color:#b42318'>{exc}</span>")
            return
        self.found.setText(result_text(result, self.parameters))

    def _save(self) -> None:
        reason = None
        if self.template_id is not None:
            reason = ask_reason(self, "Import template", "Change how this instrument export is read.")
            if reason is None:
                return
        try:
            with self.db.session() as session:
                if self.template_id is None:
                    ii.create_template(session, self.user, self.spec())
                else:
                    ii.update_template(session, self.user, self.template_id, self.spec(), reason)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class ImportResultsDialog(QDialog):
    """Pick a template and a file; show what was found; hand the values to the result form."""

    def __init__(self, db: Database, user: SessionUser, analysis_id: int, method_code: str,
                 parameters: list[ParameterSpec], sample_id: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.analysis_id = db, user, analysis_id
        self.parameters, self.sample_id = parameters, sample_id
        self.values: dict[tuple[str, int], str] = {}
        self.file: tuple[str, bytes] | None = None
        with db.session() as session:
            self.templates = [(t.id, TemplateSpec.from_row(t)) for t in ii.templates_for(session, method_code)]
        self.setWindowTitle(f"Import results – {sample_id}")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(
            "The values found fill the result form; check them, then save as usual. The file is stored with the "
            "test exactly as read, so the reviewer can trace every value back to it."))
        form = QFormLayout()
        self.template = QComboBox()
        for template_id, spec in self.templates:
            self.template.addItem(spec.name, template_id)
        self.template.currentIndexChanged.connect(lambda *_: self._preview())
        file_row = QHBoxLayout()
        self.file_label = QLabel("No file chosen")
        choose = QPushButton("Choose file…")
        choose.clicked.connect(lambda *_: self._choose())
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(choose)
        form.addRow("Import template", self.template)
        form.addRow("Instrument file", file_row)
        layout.addLayout(form)
        self.found = QLabel("")
        self.found.setWordWrap(True)
        self.found.setTextFormat(Qt.RichText)
        layout.addWidget(self.found)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Fill in values")
        self.ok.setProperty("primary", True)
        self.ok.setEnabled(False)
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self) -> None:
        picked = _read_bytes(self, "Instrument file")
        if picked is not None:
            self.file = picked
            self.file_label.setText(picked[0])
            self._preview()

    def _spec(self) -> TemplateSpec | None:
        index = self.template.currentIndex()
        return self.templates[index][1] if 0 <= index < len(self.templates) else None

    def _preview(self) -> None:
        spec = self._spec()
        self.ok.setEnabled(False)
        if spec is None or self.file is None:
            return
        try:
            _, result = ii.read_file(spec, self.file[1], self.parameters, self.sample_id)
        except AurumError as exc:
            self.found.setText(f"<span style='color:#b42318'>{exc}</span>")
            return
        self.found.setText(result_text(result, self.parameters))
        self.ok.setEnabled(bool(result.values))

    def _import(self) -> None:
        if self.file is None or self._spec() is None:
            return
        try:
            with self.db.session() as session:
                _, result = ii.import_file(session, self.user, self.analysis_id, self.template.currentData(),
                                           self.file[0], self.file[1])
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.values = result.values
        self.accept()


def save_imported_file(parent: QWidget, db: Database, user: SessionUser, file_id: int) -> None:
    try:
        with db.session() as session:
            name, data = ii.get_file(session, user, file_id)
    except AurumError as exc:
        show_error(parent, exc, "Imported file problem")
        return
    path, _ = QFileDialog.getSaveFileName(parent, "Save imported file", name, FILE_FILTER)
    if not path:
        return
    try:
        Path(path).write_bytes(data)
    except OSError as exc:
        show_error(parent, exc, "Could not save")
        return
    QMessageBox.information(parent, "Saved", f"{name} saved to:\n{path}")
