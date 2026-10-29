"""The 'Equipment and materials' part of the results window."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import instruments, inventory, methods, usage
from aurum_lims.services.methods import RequirementKind
from aurum_lims.ui.common import GridTable, error_label, muted_label, show_label_message

OTHER = -1


class RecordUsageDialog(QDialog):
    """Pick the instrument or lot used for one of the method's requirements."""

    def __init__(self, db: Database, user: SessionUser, analysis_id: int, method_id: int, kind: str, parent=None):
        super().__init__(parent)
        self.db, self.user, self.analysis_id, self.kind = db, user, analysis_id, kind
        is_instrument = kind == RequirementKind.INSTRUMENT
        self.setWindowTitle("Record instrument used" if is_instrument else "Record reagent / standard lot used")
        self.setMinimumWidth(560)
        with db.session() as session:
            self.requirements = [(r.id, r.label, r.category, r.fills_parameter)
                                 for r in methods.requirements_of(session, method_id) if r.kind == kind]
            if is_instrument:
                self.choices = [(i.id, i.category, f"{i.code} – {i.name}", instruments.problems(i), None)
                                for i in instruments.list_instruments(session)]
            else:
                self.choices = [(lot.id, lot.kind, f"{lot.internal_id} – {lot.item_name}, lot {lot.lot_number} "
                                 f"({lot.quantity_on_hand} {lot.unit} left"
                                 + (f", purity {lot.purity} %" if lot.purity else "")
                                 + (f", expires {lot.expiry.isoformat()}" if lot.expiry else "") + ")",
                                 lot.problems, lot.unit) for lot in inventory.list_lots(session)]

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.requirement = QComboBox()
        for req_id, label, category, fills in self.requirements:
            self.requirement.addItem(f"{label} ({category})" + (f" – fills {fills}" if fills else ""), req_id)
        self.requirement.addItem("Something else (not a method requirement)", OTHER)
        self.choice = QComboBox()
        self.status = muted_label("")
        form.addRow("For", self.requirement)
        form.addRow("Instrument" if is_instrument else "Lot", self.choice)
        form.addRow("", self.status)
        self.quantity = QLineEdit()
        self.unit = QLabel()
        if not is_instrument:
            row = QHBoxLayout()
            row.addWidget(self.quantity)
            row.addWidget(self.unit)
            self.quantity.setPlaceholderText("Amount taken from this lot (optional; deducted from stock)")
            form.addRow("Quantity used", row)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.requirement.currentIndexChanged.connect(lambda *_: self._fill())
        self.choice.currentIndexChanged.connect(lambda *_: self._show_status())
        self._fill()

    def _fill(self) -> None:
        req_id = self.requirement.currentData()
        category = next((c for r, _, c, _ in self.requirements if r == req_id), None)
        self.choice.clear()
        for choice_id, choice_category, label, problems, unit in self.choices:
            if category is None or choice_category == category:
                self.choice.addItem(label + ("  ⚠ not usable" if problems else ""), choice_id)
        if not self.choice.count():
            self.choice.addItem("(none available in this category)", None)
        self._show_status()

    def _show_status(self) -> None:
        current = next((c for c in self.choices if c[0] == self.choice.currentData()), None)
        if current is None:
            self.status.setText("")
            return
        problems, unit = current[3], current[4]
        self.unit.setText(unit or "")
        self.status.setText("<span style='color:#b42318'>" + "; ".join(problems) + "</span>" if problems
                            else "<span style='color:#067647'>OK to use</span>")

    def _submit(self) -> None:
        choice = self.choice.currentData()
        if choice is None:
            show_label_message(self.error, "Nothing to choose.")
            return
        req_id = self.requirement.currentData()
        req_id = None if req_id == OTHER else req_id
        try:
            with self.db.session() as session:
                if self.kind == RequirementKind.INSTRUMENT:
                    usage.record_instrument(session, self.user, self.analysis_id, choice, req_id)
                else:
                    usage.record_lot(session, self.user, self.analysis_id, choice,
                                     quantity_used=self.quantity.text(), requirement_id=req_id)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


def usage_table(requirements, usages) -> QTableWidget:
    """Requirement-by-requirement view of what has been recorded."""
    table = GridTable(0, 4)
    table.setHorizontalHeaderLabels(["Required", "Recorded", "Details", "Status"])
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    rows = []
    by_requirement = {}
    for u in usages:
        by_requirement.setdefault(u.requirement_id, []).append(u)
    for r in requirements:
        recorded = by_requirement.pop(r.id, [])
        if not recorded:
            rows.append((r.label, "—", "", "Missing", None))
        for u in recorded:
            rows.append((r.label, u.code, u.description + (f" · used {u.quantity}" if u.quantity else ""),
                         "; ".join(u.problems) if u.problems else "OK", u.id))
    for extra in by_requirement.values():
        for u in extra:
            rows.append(("(additional)", u.code, u.description + (f" · used {u.quantity}" if u.quantity else ""),
                         "; ".join(u.problems) if u.problems else "OK", u.id))
    table.setRowCount(len(rows))
    table.usage_ids = []
    for i, (required, code, details, status, usage_id) in enumerate(rows):
        for c, value in enumerate((required, code, details, status)):
            item = QTableWidgetItem(value)
            if c == 3:
                item.setForeground(QColor("#067647" if status == "OK" else "#b42318"))
            table.setItem(i, c, item)
        table.usage_ids.append(usage_id)
    table.setMaximumHeight(36 + 30 * max(len(rows), 1))
    return table
