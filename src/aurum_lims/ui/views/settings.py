"""Security settings page (Administrators only)."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QMessageBox, QSpinBox, QVBoxLayout, QWidget

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services.settings import SETTING_INFO, get_security_settings, update_security_settings
from aurum_lims.ui.common import Card, ask_reason, muted_label, primary_button, show_error


class SettingsView(QWidget):
    title = "Settings"
    settings_changed = Signal()

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.current: dict[str, int] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        card = Card("Security")
        form = QFormLayout()
        form.setVerticalSpacing(4)
        self.spins: dict[str, QSpinBox] = {}
        for key, info in SETTING_INFO.items():
            spin = QSpinBox()
            spin.setRange(info.minimum, info.maximum)
            spin.setFixedWidth(110)
            self.spins[key] = spin
            form.addRow(info.label, spin)
            form.addRow("", muted_label(info.help))
        card.body.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save = primary_button("Save changes")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        card.body.addLayout(buttons)
        layout.addWidget(card)

        storage = Card("Data storage")
        storage.body.addWidget(muted_label(f"Database: {db.url.removeprefix('sqlite:///')}"))
        storage.body.addWidget(
            muted_label(
                "Back this file up regularly (with the app closed) according to your lab's backup procedure. "
                f"Log files are in {config.log_dir()}."
            )
        )
        layout.addWidget(storage)
        layout.addStretch()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.current = asdict(get_security_settings(session))
        for key, spin in self.spins.items():
            spin.setValue(self.current[key])

    def _save(self) -> None:
        changes = {key: spin.value() for key, spin in self.spins.items() if spin.value() != self.current.get(key)}
        if not changes:
            QMessageBox.information(self, "Settings", "Nothing was changed.")
            return
        summary = "\n".join(f"• {SETTING_INFO[k].label}: {self.current[k]} → {v}" for k, v in changes.items())
        reason = ask_reason(self, "Change settings", summary)
        if reason is None:
            return
        try:
            with self.db.session() as session:
                update_security_settings(session, self.user, changes, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()
        self.settings_changed.emit()
        QMessageBox.information(self, "Settings", "Settings saved.")
