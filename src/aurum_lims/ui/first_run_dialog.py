"""First launch: create the initial Administrator account."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QFrame, QLineEdit, QMessageBox, QVBoxLayout

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.passwords import policy_description
from aurum_lims.services import users
from aurum_lims.services.settings import get_security_settings
from aurum_lims.ui.common import app_icon, error_label, muted_label, password_field, primary_button, show_label_message
from aurum_lims.ui.login_dialog import branded_header


class FirstRunDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(f"{config.APP_NAME} — First-time set-up")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(480)
        with db.session() as session:
            min_length = get_security_settings(session).password_min_length

        card = QFrame()
        card.setObjectName("LoginCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(10)
        for widget in branded_header("Welcome! Create the first Administrator account."):
            layout.addWidget(widget)
        layout.addWidget(
            muted_label(
                "The Administrator manages user accounts and settings. "
                "Each person in the lab should then get their own account — accounts are never shared."
            )
        )

        self.username = QLineEdit()
        self.username.setPlaceholderText("e.g. jmonahan")
        self.full_name = QLineEdit()
        self.full_name.setPlaceholderText("As it should appear on signatures")
        self.password = password_field()
        self.confirm = password_field()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Username", self.username)
        form.addRow("Full name", self.full_name)
        form.addRow("Password", self.password)
        form.addRow("Confirm password", self.confirm)
        layout.addLayout(form)
        layout.addWidget(muted_label(policy_description(min_length)))

        self.error = error_label()
        layout.addWidget(self.error)
        create = primary_button("Create Administrator")
        create.clicked.connect(self._submit)
        layout.addWidget(create)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(card)

    def _submit(self) -> None:
        if self.password.text() != self.confirm.text():
            show_label_message(self.error, "The passwords do not match.")
            return
        try:
            with self.db.session() as session:
                user = users.create_initial_admin(
                    session, self.username.text(), self.full_name.text(), self.password.text()
                )
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        QMessageBox.information(
            self,
            "Administrator created",
            f"The Administrator account '{user.username}' was created.\n\nSign in with it to add the rest of your team.",
        )
        self.accept()
