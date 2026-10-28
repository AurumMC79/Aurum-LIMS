"""Security settings page (Administrators only)."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services.lab_info import PAPER_SIZES, LabInfo, get_lab_info, set_lab_info
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("Content")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
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

        lab_card = Card("Laboratory details (printed on certificates)")
        lab_form = QFormLayout()
        self.lab_name = QLineEdit()
        self.lab_address = QPlainTextEdit()
        self.lab_address.setFixedHeight(64)
        self.lab_contact = QLineEdit()
        self.lab_contact.setPlaceholderText("e.g. Tel. +1 555 0100 · qc@yourlab.com")
        self.coa_statement = QPlainTextEdit()
        self.coa_statement.setFixedHeight(54)
        self.paper_size = QComboBox()
        self.paper_size.addItems(PAPER_SIZES)
        lab_form.addRow("Laboratory name *", self.lab_name)
        lab_form.addRow("Address", self.lab_address)
        lab_form.addRow("Contact", self.lab_contact)
        lab_form.addRow("Certificate statement", self.coa_statement)
        lab_form.addRow("Paper size", self.paper_size)
        lab_card.body.addLayout(lab_form)
        lab_buttons = QHBoxLayout()
        lab_buttons.addStretch()
        save_lab = primary_button("Save laboratory details")
        save_lab.setDefault(False)
        save_lab.clicked.connect(lambda *_: self._save_lab())
        lab_buttons.addWidget(save_lab)
        lab_card.body.addLayout(lab_buttons)
        layout.addWidget(lab_card)

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
            lab = get_lab_info(session)
        for key, spin in self.spins.items():
            spin.setValue(self.current[key])
        self.lab_name.setText(lab.lab_name)
        self.lab_address.setPlainText(lab.lab_address)
        self.lab_contact.setText(lab.lab_contact)
        self.coa_statement.setPlainText(lab.coa_statement)
        self.paper_size.setCurrentText(lab.paper_size)

    def _save_lab(self) -> None:
        info = LabInfo(self.lab_name.text(), self.lab_address.toPlainText(), self.lab_contact.text(),
                       self.coa_statement.toPlainText(), self.paper_size.currentText())
        reason = ask_reason(self, "Laboratory details", "Change the laboratory details printed on certificates.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                set_lab_info(session, self.user, info, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()
        QMessageBox.information(self, "Settings", "Laboratory details saved. New certificates will use them; "
                                                  "certificates already issued are not changed.")

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
