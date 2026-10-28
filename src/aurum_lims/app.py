"""Application entry point and the sign-in / sign-out flow."""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import auth, sample_types, users
from aurum_lims.ui.change_password_dialog import ChangePasswordDialog
from aurum_lims.ui.common import app_icon, load_stylesheet
from aurum_lims.ui.first_run_dialog import FirstRunDialog
from aurum_lims.ui.login_dialog import LoginDialog
from aurum_lims.ui.main_window import MainWindow

log = logging.getLogger("aurum_lims")


class AppController(QObject):
    """Moves between first-run set-up, sign-in and the main window."""

    def __init__(self, app: QApplication, db: Database):
        super().__init__()
        self.app = app
        self.db = db
        self.window: MainWindow | None = None
        self.user: SessionUser | None = None

    def start(self) -> bool:
        with self.db.session() as session:
            first_run = not users.has_any_users(session)
        if first_run and FirstRunDialog(self.db).exec() != QDialog.Accepted:
            return False
        return self.sign_in()

    def sign_in(self, notice: str = "") -> bool:
        """Show the sign-in screen until someone signs in (True) or it is closed (False)."""
        while True:
            dialog = LoginDialog(self.db, notice=notice)
            if dialog.exec() != QDialog.Accepted:
                return False
            notice = ""
            result = dialog.result_value
            user = result.user
            if result.must_change_password or result.password_expired:
                reason = (
                    "You are signing in with a temporary password. Choose your own password to continue."
                    if result.must_change_password
                    else "Your password has expired. Choose a new password to continue."
                )
                if ChangePasswordDialog(self.db, user, forced_reason=reason).exec() != QDialog.Accepted:
                    with self.db.session() as session:
                        auth.logout(session, user, "password change not completed")
                    continue
            self._open_main_window(user)
            return True

    def _open_main_window(self, user: SessionUser) -> None:
        self.user = user
        self.window = MainWindow(self.db, user)
        self.window.signed_out.connect(self._on_signed_out)
        self.window.show()
        log.info("User %s signed in", user.username)

    def _on_signed_out(self, trigger: str, exit_app: bool) -> None:
        if self.user is not None:
            with self.db.session() as session:
                auth.logout(session, self.user, trigger)
            log.info("User %s signed out (%s)", self.user.username, trigger)
        self.user = None
        window, self.window = self.window, None
        # Close anything the old session left open (message boxes, dialogs).
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                widget.reject()
        if window is not None:
            window.hide()
            window.deleteLater()
        if exit_app:
            self.app.quit()
        else:
            QTimer.singleShot(0, self._return_to_sign_in)

    def _return_to_sign_in(self) -> None:
        if not self.sign_in():
            self.app.quit()


def _configure_logging() -> None:
    handler = RotatingFileHandler(config.log_dir() / "aurum.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _install_exception_hook() -> None:
    def hook(exc_type, exc, tb):
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        if QApplication.instance() is not None:
            box = QMessageBox(QMessageBox.Critical, config.APP_NAME, f"An unexpected error occurred:\n\n{exc}")
            box.setDetailedText(details)
            box.exec()

    sys.excepthook = hook


def main() -> int:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setOrganizationName(config.ORGANIZATION)
    app.setApplicationVersion(config.APP_VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    _install_exception_hook()

    try:
        db = Database(config.database_url())
        db.upgrade()
        with db.session() as session:
            sample_types.seed_defaults(session)
    except Exception as exc:  # noqa: BLE001 - shown to the user, then exit
        log.exception("Could not open the database")
        QMessageBox.critical(None, config.APP_NAME, f"Could not open the database:\n\n{exc}")
        return 1
    log.info("Aurum LIMS %s started, database %s", config.APP_VERSION, db.url)

    controller = AppController(app, db)
    if not controller.start():
        return 0
    exit_code = app.exec()
    db.dispose()
    return exit_code
