"""Storage page: the tree of storage locations and what is stored in each."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import samples, storage
from aurum_lims.services.storage import LocationKind
from aurum_lims.ui.common import (
    GridTable,
    ask_reason,
    error_label,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)


class LocationDialog(QDialog):
    def __init__(self, choices: list[tuple[int, str]], parent=None, *, existing=None, parent_id=None):
        super().__init__(parent)
        self.setWindowTitle("Edit location" if existing else "New storage location")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(existing.name if existing else "")
        self.name.setPlaceholderText("e.g. Fridge 2")
        self.kind = QComboBox()
        for kind in LocationKind:
            self.kind.addItem(kind.value)
        self.kind.setCurrentText(existing.kind if existing else LocationKind.REFRIGERATOR.value)
        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(top level)", None)
        selected_parent = existing.parent_id if existing else parent_id
        for location_id, path in choices:
            if existing and location_id == existing.id:
                continue
            self.parent_combo.addItem(path, location_id)
            if location_id == selected_parent:
                self.parent_combo.setCurrentIndex(self.parent_combo.count() - 1)
        self.condition = QLineEdit(existing.condition or "" if existing else "")
        self.condition.setPlaceholderText("e.g. 2-8 °C, -20 °C, 25 °C/60% RH, ambient")
        form.addRow("Name *", self.name)
        form.addRow("Kind", self.kind)
        form.addRow("Inside", self.parent_combo)
        form.addRow("Storage condition", self.condition)
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

    def values(self) -> dict:
        return {
            "name": self.name.text(),
            "kind": self.kind.currentText(),
            "parent_id": self.parent_combo.currentData(),
            "condition": self.condition.text(),
        }


class StorageView(QWidget):
    title = "Storage"

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.locations = {}
        can_manage = user.can(Permission.MANAGE_MASTER_DATA)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "Build your storage as a tree, e.g. QC Lab › Fridge 1 › Shelf 2. Select a location to see what is stored there."
        ))

        toolbar = QHBoxLayout()
        self.add_button = primary_button("New location")
        self.add_child_button = QPushButton("New location inside")
        self.edit_button = QPushButton("Edit")
        self.toggle_button = QPushButton("Deactivate")
        for button in (self.add_button, self.add_child_button, self.edit_button, self.toggle_button):
            button.setVisible(can_manage)
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Location", "Kind", "Condition", "Samples"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        self.location_title = QLabel("Select a location")
        self.location_title.setObjectName("CardTitle")
        right_layout.addWidget(self.location_title)
        self.samples_table = GridTable(0, 4)
        self.samples_table.setHorizontalHeaderLabels(["Sample ID", "Type", "Material / point", "Status"])
        self.samples_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.samples_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.samples_table.verticalHeader().hide()
        self.samples_table.setAlternatingRowColors(True)
        self.samples_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.samples_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        right_layout.addWidget(self.samples_table)
        splitter.addWidget(right)
        splitter.setSizes([620, 420])
        layout.addWidget(splitter, 1)

        self.add_button.clicked.connect(lambda *_: self._add(None))
        self.add_child_button.clicked.connect(lambda *_: self._add(self._selected_id()))
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.toggle_button.clicked.connect(lambda *_: self._toggle())
        self._update_buttons()

    def refresh(self) -> None:
        selected = self._selected_id()
        with self.db.session() as session:
            rows = storage.list_locations(session)
            counts = storage.sample_counts(session)
            self.paths = storage.paths(session)
            self.choices = storage.active_choices(session)
        self.locations = {loc.id: loc for loc in rows}
        self.tree.clear()
        items: dict[int, QTreeWidgetItem] = {}
        pending = sorted(rows, key=lambda loc: self.paths[loc.id].lower())
        for loc in pending:  # sorted by path, so parents always come first
            item = QTreeWidgetItem([
                loc.name + ("" if loc.is_active else " (inactive)"), loc.kind, loc.condition or "",
                str(counts.get(loc.id, "")),
            ])
            item.setData(0, Qt.UserRole, loc.id)
            if not loc.is_active:
                for col in range(4):
                    item.setForeground(col, QColor("#8b98a8"))
            parent = items.get(loc.parent_id)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            items[loc.id] = item
        self.tree.expandAll()
        if selected in items:
            self.tree.setCurrentItem(items[selected])
        self._selection_changed()

    def _selected_id(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item and item.isSelected() else None

    def _update_buttons(self) -> None:
        location = self.locations.get(self._selected_id())
        self.add_child_button.setEnabled(location is not None and location.is_active)
        self.edit_button.setEnabled(location is not None)
        self.toggle_button.setEnabled(location is not None)
        self.toggle_button.setText("Reactivate" if location is not None and not location.is_active else "Deactivate")

    def _selection_changed(self) -> None:
        self._update_buttons()
        location_id = self._selected_id()
        if location_id is None:
            self.location_title.setText("Select a location")
            self.samples_table.setRowCount(0)
            return
        with self.db.session() as session:
            rows = samples.search(session, self.user, location_id=location_id)
        self.location_title.setText(f"{self.paths.get(location_id, '')} — {len(rows)} sample(s)")
        self.samples_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate([row.sample_id, row.type_name, row.material_name or "", row.status]):
                self.samples_table.setItem(r, c, QTableWidgetItem(value))

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    def _add(self, parent_id: int | None) -> None:
        dialog = LocationDialog(self.choices, self, parent_id=parent_id)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: storage.create_location(s, self.user, **dialog.values())):
                return

    def _edit(self) -> None:
        location = self.locations.get(self._selected_id())
        if location is None:
            return
        dialog = LocationDialog(self.choices, self, existing=location)
        # If saving fails (or the reason is cancelled) the window comes back with what was typed.
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit location", f"Changing {self.paths.get(location.id)}.")
            if reason is not None and self._run(
                    lambda s: storage.update_location(s, self.user, location.id, reason=reason, **dialog.values())):
                return

    def _toggle(self) -> None:
        location = self.locations.get(self._selected_id())
        if location is None:
            return
        activate = not location.is_active
        verb = "Reactivate" if activate else "Deactivate"
        reason = ask_reason(self, f"{verb} location", f"{verb} {self.paths.get(location.id)}.")
        if reason is not None:
            self._run(lambda s: storage.set_active(s, self.user, location.id, activate, reason))
