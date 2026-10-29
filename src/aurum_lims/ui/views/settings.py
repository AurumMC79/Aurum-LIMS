"""Settings page: security and laboratory details (Administrators) and Report a bug (everyone)."""

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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services.lab_info import PAPER_SIZES, LabInfo, get_lab_info, set_lab_info
from aurum_lims.services.settings import SETTING_INFO, get_security_settings, update_security_settings
from aurum_lims.ui.common import Card, ask_reason, muted_label, primary_button, show_error
from aurum_lims.ui.views.bug_report import BugReportTab


def _scrolling(*widgets: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    content = QWidget()
    content.setObjectName("Content")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(4, 12, 4, 12)
    layout.setSpacing(16)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch()
    scroll.setWidget(content)
    return scroll


class SettingsView(QWidget):
    title = "Settings"
    settings_changed = Signal()

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.current: dict[str, int] = {}
        self.is_admin = user.can(Permission.MANAGE_SETTINGS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        if self.is_admin:
            self.tabs.addTab(_scrolling(self._security_card(), self._storage_card()), "Security")
            self.tabs.addTab(_scrolling(self._lab_card()), "Laboratory details")
        self.bug_tab = BugReportTab(db, user)
        self.tabs.addTab(self.bug_tab, "Report a bug")

    # ----- building -----
    def _security_card(self) -> Card:
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
        save.clicked.connect(lambda *_: self._save())
        buttons.addWidget(save)
        card.body.addLayout(buttons)
        return card

    def _lab_card(self) -> Card:
        card = Card("Laboratory details (printed on certificates)")
        form = QFormLayout()
        self.lab_name = QLineEdit()
        self.lab_address = QPlainTextEdit()
        self.lab_address.setFixedHeight(64)
        self.lab_contact = QLineEdit()
        self.lab_contact.setPlaceholderText("e.g. Tel. +1 555 0100 · qc@yourlab.com")
        self.coa_statement = QPlainTextEdit()
        self.coa_statement.setFixedHeight(54)
        self.paper_size = QComboBox()
        self.paper_size.addItems(PAPER_SIZES)
        form.addRow("Laboratory name *", self.lab_name)
        form.addRow("Address", self.lab_address)
        form.addRow("Contact", self.lab_contact)
        form.addRow("Certificate statement", self.coa_statement)
        form.addRow("Paper size", self.paper_size)
        card.body.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save = primary_button("Save laboratory details")
        save.setDefault(False)
        save.clicked.connect(lambda *_: self._save_lab())
        buttons.addWidget(save)
        card.body.addLayout(buttons)
        return card

    def _storage_card(self) -> Card:
        card = Card("Data storage")
        card.body.addWidget(muted_label(f"Database: {self.db.url.removeprefix('sqlite:///')}"))
        card.body.addWidget(muted_label(
            "Back this file up regularly (with the app closed) according to your lab's backup procedure. "
            f"Log files are in {config.log_dir()}."
        ))
        return card

    # ----- data -----
    def refresh(self) -> None:
        self.bug_tab.refresh()
        if not self.is_admin:
            return
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
