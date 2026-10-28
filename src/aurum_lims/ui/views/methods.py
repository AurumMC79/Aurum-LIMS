"""Methods page and method editor."""

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import methods
from aurum_lims.services.esign import SignatureMeaning
from aurum_lims.services.methods import ParameterSpec
from aurum_lims.services.versioning import VersionStatus
from aurum_lims.ui.common import ask_reason, error_label, format_local, muted_label, primary_button, show_error, show_label_message
from aurum_lims.ui.sign_dialog import SignDialog

# (label, kind, result_type, calculated)
PARAMETER_TYPES = [
    ("Input (number)", "input", "numeric", False),
    ("Result (number, entered)", "result", "numeric", False),
    ("Result (calculated)", "result", "numeric", True),
    ("Result (text)", "result", "text", False),
    ("Result (Pass / Fail)", "result", "pass_fail", False),
]
STATUS_COLORS = {VersionStatus.DRAFT: "#8a6d15", VersionStatus.ACTIVE: "#067647", VersionStatus.RETIRED: "#8b98a8"}


def type_index(p: ParameterSpec) -> int:
    for i, (_, kind, result_type, calculated) in enumerate(PARAMETER_TYPES):
        if kind == p.kind and result_type == p.result_type and calculated == bool(p.formula):
            return i
    return 1


class ParameterTable(QTableWidget):
    COLUMNS = ["Key", "Name", "Type", "Unit", "Decimals", "Replicates", "Formula (uses keys above)"]

    def __init__(self, parameters: list[ParameterSpec], parent=None):
        super().__init__(0, len(self.COLUMNS), parent)
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().setVisible(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        for column, width in enumerate([110, 190, 190, 60, 72, 80]):
            self.setColumnWidth(column, width)
        for p in parameters:
            self.add_row(p)

    def add_row(self, p: ParameterSpec | None = None, at: int | None = None) -> None:
        p = p or ParameterSpec(key="", name="")
        row = self.rowCount() if at is None else at
        self.insertRow(row)
        key, name, unit, formula = QLineEdit(p.key), QLineEdit(p.name), QLineEdit(p.unit or ""), QLineEdit(p.formula or "")
        key.setPlaceholderText("e.g. wt_spl")
        formula.setPlaceholderText("e.g. area_spl / area_std * 100")
        kind = QComboBox()
        for label, *_ in PARAMETER_TYPES:
            kind.addItem(label)
        kind.setCurrentIndex(type_index(p))
        decimals = QLineEdit("" if p.decimals is None else str(p.decimals))
        decimals.setPlaceholderText("–")
        decimals.setToolTip("Decimal places the reportable result is rounded to (USP). Leave blank for no rounding.")
        replicates = QSpinBox()
        replicates.setRange(1, methods.MAX_REPLICATES)
        replicates.setValue(p.replicates or 1)
        for column, widget in enumerate([key, name, kind, unit, decimals, replicates, formula]):
            self.setCellWidget(row, column, widget)
        kind.currentIndexChanged.connect(lambda *_: self._sync(kind))
        self._sync(kind)

    def _row_of(self, widget) -> int:
        for row in range(self.rowCount()):
            if self.cellWidget(row, 2) is widget:
                return row
        return -1

    def _sync(self, kind_combo: QComboBox) -> None:
        row = self._row_of(kind_combo)
        if row < 0:
            return
        _, kind, result_type, calculated = PARAMETER_TYPES[kind_combo.currentIndex()]
        numeric = result_type == "numeric"
        self.cellWidget(row, 3).setEnabled(numeric)
        self.cellWidget(row, 4).setEnabled(numeric)
        self.cellWidget(row, 5).setEnabled(numeric and not calculated)
        self.cellWidget(row, 6).setEnabled(calculated)

    def parameters(self) -> list[ParameterSpec]:
        out = []
        for row in range(self.rowCount()):
            w = [self.cellWidget(row, c) for c in range(len(self.COLUMNS))]
            _, kind, result_type, calculated = PARAMETER_TYPES[w[2].currentIndex()]
            decimals_text = w[4].text().strip()
            try:
                decimals = int(decimals_text) if decimals_text else None
            except ValueError:
                decimals = -1  # rejected by validation with a clear message
            out.append(ParameterSpec(
                key=w[0].text(), name=w[1].text(), kind=kind, result_type=result_type, unit=w[3].text(),
                decimals=decimals, replicates=w[5].value(), formula=w[6].text() if calculated else None,
            ))
        return out

    def move(self, offset: int) -> None:
        row = self.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.rowCount():
            return
        params = self.parameters()
        params[row], params[target] = params[target], params[row]
        self.setRowCount(0)
        for p in params:
            self.add_row(p)
        self.selectRow(target)


class MethodEditorDialog(QDialog):
    def __init__(self, parent=None, method=None, parameters: list[ParameterSpec] | None = None,
                 read_only: bool = False):
        super().__init__(parent)
        self.method = method
        if read_only:
            self.setWindowTitle(f"{method.code} v{method.version} ({method.status}, read-only)")
        else:
            self.setWindowTitle(f"Edit {method.code} v{method.version} (draft)" if method else "New method")
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.code = QLineEdit(method.code if method else "")
        self.code.setEnabled(method is None)
        self.code.setPlaceholderText("e.g. HPLC-ASSAY-01")
        self.name = QLineEdit(method.name if method else "")
        self.sop = QLineEdit(method.sop_reference or "" if method else "")
        self.sop.setPlaceholderText("e.g. SOP-QC-101 rev 3")
        self.description = QPlainTextEdit(method.description or "" if method else "")
        self.description.setFixedHeight(48)
        form.addRow("Code *", self.code)
        form.addRow("Name *", self.name)
        form.addRow("SOP reference", self.sop)
        form.addRow("Description", self.description)
        layout.addLayout(form)

        layout.addWidget(muted_label(
            "Inputs are entered values that are not reported (weights, peak areas). Results are reported and "
            "compared with the specification. A calculated result's formula can use the keys of parameters listed "
            "above it, e.g. area_spl / area_std * wt_std / wt_spl * purity_std. Parameters with several replicates "
            "are calculated per replicate and averaged; single values (1 replicate) are shared by all replicates."
        ))
        self.table = ParameterTable(parameters or [ParameterSpec("", "")])
        layout.addWidget(self.table, 1)
        row_buttons = QHBoxLayout()
        for text, handler in (("Add parameter", lambda: self.table.add_row()),
                              ("Insert above", lambda: self.table.add_row(at=max(self.table.currentRow(), 0))),
                              ("Remove", self._remove), ("Move up", lambda: self.table.move(-1)),
                              ("Move down", lambda: self.table.move(1))):
            button = QPushButton(text)
            button.clicked.connect(lambda *_, h=handler: h())
            button.setEnabled(not read_only)
            row_buttons.addWidget(button)
        row_buttons.addStretch()
        layout.addLayout(row_buttons)
        self.error = error_label()
        layout.addWidget(self.error)
        if read_only:
            for widget in (self.name, self.sop, self.description):
                widget.setEnabled(False)
            for row in range(self.table.rowCount()):
                for column in range(self.table.columnCount()):
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

    def _remove(self) -> None:
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def _check(self) -> None:
        try:
            methods.validate_parameters(self.table.parameters())
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text(), "parameters": self.table.parameters(),
                "sop_reference": self.sop.text(), "description": self.description.toPlainText()}


class MethodsView(QWidget):
    title = "Methods"
    COLUMNS = ["Code", "Version", "Name", "SOP", "Parameters", "Status", "Activated"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "Methods are edited as drafts and approved by QA with an electronic signature before use. To change an "
            "active method, create a new version; the old version is retired when the new one is activated, and "
            "every test keeps the exact version it was run with."
        ))
        toolbar = QHBoxLayout()
        self.new_button = primary_button("New method")
        self.new_button.setDefault(False)
        self.edit_button = QPushButton("Edit draft")
        self.version_button = QPushButton("New version")
        self.activate_button = QPushButton("Approve && activate")
        self.retire_button = QPushButton("Retire")
        self.show_retired = QCheckBox("Show retired versions")
        self.activate_button.setVisible(user.can(Permission.APPROVE_RECORDS))
        for b in (self.new_button, self.edit_button, self.version_button, self.activate_button, self.retire_button):
            toolbar.addWidget(b)
        toolbar.addStretch()
        toolbar.addWidget(self.show_retired)
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

    def refresh(self) -> None:
        with self.db.session() as session:
            rows = methods.list_methods(session, include_retired=self.show_retired.isChecked())
            self.rows = [(m, [methods.to_spec(p) for p in methods.parameters_of(session, m.id)]) for m in rows]
        self.table.setRowCount(len(self.rows))
        for r, (m, params) in enumerate(self.rows):
            values = [m.code, f"v{m.version}", m.name, m.sop_reference or "", str(len(params)), m.status,
                      format_local(m.activated_at) if m.activated_at else ""]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 5:
                    item.setForeground(QColor(STATUS_COLORS.get(m.status, "#1f2933")))
                self.table.setItem(r, c, item)
        self._update_buttons()

    def _selected(self):
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else (None, None)

    def _update_buttons(self) -> None:
        method, params = self._selected()
        status = method.status if method else None
        self.edit_button.setEnabled(status is not None)
        self.edit_button.setText("Edit draft" if status == VersionStatus.DRAFT else "View")
        self.version_button.setEnabled(status in (VersionStatus.ACTIVE, VersionStatus.RETIRED))
        self.activate_button.setEnabled(status == VersionStatus.DRAFT)
        self.retire_button.setEnabled(status in (VersionStatus.DRAFT, VersionStatus.ACTIVE))
        if method:
            summary = "; ".join(
                f"{p.name} [{p.key}]" + (f" = {p.formula}" if p.formula else "")
                + (f" ×{p.replicates}" if p.replicates > 1 else "") for p in params)
            self.detail.setText(f"{method.code} v{method.version}: {summary}")
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
        dialog = MethodEditorDialog(self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: methods.create_method(s, self.user, code=dialog.code.text(), **dialog.values())):
                return

    def _edit(self) -> None:
        method, params = self._selected()
        if method is None:
            return
        dialog = MethodEditorDialog(self, method, params, read_only=method.status != VersionStatus.DRAFT)
        if method.status != VersionStatus.DRAFT:
            dialog.exec()
            return
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: methods.update_draft(s, self.user, method.id, **dialog.values())):
                return

    def _new_version(self) -> None:
        method, _ = self._selected()
        if method is not None and self._run(lambda s: methods.new_version(s, self.user, method.id)):
            QMessageBox.information(self, "New version", f"A draft of {method.code} was created. Edit it, then ask QA "
                                                         "to approve and activate it.")

    def _activate(self) -> None:
        method, _ = self._selected()
        if method is None:
            return

        def action(password):
            with self.db.session() as session:
                return methods.activate(session, self.user, method.id, password)

        statement = (f"By entering your password you electronically sign:\n“{SignatureMeaning.APPROVED}” "
                     f"{self.user.full_name} — {method.code} v{method.version} is approved for use. "
                     "Any previous active version will be retired.")
        if SignDialog(self, "Approve method", statement, action, "Sign and activate").exec() == QDialog.Accepted:
            self.refresh()

    def _retire(self) -> None:
        method, _ = self._selected()
        if method is None:
            return
        reason = ask_reason(self, "Retire method", f"Retire {method.code} v{method.version}? It can no longer be "
                                                   "assigned to samples.")
        if reason is not None:
            self._run(lambda s: methods.retire(s, self.user, method.id, reason))
