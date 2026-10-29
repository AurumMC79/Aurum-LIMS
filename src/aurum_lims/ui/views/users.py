"""User management page (Administrators only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.passwords import generate_temporary_password
from aurum_lims.security.roles import ROLE_DESCRIPTIONS, Role
from aurum_lims.services import users
from aurum_lims.ui.common import (
    GridTable,
    ask_reason,
    danger_button,
    error_label,
    format_local,
    muted_label,
    primary_button,
    show_error,
    show_label_message,
)


@dataclass(frozen=True)
class UserRow:
    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    is_locked: bool
    last_login_at: datetime | None
    created_at: datetime

    @property
    def status(self) -> str:
        if not self.is_active:
            return "Inactive"
        if self.is_locked:
            return "Locked"
        return "Active"


class UserDialog(QDialog):
    """Create a new user, or edit an existing user's name and role."""

    def __init__(self, parent=None, existing: UserRow | None = None):
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle("Edit user" if existing else "New user")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        self.username = QLineEdit(existing.username if existing else "")
        self.username.setEnabled(existing is None)
        self.full_name = QLineEdit(existing.full_name if existing else "")
        self.role = QComboBox()
        for role in Role:
            self.role.addItem(role.label, role)
        if existing:
            self.role.setCurrentIndex(list(Role).index(existing.role))
        else:
            self.role.setCurrentIndex(list(Role).index(Role.ANALYST))
        self.role_help = muted_label("")
        self.role.currentIndexChanged.connect(self._update_role_help)

        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Full name", self.full_name)
        form.addRow("Role", self.role)
        form.addRow("", self.role_help)

        self.password = QLineEdit()
        if existing is None:
            self.password.setText(generate_temporary_password())
            regenerate = QPushButton("Generate")
            regenerate.clicked.connect(lambda: self.password.setText(generate_temporary_password()))
            row = QHBoxLayout()
            row.addWidget(self.password)
            row.addWidget(regenerate)
            form.addRow("Temporary password", row)
            form.addRow("", muted_label("Give this to the person. They must choose their own password at first sign-in."))
        layout.addLayout(form)
        self._update_role_help()

        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save" if existing else "Create user")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_role_help(self) -> None:
        self.role_help.setText(ROLE_DESCRIPTIONS[self.role.currentData()])

    def _validate(self) -> None:
        if not self.full_name.text().strip():
            show_label_message(self.error, "Full name is required.")
            return
        if self.existing is None and not self.username.text().strip():
            show_label_message(self.error, "Username is required.")
            return
        self.accept()

    def show_error(self, message: str) -> None:
        show_label_message(self.error, message)


class UsersView(QWidget):
    title = "Users"
    COLUMNS = ["Username", "Full name", "Role", "Status", "Last sign-in", "Created"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows: list[UserRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(
            muted_label(
                "Every person needs their own account. Accounts are never deleted — deactivate an account "
                "when someone leaves so their past work stays attributable to them."
            )
        )

        toolbar = QHBoxLayout()
        self.new_button = primary_button("New user")
        self.edit_button = QPushButton("Edit")
        self.reset_button = QPushButton("Reset password")
        self.unlock_button = QPushButton("Unlock")
        self.toggle_button = danger_button("Deactivate")
        for button in (self.new_button, self.edit_button, self.reset_button, self.unlock_button, self.toggle_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.table = GridTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        layout.addWidget(self.table)

        self.new_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.reset_button.clicked.connect(self._reset_password)
        self.unlock_button.clicked.connect(self._unlock)
        self.toggle_button.clicked.connect(self._toggle_active)
        self._update_buttons()

    # ----- data -----
    def refresh(self) -> None:
        selected = self._selected()
        with self.db.session() as session:
            self.rows = [
                UserRow(u.id, u.username, u.full_name, Role(u.role), u.is_active, u.is_locked, u.last_login_at, u.created_at)
                for u in users.list_users(session, self.user)
            ]
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            values = [row.username, row.full_name, row.role.label, row.status,
                      format_local(row.last_login_at), format_local(row.created_at)]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 3 and row.status != "Active":
                    item.setForeground(QColor("#b42318" if row.status == "Locked" else "#8b98a8"))
                self.table.setItem(r, c, item)
        if selected:
            for r, row in enumerate(self.rows):
                if row.id == selected.id:
                    self.table.selectRow(r)
        self._update_buttons()

    def _selected(self) -> UserRow | None:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes or indexes[0].row() >= len(self.rows):
            return None
        return self.rows[indexes[0].row()]

    def _update_buttons(self) -> None:
        row = self._selected()
        self.edit_button.setEnabled(row is not None)
        self.reset_button.setEnabled(row is not None and row.is_active)
        self.unlock_button.setEnabled(row is not None and row.is_active and row.is_locked)
        self.toggle_button.setEnabled(row is not None and row.id != self.user.id)
        self.toggle_button.setText("Reactivate" if row is not None and not row.is_active else "Deactivate")

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    # ----- actions -----
    def _create(self) -> None:
        dialog = UserDialog(self)
        while dialog.exec() == QDialog.Accepted:
            password = dialog.password.text()
            try:
                with self.db.session() as session:
                    created = users.create_user(
                        session, self.user, username=dialog.username.text(), full_name=dialog.full_name.text(),
                        role=dialog.role.currentData(), temporary_password=password,
                    )
                    username = created.username
            except AurumError as exc:
                dialog.show_error(str(exc))
                continue
            self.refresh()
            self._show_temporary_password(username, password)
            return

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = UserDialog(self, existing=row)
        # If saving fails (or the reason is cancelled) the window comes back with what was typed.
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit user", f"Changing {row.username}.")
            if reason is not None and self._run(lambda s: users.update_user(
                s, self.user, row.id, full_name=dialog.full_name.text(), role=dialog.role.currentData(), reason=reason
            )):
                return

    def _reset_password(self) -> None:
        row = self._selected()
        if row is None:
            return
        reason = ask_reason(self, "Reset password", f"Give {row.full_name} ({row.username}) a new temporary password.")
        if reason is None:
            return
        password = generate_temporary_password()
        if self._run(lambda s: users.reset_password(s, self.user, row.id, password, reason)):
            self._show_temporary_password(row.username, password)

    def _unlock(self) -> None:
        row = self._selected()
        if row is None:
            return
        reason = ask_reason(self, "Unlock account", f"Unlock {row.full_name} ({row.username}).")
        if reason is not None:
            self._run(lambda s: users.unlock_user(s, self.user, row.id, reason))

    def _toggle_active(self) -> None:
        row = self._selected()
        if row is None:
            return
        if row.is_active:
            reason = ask_reason(
                self, "Deactivate account",
                f"Deactivate {row.full_name} ({row.username}). They will no longer be able to sign in.",
            )
            if reason is not None:
                self._run(lambda s: users.deactivate_user(s, self.user, row.id, reason))
        else:
            reason = ask_reason(self, "Reactivate account", f"Reactivate {row.full_name} ({row.username}).")
            if reason is not None:
                self._run(lambda s: users.reactivate_user(s, self.user, row.id, reason))

    def _show_temporary_password(self, username: str, password: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Temporary password")
        box.setIcon(QMessageBox.Information)
        box.setText(
            f"Temporary password for <b>{username}</b>:<br><br>"
            f"<span style='font-family: Consolas, monospace; font-size: 14pt'>{password}</span><br><br>"
            "Give it to the person privately. They will be asked to choose their own password when they sign in. "
            "This password will not be shown again."
        )
        copy = box.addButton("Copy to clipboard", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(password)
