"""Specifications page and specification editor."""

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
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import formulas, methods, sample_types, specifications
from aurum_lims.services.esign import SignatureMeaning
from aurum_lims.services.formulas import LimitType
from aurum_lims.services.specifications import LimitSpec
from aurum_lims.services.versioning import VersionStatus
from aurum_lims.ui.common import (
    allow,
    GridTable,
    ask_reason,
    error_label,
    format_local,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)
from aurum_lims.ui.sign_dialog import SignDialog
from aurum_lims.ui.views.methods import STATUS_COLORS


class LimitTable(GridTable):
    COLUMNS = ["Method", "Result", "Limit", "Low", "High", "Expected text", "Shows as"]

    def __init__(self, catalog: dict[str, list[tuple[str, str, str, str | None]]], limits: list[LimitSpec], parent=None):
        """``catalog``: method code -> [(key, name, result_type, unit)] for the newest version."""
        super().__init__(0, len(self.COLUMNS), parent)
        self.catalog = catalog
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        for column, width in enumerate([150, 170, 160, 70, 70, 140]):
            self.setColumnWidth(column, width)
        for limit in limits:
            self.add_row(limit)

    def add_row(self, limit: LimitSpec | None = None) -> None:
        row = self.rowCount()
        self.insertRow(row)
        method = QComboBox()
        for code in sorted(self.catalog):
            method.addItem(code, code)
        parameter = QComboBox()
        kind = QComboBox()
        for limit_type in LimitType:
            kind.addItem(limit_type.label, limit_type.value)
        low, high, expected = QLineEdit(), QLineEdit(), QLineEdit()
        preview = QLabel()
        for column, widget in enumerate([method, parameter, kind, low, high, expected, preview]):
            self.setCellWidget(row, column, widget)
        method.currentIndexChanged.connect(lambda *_: self._fill_parameters(method))
        for signal in (parameter.currentIndexChanged, kind.currentIndexChanged):
            signal.connect(lambda *_: self._sync(method))
        for edit in (low, high, expected):
            edit.textChanged.connect(lambda *_: self._sync(method))
        if limit:
            method.setCurrentIndex(max(method.findData(limit.method_code), 0))
        self._fill_parameters(method)
        if limit:
            parameter.setCurrentIndex(max(parameter.findData(limit.parameter_key), 0))
            kind.setCurrentIndex(max(kind.findData(limit.limit_type), 0))
            low.setText(limit.low or "")
            high.setText(limit.high or "")
            expected.setText(limit.expected_text or "")
        self._sync(method)

    def _row_of(self, method_combo) -> int:
        for row in range(self.rowCount()):
            if self.cellWidget(row, 0) is method_combo:
                return row
        return -1

    def _fill_parameters(self, method_combo) -> None:
        row = self._row_of(method_combo)
        if row < 0:
            return
        parameter = self.cellWidget(row, 1)
        parameter.blockSignals(True)
        parameter.clear()
        for key, name, result_type, unit in self.catalog.get(method_combo.currentData(), []):
            parameter.addItem(f"{name} [{key}]", key)
        parameter.blockSignals(False)
        # Suggest the natural limit type for the result type.
        result_type = self._result_type(row)
        kind = self.cellWidget(row, 2)
        if result_type == "text" and kind.currentData() not in ("text", "report"):
            kind.setCurrentIndex(kind.findData("text"))
        elif result_type == "pass_fail" and kind.currentData() not in ("pass", "report"):
            kind.setCurrentIndex(kind.findData("pass"))
        self._sync(method_combo)

    def _result_type(self, row: int) -> str | None:
        key = self.cellWidget(row, 1).currentData()
        for k, _, result_type, _ in self.catalog.get(self.cellWidget(row, 0).currentData(), []):
            if k == key:
                return result_type
        return None

    def _unit(self, row: int) -> str | None:
        key = self.cellWidget(row, 1).currentData()
        for k, _, _, unit in self.catalog.get(self.cellWidget(row, 0).currentData(), []):
            if k == key:
                return unit
        return None

    def _sync(self, method_combo) -> None:
        row = self._row_of(method_combo)
        if row < 0:
            return
        limit_type = LimitType(self.cellWidget(row, 2).currentData())
        low, high, expected, preview = (self.cellWidget(row, c) for c in (3, 4, 5, 6))
        low.setEnabled(limit_type in (LimitType.RANGE, LimitType.NLT, LimitType.GT))
        high.setEnabled(limit_type in (LimitType.RANGE, LimitType.NMT, LimitType.LT))
        expected.setEnabled(limit_type is LimitType.TEXT)
        problems = formulas.limit_problems(limit_type, low.text() if low.isEnabled() else None,
                                           high.text() if high.isEnabled() else None,
                                           expected.text() if expected.isEnabled() else None)
        if problems:
            preview.setText(f"<span style='color:#b42318'>{problems[0]}</span>")
        else:
            preview.setText(formulas.limit_text(limit_type, low.text(), high.text(), expected.text(), self._unit(row)))

    def limits(self) -> list[LimitSpec]:
        out = []
        for row in range(self.rowCount()):
            w = [self.cellWidget(row, c) for c in range(6)]
            out.append(LimitSpec(w[0].currentData() or "", w[1].currentData() or "", w[2].currentData(),
                                 w[3].text(), w[4].text(), w[5].text()))
        return out


class SpecEditorDialog(QDialog):
    def __init__(self, catalog, type_choices, parent=None, spec=None, limits=None, read_only=False):
        super().__init__(parent)
        title = "New specification" if spec is None else f"{spec.code} v{spec.version}"
        self.setWindowTitle(title + (" (read-only)" if read_only else " (draft)" if spec else ""))
        self.resize(1040, 600)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.code = QLineEdit(spec.code if spec else "")
        self.code.setEnabled(spec is None)
        self.code.setPlaceholderText("e.g. SPEC-FP-001")
        self.name = QLineEdit(spec.name if spec else "")
        self.name.setPlaceholderText("e.g. Aurumex 10 mg tablets – release")
        self.material = QLineEdit(spec.material_name or "" if spec else "")
        self.material.setPlaceholderText("Material / product name as used on samples (used to suggest this spec)")
        self.sample_type = QComboBox()
        self.sample_type.addItem("(any sample type)", None)
        for type_id, name in type_choices:
            self.sample_type.addItem(name, type_id)
        if spec and spec.sample_type_id:
            self.sample_type.setCurrentIndex(max(self.sample_type.findData(spec.sample_type_id), 0))
        self.description = QPlainTextEdit(spec.description or "" if spec else "")
        self.description.setFixedHeight(48)
        form.addRow("Code *", self.code)
        form.addRow("Name *", self.name)
        form.addRow("Material / product", self.material)
        form.addRow("Sample type", self.sample_type)
        form.addRow("Description", self.description)
        layout.addLayout(form)
        if not catalog:
            layout.addWidget(muted_label("There are no methods yet. Create and activate methods first."))
        self.table = LimitTable(catalog, limits or ([LimitSpec("", "", "range")] if catalog else []))
        layout.addWidget(self.table, 1)
        rows = QHBoxLayout()
        add = QPushButton("Add limit")
        add.clicked.connect(lambda *_: self.table.add_row())
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda *_: self.table.currentRow() >= 0 and self.table.removeRow(self.table.currentRow()))
        for b in (add, remove):
            b.setEnabled(not read_only and bool(catalog))
            rows.addWidget(b)
        rows.addStretch()
        layout.addLayout(rows)
        self.error = error_label()
        layout.addWidget(self.error)
        if read_only:
            for widget in (self.name, self.material, self.sample_type, self.description):
                widget.setEnabled(False)
            for row in range(self.table.rowCount()):
                for column in range(6):
                    self.table.cellWidget(row, column).setEnabled(False)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Save draft")
            buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
            buttons.accepted.connect(self._check)
            buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check(self) -> None:
        if not self.table.rowCount():
            show_label_message(self.error, "Add at least one limit.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text(), "limits": self.table.limits(), "material_name": self.material.text(),
                "sample_type_id": self.sample_type.currentData(), "description": self.description.toPlainText()}


class SpecificationsView(QWidget):
    title = "Specifications"
    COLUMNS = ["Code", "Version", "Name", "Material / product", "Limits", "Status", "Activated"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "A specification lists the limits for each test result. Limits point at a method code and result, so "
            "they keep working when a method gets a new version. Drafts are approved by QA before use."
        ))
        toolbar = QHBoxLayout()
        self.new_button = primary_button("New specification")
        self.new_button.setDefault(False)
        self.edit_button = QPushButton("Edit draft")
        self.version_button = QPushButton("New version")
        self.activate_button = QPushButton("Approve && activate")
        self.retire_button = QPushButton("Retire")
        allow(self.activate_button, user.can(Permission.APPROVE_RECORDS))
        self.show_retired = QCheckBox("Show retired versions")
        for b in (self.new_button, self.edit_button, self.version_button, self.activate_button, self.retire_button):
            toolbar.addWidget(b)
        toolbar.addStretch()
        toolbar.addWidget(self.show_retired)
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
        self.table.doubleClicked.connect(lambda *_: self._edit())
        layout.addWidget(self.table, 1)
        self.detail = muted_label("")
        layout.addWidget(self.detail)
        self.new_button.clicked.connect(lambda *_: self._new())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.version_button.clicked.connect(lambda *_: self._new_version())
        self.activate_button.clicked.connect(lambda *_: self._activate())
        self.retire_button.clicked.connect(lambda *_: self._retire())
        self.show_retired.toggled.connect(lambda *_: self.refresh())
        self._update_buttons()

    def _catalog(self, session):
        catalog = {}
        for m in methods.list_methods(session):
            if m.code in catalog:
                continue  # newest non-retired version first
            catalog[m.code] = [(p.key, p.name, p.result_type, p.unit)
                               for p in specifications.result_parameters(session, m.code)]
        return catalog

    def refresh(self) -> None:
        with self.db.session() as session:
            specs = specifications.list_specs(session, include_retired=self.show_retired.isChecked())
            self.rows = [(s, [specifications.to_limit(x) for x in specifications.limits_of(session, s.id)])
                         for s in specs]
            self.catalog = self._catalog(session)
            self.type_choices = [(t.id, t.name) for t in sample_types.list_types(session)]
        self.table.setRowCount(len(self.rows))
        for r, (s, limits) in enumerate(self.rows):
            values = [s.code, f"v{s.version}", s.name, s.material_name or "", str(len(limits)), s.status,
                      format_local(s.activated_at) if s.activated_at else ""]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 5:
                    item.setForeground(QColor(STATUS_COLORS.get(s.status, "#1f2933")))
                self.table.setItem(r, c, item)
        self._update_buttons()

    def _selected(self):
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else (None, None)

    def _update_buttons(self) -> None:
        spec, limits = self._selected()
        status = spec.status if spec else None
        self.edit_button.setEnabled(spec is not None)
        self.edit_button.setText("Edit draft" if status == VersionStatus.DRAFT else "View")
        self.version_button.setEnabled(status in (VersionStatus.ACTIVE, VersionStatus.RETIRED))
        self.activate_button.setEnabled(status == VersionStatus.DRAFT)
        self.retire_button.setEnabled(status in (VersionStatus.DRAFT, VersionStatus.ACTIVE))
        if spec:
            self.detail.setText(f"{spec.code} v{spec.version}: " + "; ".join(
                f"{x.method_code} {x.parameter_key}: {formulas.limit_text(x.limit_type, x.low, x.high, x.expected_text)}"
                for x in limits))
        else:
            self.detail.setText("")

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
        dialog = SpecEditorDialog(self.catalog, self.type_choices, self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: specifications.create_spec(s, self.user, code=dialog.code.text(), **dialog.values())):
                return

    def _edit(self) -> None:
        spec, limits = self._selected()
        if spec is None:
            return
        read_only = spec.status != VersionStatus.DRAFT
        dialog = SpecEditorDialog(self.catalog, self.type_choices, self, spec, limits, read_only=read_only)
        if read_only:
            dialog.exec()
            return
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: specifications.update_draft(s, self.user, spec.id, **dialog.values())):
                return

    def _new_version(self) -> None:
        spec, _ = self._selected()
        if spec is not None and self._run(lambda s: specifications.new_version(s, self.user, spec.id)):
            QMessageBox.information(self, "New version", f"A draft of {spec.code} was created. Edit it, then ask QA "
                                                         "to approve and activate it.")

    def _activate(self) -> None:
        spec, _ = self._selected()
        if spec is None:
            return

        def action(password):
            with self.db.session() as session:
                return specifications.activate(session, self.user, spec.id, password)

        statement = (f"By entering your password you electronically sign:\n“{SignatureMeaning.APPROVED}” "
                     f"{self.user.full_name} — {spec.code} v{spec.version} is approved for use. "
                     "Any previous active version will be retired.")
        if SignDialog(self, "Approve specification", statement, action, "Sign and activate").exec() == QDialog.Accepted:
            self.refresh()

    def _retire(self) -> None:
        spec, _ = self._selected()
        if spec is None:
            return
        reason = ask_reason(self, "Retire specification", f"Retire {spec.code} v{spec.version}?")
        if reason is not None:
            self._run(lambda s: specifications.retire(s, self.user, spec.id, reason))
