"""Change your own password (voluntarily, or forced after a reset/expiry)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.passwords import policy_description
from aurum_lims.services import auth
from aurum_lims.services.settings import get_security_settings
from aurum_lims.ui.common import app_icon, error_label, muted_label, password_field, show_label_message


class ChangePasswordDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, parent=None, *, forced_reason: str = ""):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.setWindowTitle("Change password")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(440)
        with db.session() as session:
            min_length = get_security_settings(session).password_min_length

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        if forced_reason:
            banner = QLabel(forced_reason)
            banner.setObjectName("Banner")
            banner.setWordWrap(True)
            layout.addWidget(banner)

        self.current = password_field()
        self.new = password_field()
        self.confirm = password_field()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Signed in as", QLabel(f"{user.full_name} ({user.username})"))
        form.addRow("Current password", self.current)
        form.addRow("New password", self.new)
        form.addRow("Confirm new password", self.confirm)
        layout.addLayout(form)
        layout.addWidget(muted_label(policy_description(min_length)))
        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = buttons.button(QDialogButtonBox.Ok)
        ok.setText("Change password")
        ok.setProperty("primary", True)
        if forced_reason:
            buttons.button(QDialogButtonBox.Cancel).setText("Sign out")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        if self.new.text() != self.confirm.text():
            show_label_message(self.error, "The new passwords do not match.")
            return
        try:
            with self.db.session() as session:
                auth.change_password(session, self.user.id, self.current.text(), self.new.text())
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()
