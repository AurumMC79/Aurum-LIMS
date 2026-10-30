"""Suppliers tab of the Inventory page."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import inventory, suppliers
from aurum_lims.services.suppliers import APPROVED_STATUSES, SupplierDetails, SupplierStatus
from aurum_lims.ui.common import ask_reason, error_label, muted_label, primary_button, show_error, show_label_message
from aurum_lims.ui.views.instruments import AMBER, GREEN, GREY, RED, _table

STATUS_COLOURS = {SupplierStatus.APPROVED: GREEN, SupplierStatus.CONDITIONAL: AMBER,
                  SupplierStatus.EVALUATION: AMBER, SupplierStatus.DISQUALIFIED: RED}


def _optional_date(value: date | None, default: date) -> tuple[QCheckBox, QDateEdit, QHBoxLayout]:
    check = QCheckBox()
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    shown = value or default
    edit.setDate(QDate(shown.year, shown.month, shown.day))
    check.setChecked(value is not None)
    edit.setEnabled(value is not None)
    check.toggled.connect(edit.setEnabled)
    row = QHBoxLayout()
    row.addWidget(check)
    row.addWidget(edit)
    row.addStretch()
    return check, edit, row


class SupplierDialog(QDialog):
    def __init__(self, parent: QWidget, can_approve: bool, existing=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {existing.name}" if existing else "New supplier")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        get = (lambda name: getattr(existing, name) or "") if existing else (lambda name: "")
        self.name = QLineEdit(get("name"))
        self.status = QComboBox()
        for status in SupplierStatus:
            self.status.addItem(status.value)
        self.status.setCurrentText(existing.status if existing else SupplierStatus.EVALUATION)
        self.status.setEnabled(can_approve)
        if not can_approve:
            self.status.setToolTip("Only QA can approve, conditionally approve or disqualify a supplier.")
        self.scope = QLineEdit(get("scope"))
        self.scope.setPlaceholderText("What they supply, e.g. HPLC solvents, reference standards, calibration service")
        self.contact = QLineEdit(get("contact_name"))
        self.email = QLineEdit(get("email"))
        self.phone = QLineEdit(get("phone"))
        self.address = QPlainTextEdit(get("address"))
        self.address.setFixedHeight(54)
        self.qualification = QLineEdit(get("qualification_ref"))
        self.qualification.setPlaceholderText("e.g. questionnaire QA-SUP-014, ISO 9001 certificate, audit report")
        self.has_approved, self.approved_on, approved_row = _optional_date(
            existing.approved_on if existing else None, date.today())
        self.has_review, self.review_due, review_row = _optional_date(
            existing.review_due if existing else None, date.today() + timedelta(days=730))
        if not can_approve:
            for widget in (self.has_approved, self.approved_on, self.has_review, self.review_due):
                widget.setEnabled(False)
        self.notes = QPlainTextEdit(get("notes"))
        self.notes.setFixedHeight(54)
        form.addRow("Name *", self.name)
        form.addRow("Approval status", self.status)
        form.addRow("Supplies", self.scope)
        form.addRow("Contact", self.contact)
        form.addRow("Email", self.email)
        form.addRow("Phone", self.phone)
        form.addRow("Address", self.address)
        form.addRow("Qualification ref.", self.qualification)
        form.addRow("Approved on", approved_row)
        form.addRow("Review due", review_row)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        layout.addWidget(muted_label("Approved and conditionally approved suppliers need a review date. Lots cannot "
                                     "be received from a disqualified supplier."))
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.status.currentTextChanged.connect(self._status_changed)

    def _status_changed(self, status: str) -> None:
        if status in APPROVED_STATUSES and not self.has_review.isChecked():
            self.has_review.setChecked(True)
            self.has_approved.setChecked(True)

    def _check(self) -> None:
        if not self.name.text().strip():
            show_label_message(self.error, "Name is required.")
            return
        self.accept()

    def details(self) -> SupplierDetails:
        return SupplierDetails(
            self.name.text(), self.status.currentText(), self.scope.text(), self.contact.text(), self.email.text(),
            self.phone.text(), self.address.toPlainText(), self.qualification.text(),
            self.approved_on.date().toPython() if self.has_approved.isChecked() else None,
            self.review_due.date().toPython() if self.has_review.isChecked() else None,
            self.notes.toPlainText())


class SuppliersTab(QWidget):
    COLUMNS = ["Supplier", "Status", "Supplies", "Contact", "Review due", "Items", "Lots received",
               "Last delivery", "Attention"]
    DELIVERY_COLUMNS = ["Lot ID", "Item", "Supplier lot", "Received", "Quantity", "Status"]

    def __init__(self, db: Database, user: SessionUser, on_change=None, parent=None):
        super().__init__(parent)
        self.db, self.user, self.on_change = db, user, on_change
        self.rows = []
        self.can_manage = inventory.can_manage(user)
        self.can_approve = user.can(Permission.APPROVE_RECORDS)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(
            "Approved suppliers are qualified (questionnaire, certificate or audit) and reviewed by the date shown. "
            "Receiving from a supplier that is not approved, or whose review is overdue, asks for confirmation; "
            "disqualified suppliers are refused. Only QA can change a supplier's approval status."))
        bar = QHBoxLayout()
        self.new_button = primary_button("New supplier")
        self.new_button.setDefault(False)
        self.edit_button = QPushButton("Edit")
        self.active_button = QPushButton("Deactivate")
        for b in (self.new_button, self.edit_button, self.active_button):
            b.setVisible(self.can_manage)
            bar.addWidget(b)
        bar.addStretch()
        self.show_inactive = QCheckBox("Show inactive")
        bar.addWidget(self.show_inactive)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Vertical)
        self.table = _table(self.COLUMNS, 8)
        splitter.addWidget(self.table)
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 6, 0, 0)
        self.detail = QLabel("<b>Deliveries</b> – select a supplier")
        self.detail.setWordWrap(True)
        lower_layout.addWidget(self.detail)
        self.deliveries = _table(self.DELIVERY_COLUMNS, 1)
        lower_layout.addWidget(self.deliveries)
        splitter.addWidget(lower)
        splitter.setSizes([340, 240])
        layout.addWidget(splitter, 1)
        self.new_button.clicked.connect(lambda *_: self._new())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.active_button.clicked.connect(lambda *_: self._toggle())
        self.show_inactive.toggled.connect(lambda *_: self.refresh())
        self.table.itemSelectionChanged.connect(self._selected_changed)
        self.table.doubleClicked.connect(lambda *_: self._edit() if self.can_manage else None)
        self._selected_changed()

    def refresh(self) -> None:
        selected = self._selected()
        with self.db.session() as session:
            self.rows = suppliers.overview(session, self.show_inactive.isChecked())
        today = date.today()
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            s = row.supplier
            contact = " · ".join(x for x in (s.contact_name, s.email, s.phone) if x)
            values = [s.name, s.status if s.is_active else f"{s.status} (inactive)", s.scope or "", contact,
                      s.review_due.isoformat() if s.review_due else "", str(row.items), str(row.lots_received),
                      row.last_received.isoformat() if row.last_received else "",
                      "; ".join(c.replace(f"{s.name} ", "") for c in row.concerns) or "—"]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if c == 1:
                    item.setForeground(QColor(STATUS_COLOURS.get(s.status, GREY) if s.is_active else GREY))
                if c == 4 and s.review_due and s.status in APPROVED_STATUSES:
                    item.setForeground(QColor(RED if s.review_due < today else
                                              AMBER if s.review_due <= today + timedelta(days=60) else GREEN))
                if c == 8 and row.concerns:
                    item.setForeground(QColor(RED if s.status == SupplierStatus.DISQUALIFIED else AMBER))
                self.table.setItem(r, c, item)
        if selected is not None:
            for r, row in enumerate(self.rows):
                if row.supplier.id == selected.supplier.id:
                    self.table.selectRow(r)
        self._selected_changed()

    def _selected(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) and self.table.selectedItems() else None

    def _selected_changed(self) -> None:
        row = self._selected()
        self.edit_button.setEnabled(row is not None)
        self.active_button.setEnabled(row is not None)
        self.active_button.setText("Activate" if row is not None and not row.supplier.is_active else "Deactivate")
        if row is None:
            self.detail.setText("<b>Deliveries</b> – select a supplier")
            self.deliveries.setRowCount(0)
            return
        s = row.supplier
        with self.db.session() as session:
            lots = suppliers.deliveries(session, s.id)
        extra = " · ".join(x for x in (
            f"qualified by {s.qualification_ref}" if s.qualification_ref else None,
            f"approved {s.approved_on.isoformat()}" if s.approved_on else None,
            f"{row.lots_discarded} lot(s) discarded" if row.lots_discarded else None) if x)
        self.detail.setText(f"<b>Deliveries – {s.name}</b>  {extra}")
        self.deliveries.setRowCount(len(lots))
        for r, lot in enumerate(lots):
            values = [lot.internal_id, lot.item_name, lot.lot_number, lot.received_on.isoformat(), lot.quantity,
                      lot.status]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 5 and value == "Discarded":
                    item.setForeground(QColor(RED))
                self.deliveries.setItem(r, c, item)

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        if self.on_change:
            self.on_change()
        return True

    def _new(self) -> None:
        dialog = SupplierDialog(self, self.can_approve)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: suppliers.create_supplier(s, self.user, dialog.details())):
                return

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = SupplierDialog(self, self.can_approve, row.supplier)
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit supplier", f"Changing {row.supplier.name}.")
            if reason is not None and self._run(
                    lambda s: suppliers.update_supplier(s, self.user, row.supplier.id, dialog.details(), reason)):
                return

    def _toggle(self) -> None:
        row = self._selected()
        if row is None:
            return
        activate = not row.supplier.is_active
        reason = ask_reason(self, "Supplier", f"{'Activate' if activate else 'Deactivate'} {row.supplier.name}.")
        if reason is not None:
            self._run(lambda s: suppliers.set_active(s, self.user, row.supplier.id, activate, reason))
