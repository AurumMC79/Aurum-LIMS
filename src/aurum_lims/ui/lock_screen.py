"""Inactivity detection and the lock screen shown when it fires."""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from aurum_lims.db import Database
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import auth
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.auth import LoginStatus
from aurum_lims.ui.common import app_icon, error_label, logo_pixmap, muted_label, password_field, primary_button, show_label_message

ACTIVITY_EVENTS = {
    QEvent.KeyPress,
    QEvent.MouseButtonPress,
    QEvent.MouseMove,
    QEvent.Wheel,
    QEvent.TouchBegin,
}


class IdleWatcher(QObject):
    """Emits ``idle`` once no keyboard/mouse input has happened for the timeout."""

    idle = Signal()

    def __init__(self, app, timeout_minutes: int, parent=None):
        super().__init__(parent)
        self._app = app
        self._timeout_ms = timeout_minutes * 60_000
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._paused = False
        self._check = QTimer(self)
        self._check.setInterval(5_000)
        self._check.timeout.connect(self._tick)
        app.installEventFilter(self)
        self._check.start()

    def set_timeout(self, minutes: int) -> None:
        self._timeout_ms = minutes * 60_000

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._elapsed.restart()

    def stop(self) -> None:
        self._check.stop()
        self._app.removeEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in ACTIVITY_EVENTS:
            self._elapsed.restart()
        return False

    def _tick(self) -> None:
        if not self._paused and self._elapsed.elapsed() >= self._timeout_ms:
            self._paused = True
            self.idle.emit()


class LockDialog(QDialog):
    """Accepted = unlocked by the same user; Rejected = sign out."""

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.locked_out = False
        self.setWindowTitle("Session locked")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(48))
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)
        title = QLabel("Session locked")
        title.setObjectName("LoginTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        info = muted_label(
            f"Aurum LIMS is locked.\n{user.full_name}, enter your password to continue."
        )
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        self.password = password_field("Password")
        self.password.returnPressed.connect(self._unlock)
        layout.addWidget(self.password)
        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(self.reject)
        unlock = primary_button("Unlock")
        unlock.clicked.connect(self._unlock)
        buttons.addWidget(sign_out)
        buttons.addStretch()
        buttons.addWidget(unlock)
        layout.addLayout(buttons)

    def keyPressEvent(self, event) -> None:
        # Escape would otherwise sign the user out by accident.
        if event.key() != Qt.Key_Escape:
            super().keyPressEvent(event)

    def _unlock(self) -> None:
        with self.db.session() as session:
            result = auth.reauthenticate(
                session,
                self.user.id,
                self.password.text(),
                failure_action=AuditAction.SESSION_UNLOCK_FAILED,
                success_action=AuditAction.SESSION_UNLOCKED,
            )
        self.password.clear()
        if result.ok:
            self.accept()
        elif result.status is LoginStatus.LOCKED:
            self.locked_out = True
            show_label_message(self.error, result.message)
            self.password.setEnabled(False)
            QTimer.singleShot(2500, self.reject)
        else:
            show_label_message(self.error, result.message)
