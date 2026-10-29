"""Inventory page: lots in stock (with expiry and quantity) and the item catalogue."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import formulas, inventory, storage
from aurum_lims.services.inventory import UNITS, ItemDetails, ItemKind
from aurum_lims.ui.common import (
    ask_reason,
    error_label,
    fill_location_combo,
    format_local,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)
from aurum_lims.ui.views.instruments import AMBER, GREEN, GREY, RED, _table, date_edit


class ItemDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {existing.name}" if existing else "New inventory item")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(existing.name if existing else "")
        self.name.setPlaceholderText("e.g. Acetonitrile HPLC grade, Aurumex reference standard")
        self.kind = QComboBox()
        for kind in ItemKind:
            self.kind.addItem(kind.value)
        if existing:
            self.kind.setCurrentText(existing.kind)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(UNITS)
        self.unit.setCurrentText(existing.unit if existing else "g")
        self.grade = QLineEdit(existing.grade or "" if existing else "")
        self.supplier = QLineEdit(existing.supplier or "" if existing else "")
        self.catalogue = QLineEdit(existing.catalogue_number or "" if existing else "")
        self.min_stock = QLineEdit(existing.min_stock or "" if existing else "")
        self.min_stock.setPlaceholderText("Warn when usable stock falls below this (optional)")
        self.days_open = QSpinBox()
        self.days_open.setRange(0, 3650)
        self.days_open.setSpecialValueText("no limit")
        self.days_open.setSuffix(" days")
        self.days_open.setValue((existing.days_after_opening or 0) if existing else 0)
        self.active = QCheckBox("Active")
        self.active.setChecked(existing.is_active if existing else True)
        form.addRow("Name *", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Stock unit", self.unit)
        form.addRow("Grade", self.grade)
        form.addRow("Supplier", self.supplier)
        form.addRow("Catalogue number", self.catalogue)
        form.addRow("Minimum stock", self.min_stock)
        form.addRow("Use within after opening", self.days_open)
        if existing:
            form.addRow("", self.active)
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
        if not self.name.text().strip():
            show_label_message(self.error, "Name is required.")
            return
        self.accept()

    def details(self) -> ItemDetails:
        return ItemDetails(self.name.text(), self.kind.currentText(), self.unit.currentText(), self.grade.text(),
                           self.supplier.text(), self.catalogue.text(), self.min_stock.text(),
                           self.days_open.value() or None)


class ReceiveLotDialog(QDialog):
    def __init__(self, items, locations, parent=None, item_id: int | None = None):
        super().__init__(parent)
        self.items = {i.id: i for i in items}
        self.setWindowTitle("Receive a lot")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.item = QComboBox()
        for i in items:
            self.item.addItem(f"{i.name} ({i.kind})", i.id)
        if item_id:
            self.item.setCurrentIndex(max(self.item.findData(item_id), 0))
        self.lot_number = QLineEdit()
        self.lot_number.setPlaceholderText("Supplier's lot / batch number")
        self.quantity = QLineEdit()
        self.unit_label = QLabel()
        quantity_row = QHBoxLayout()
        quantity_row.addWidget(self.quantity)
        quantity_row.addWidget(self.unit_label)
        self.received_on = date_edit()
        self.received_on.setMaximumDate(QDate.currentDate())
        self.has_expiry = QCheckBox("Expiry date")
        self.has_expiry.setChecked(True)
        self.expiry = date_edit(date.today() + timedelta(days=365))
        self.has_expiry.toggled.connect(self.expiry.setEnabled)
        expiry_row = QHBoxLayout()
        expiry_row.addWidget(self.has_expiry)
        expiry_row.addWidget(self.expiry)
        expiry_row.addStretch()
        self.purity = QLineEdit()
        self.purity.setPlaceholderText("Certified purity / assay in %, from the certificate")
        self.certificate = QLineEdit()
        self.location = QComboBox()
        fill_location_combo(self.location, locations)
        self.location.setItemText(0, "(not specified)")
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(46)
        form.addRow("Item", self.item)
        form.addRow("Lot number *", self.lot_number)
        form.addRow("Quantity received *", quantity_row)
        form.addRow("Received on", self.received_on)
        form.addRow("", expiry_row)
        self.purity_label = QLabel("Purity (%)")
        form.addRow(self.purity_label, self.purity)
        form.addRow("Certificate ref.", self.certificate)
        form.addRow("Stored in", self.location)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Receive")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.item.currentIndexChanged.connect(lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        item = self.items.get(self.item.currentData())
        self.unit_label.setText(item.unit if item else "")
        standard = item is not None and item.kind == ItemKind.REFERENCE_STANDARD
        self.purity_label.setText("Purity (%) *" if standard else "Purity (%)")

    def values(self) -> dict:
        return {
            "lot_number": self.lot_number.text(), "quantity": self.quantity.text(),
            "received_on": self.received_on.date().toPython(),
            "expiry_date": self.expiry.date().toPython() if self.has_expiry.isChecked() else None,
            "purity": self.purity.text(), "certificate_ref": self.certificate.text(),
            "location_id": self.location.currentData(), "notes": self.notes.toPlainText(),
        }


class InventoryView(QWidget):
    title = "Inventory"
    LOT_COLUMNS = ["Lot ID", "Item", "Type", "Supplier lot", "On hand", "Opened", "Expires", "Purity", "Stored in",
                   "Status"]
    ITEM_COLUMNS = ["Name", "Type", "Grade", "Supplier", "Catalogue no.", "Unit", "Usable stock", "Minimum",
                    "After opening", "Status"]
    TX_COLUMNS = ["When", "Movement", "Quantity", "Balance", "Test", "Reason", "By"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.lots = []
        self.items = []
        can_manage = inventory.can_manage(user)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ----- lots tab -----
        lots_tab = QWidget()
        lots_layout = QVBoxLayout(lots_tab)
        toolbar = QHBoxLayout()
        self.receive_button = primary_button("Receive lot…")
        self.receive_button.setDefault(False)
        self.open_button = QPushButton("Mark opened")
        self.adjust_button = QPushButton("Stock count…")
        self.discard_button = QPushButton("Discard…")
        self.discard_button.setProperty("danger", True)
        for b in (self.receive_button, self.open_button, self.adjust_button, self.discard_button):
            b.setVisible(can_manage)
            toolbar.addWidget(b)
        toolbar.addStretch()
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("All types", None)
        for kind in ItemKind:
            self.kind_filter.addItem(kind.value, kind.value)
        self.show_closed = QCheckBox("Include depleted / discarded")
        toolbar.addWidget(self.kind_filter)
        toolbar.addWidget(self.show_closed)
        lots_layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Vertical)
        self.lot_table = _table(self.LOT_COLUMNS, len(self.LOT_COLUMNS) - 1)
        splitter.addWidget(self.lot_table)
        tx_box = QWidget()
        tx_layout = QVBoxLayout(tx_box)
        tx_layout.setContentsMargins(0, 6, 0, 0)
        self.tx_title = QLabel("<b>Stock movements</b>")
        tx_layout.addWidget(self.tx_title)
        self.tx_table = _table(self.TX_COLUMNS, 5)
        tx_layout.addWidget(self.tx_table)
        splitter.addWidget(tx_box)
        splitter.setSizes([380, 220])
        lots_layout.addWidget(splitter, 1)
        self.tabs.addTab(lots_tab, "Lots in stock")

        # ----- items tab -----
        items_tab = QWidget()
        items_layout = QVBoxLayout(items_tab)
        items_layout.addWidget(muted_label("The catalogue of things you stock. Receive lots of an item on the "
                                           "'Lots in stock' tab."))
        toolbar = QHBoxLayout()
        self.new_item_button = primary_button("New item")
        self.new_item_button.setDefault(False)
        self.edit_item_button = QPushButton("Edit")
        for b in (self.new_item_button, self.edit_item_button):
            b.setVisible(can_manage)
            toolbar.addWidget(b)
        toolbar.addStretch()
        self.show_inactive = QCheckBox("Show inactive")
        toolbar.addWidget(self.show_inactive)
        items_layout.addLayout(toolbar)
        self.item_table = _table(self.ITEM_COLUMNS, 0)
        items_layout.addWidget(self.item_table, 1)
        self.tabs.addTab(items_tab, "Item catalogue")

        self.receive_button.clicked.connect(lambda *_: self._receive())
        self.open_button.clicked.connect(lambda *_: self._open_lot())
        self.adjust_button.clicked.connect(lambda *_: self._adjust())
        self.discard_button.clicked.connect(lambda *_: self._discard())
        self.new_item_button.clicked.connect(lambda *_: self._new_item())
        self.edit_item_button.clicked.connect(lambda *_: self._edit_item())
        self.item_table.doubleClicked.connect(lambda *_: self._edit_item() if can_manage else None)
        self.kind_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        self.show_closed.toggled.connect(lambda *_: self.refresh())
        self.show_inactive.toggled.connect(lambda *_: self.refresh())
        self.lot_table.itemSelectionChanged.connect(self._lot_selected)
        self.item_table.itemSelectionChanged.connect(
            lambda: self.edit_item_button.setEnabled(self._selected(self.item_table, self.items) is not None))
        self._lot_selected()
        self.edit_item_button.setEnabled(False)

    @staticmethod
    def _selected(table, rows):
        model = table.selectionModel()
        idx = model.selectedRows() if model else []
        return rows[idx[0].row()] if idx and idx[0].row() < len(rows) else None

    def refresh(self) -> None:
        with self.db.session() as session:
            self.lots = inventory.list_lots(session, include_closed=self.show_closed.isChecked(),
                                            kind=self.kind_filter.currentData())
            self.items = inventory.list_items(session, include_inactive=self.show_inactive.isChecked())
            stock = {i.id: inventory.stock_on_hand(session, i) for i in self.items}
            self.paths = storage.paths(session)
            self.locations = storage.active_choices(session)
            self.active_items = inventory.list_items(session)
        today = date.today()
        self.lot_table.setRowCount(len(self.lots))
        for r, lot in enumerate(self.lots):
            values = [lot.internal_id, lot.item_name, lot.kind, lot.lot_number, f"{lot.quantity_on_hand} {lot.unit}",
                      lot.opened_on.isoformat() if lot.opened_on else "", lot.expiry.isoformat() if lot.expiry else "",
                      f"{lot.purity} %" if lot.purity else "", self.paths.get(lot.location_id, ""),
                      "; ".join(lot.problems) if lot.problems else lot.status]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 6 and lot.expiry:
                    item.setForeground(QColor(RED if lot.expiry < today else
                                              AMBER if lot.expiry <= today + timedelta(days=30) else GREEN))
                if c == 9:
                    item.setForeground(QColor(RED if lot.problems else GREEN if lot.status == "Available" else GREY))
                self.lot_table.setItem(r, c, item)
        self.item_table.setRowCount(len(self.items))
        for r, i in enumerate(self.items):
            on_hand = stock[i.id]
            low = i.min_stock is not None and on_hand < formulas.parse_decimal(i.min_stock)
            values = [i.name, i.kind, i.grade or "", i.supplier or "", i.catalogue_number or "", i.unit,
                      f"{formulas.format_decimal(on_hand) or '0'} {i.unit}", i.min_stock or "",
                      f"{i.days_after_opening} days" if i.days_after_opening else "",
                      ("LOW STOCK" if low else "Active") if i.is_active else "Inactive"]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c in (6, 9) and low:
                    item.setForeground(QColor(RED))
                self.item_table.setItem(r, c, item)
        self._lot_selected()

    def _lot_selected(self) -> None:
        lot = self._selected(self.lot_table, self.lots)
        self.open_button.setEnabled(lot is not None and lot.opened_on is None and lot.status != "Discarded")
        self.adjust_button.setEnabled(lot is not None and lot.status != "Discarded")
        self.discard_button.setEnabled(lot is not None and lot.status != "Discarded")
        if lot is None:
            self.tx_title.setText("<b>Stock movements</b> – select a lot")
            self.tx_table.setRowCount(0)
            return
        with self.db.session() as session:
            rows = inventory.transactions(session, lot.id)
        cert = f" · certificate {lot.certificate_ref}" if lot.certificate_ref else ""
        self.tx_title.setText(f"<b>Stock movements – {lot.internal_id}</b>  {lot.item_name}, lot {lot.lot_number}"
                              f", received {lot.received_on.isoformat()}{cert}")
        self.tx_table.setRowCount(len(rows))
        for r, t in enumerate(rows):
            values = [format_local(t.at), t.kind, t.quantity, t.balance_after,
                      f"#{t.analysis_id}" if t.analysis_id else "", t.reason or "", t.by]
            for c, value in enumerate(values):
                self.tx_table.setItem(r, c, QTableWidgetItem(value))

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    def _receive(self) -> None:
        if not self.active_items:
            show_error(self, "Add the item to the catalogue first (Item catalogue tab).", "No items")
            return
        dialog = ReceiveLotDialog(self.active_items, self.locations, self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: inventory.receive_lot(s, self.user, dialog.item.currentData(), **dialog.values())):
                return

    def _open_lot(self) -> None:
        lot = self._selected(self.lot_table, self.lots)
        if lot is not None:
            self._run(lambda s: inventory.open_lot(s, self.user, lot.id))

    def _adjust(self) -> None:
        lot = self._selected(self.lot_table, self.lots)
        if lot is None:
            return
        counted, ok = QInputDialog.getText(self, "Stock count", f"Counted quantity of {lot.internal_id} ({lot.unit}):",
                                           text=lot.quantity_on_hand)
        if not ok:
            return
        reason = ask_reason(self, "Stock count", f"{lot.internal_id}: {lot.quantity_on_hand} → {counted} {lot.unit}")
        if reason is not None:
            self._run(lambda s: inventory.adjust_stock(s, self.user, lot.id, counted, reason))

    def _discard(self) -> None:
        lot = self._selected(self.lot_table, self.lots)
        if lot is None:
            return
        reason = ask_reason(self, "Discard lot", f"Discard {lot.internal_id} ({lot.item_name}, lot {lot.lot_number})?")
        if reason is not None:
            self._run(lambda s: inventory.discard_lot(s, self.user, lot.id, reason))

    def _new_item(self) -> None:
        dialog = ItemDialog(self)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: inventory.create_item(s, self.user, dialog.details())):
                return

    def _edit_item(self) -> None:
        item = self._selected(self.item_table, self.items)
        if item is None:
            return
        dialog = ItemDialog(self, item)
        # If saving fails (or the reason is cancelled) the window comes back with what was typed.
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit item", f"Changing {item.name}.")
            if reason is not None and self._run(lambda s: inventory.update_item(
                    s, self.user, item.id, dialog.details(), dialog.active.isChecked(), reason)):
                return
