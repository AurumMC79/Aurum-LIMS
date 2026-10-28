"""My Account page: details of the signed-in user and password change."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from aurum_lims.db import Database
from aurum_lims.db.models import User
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import ROLE_PERMISSIONS
from aurum_lims.services import auth
from aurum_lims.ui.change_password_dialog import ChangePasswordDialog
from aurum_lims.ui.common import Card, format_local, muted_label, primary_button


class AccountView(QWidget):
    title = "My Account"

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = Card("Account details")
        form = QFormLayout()
        self.labels = {name: QLabel() for name in ("username", "full_name", "role", "created", "changed", "expires")}
        form.addRow("Username", self.labels["username"])
        form.addRow("Full name", self.labels["full_name"])
        form.addRow("Role", self.labels["role"])
        form.addRow("Account created", self.labels["created"])
        form.addRow("Password last changed", self.labels["changed"])
        form.addRow("Password expires", self.labels["expires"])
        card.body.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        change = primary_button("Change password")
        change.setDefault(False)
        change.clicked.connect(self._change_password)
        row.addWidget(change)
        card.body.addLayout(row)
        layout.addWidget(card)

        perms = Card("What your role can do")
        names = sorted(p.value.replace("_", " ").capitalize() for p in ROLE_PERMISSIONS[user.role])
        perms.body.addWidget(muted_label("\n".join(f"• {n}" for n in names)))
        perms.body.addWidget(muted_label("Your name and role are managed by an Administrator."))
        layout.addWidget(perms)
        layout.addStretch()

    def refresh(self) -> None:
        with self.db.session() as session:
            me = session.get(User, self.user.id)
            expires = auth.password_expires_in_days(session, self.user.id)
            values = {
                "username": me.username,
                "full_name": me.full_name,
                "role": self.user.role.label,
                "created": format_local(me.created_at),
                "changed": format_local(me.password_changed_at),
                "expires": "Never" if expires is None else f"In {expires} day(s)",
            }
        for key, value in values.items():
            self.labels[key].setText(value)

    def _change_password(self) -> None:
        if ChangePasswordDialog(self.db, self.user, self).exec() == QDialog.Accepted:
            QMessageBox.information(self, "Password changed", "Your password has been changed.")
            self.refresh()
