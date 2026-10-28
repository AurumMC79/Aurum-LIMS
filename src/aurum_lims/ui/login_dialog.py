"""Sign-in screen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QFrame, QLabel, QLineEdit, QVBoxLayout

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.services import auth
from aurum_lims.services.auth import LoginResult
from aurum_lims.ui.common import (
    app_icon,
    error_label,
    logo_pixmap,
    muted_label,
    password_field,
    primary_button,
    show_label_message,
)


def branded_header(subtitle: str) -> list:
    logo = QLabel()
    logo.setPixmap(logo_pixmap(64))
    logo.setAlignment(Qt.AlignCenter)
    title = QLabel(config.APP_NAME)
    title.setObjectName("LoginTitle")
    title.setAlignment(Qt.AlignCenter)
    sub = muted_label(subtitle)
    sub.setAlignment(Qt.AlignCenter)
    return [logo, title, sub]


class LoginDialog(QDialog):
    def __init__(self, db: Database, parent=None, notice: str = ""):
        super().__init__(parent)
        self.db = db
        self.result_value: LoginResult | None = None
        self.setWindowTitle(f"{config.APP_NAME} — Sign in")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(420)

        card = QFrame()
        card.setObjectName("LoginCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(10)
        for widget in branded_header("Laboratory Information Management System"):
            layout.addWidget(widget)
        layout.addSpacing(8)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        self.password = password_field("Password")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        layout.addLayout(form)

        self.notice = muted_label(notice)
        self.notice.setVisible(bool(notice))
        layout.addWidget(self.notice)
        self.error = error_label()
        layout.addWidget(self.error)

        self.sign_in = primary_button("Sign in")
        self.sign_in.clicked.connect(self._submit)
        layout.addWidget(self.sign_in)

        footer = muted_label(f"Version {config.APP_VERSION} · All sign-in attempts are recorded in the audit trail.")
        footer.setAlignment(Qt.AlignCenter)
        layout.addSpacing(6)
        layout.addWidget(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(card)

        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self._submit)
        self.username.setFocus()

    def _submit(self) -> None:
        username, password = self.username.text(), self.password.text()
        if not username.strip() or not password:
            show_label_message(self.error, "Enter your username and password.")
            return
        with self.db.session() as session:
            result = auth.authenticate(session, username, password)
        self.password.clear()
        if result.ok:
            self.result_value = result
            self.accept()
        else:
            show_label_message(self.error, result.message)
            self.password.setFocus()
