"""Generic electronic-signature dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGroupBox, QVBoxLayout, QWidget

from aurum_lims.errors import AurumError
from aurum_lims.services.esign import SignatureResult
from aurum_lims.ui.common import error_label, muted_label, password_field, show_label_message


class SignDialog(QDialog):
    """Asks for the password and runs ``action(password)``; stays open on a
    wrong password so the user can retry (each failure is audited)."""

    def __init__(self, parent: QWidget | None, title: str, statement: str,
                 action: Callable[[str], SignatureResult], ok_text: str = "Sign", extra: QWidget | None = None):
        super().__init__(parent)
        self.action = action
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        if extra is not None:
            layout.addWidget(extra)
        box = QGroupBox("Electronic signature")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(muted_label(statement))
        self.password = password_field("Your password")
        box_layout.addWidget(self.password)
        layout.addWidget(box)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(ok_text)
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._sign)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.password.setFocus()

    def _sign(self) -> None:
        try:
            result = self.action(self.password.text())
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        finally:
            self.password.clear()
        if not result.ok:
            show_label_message(self.error, result.message)
            return
        self.accept()
