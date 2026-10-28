"""Smoke tests that build the real screens (offscreen) and click through them."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from aurum_lims.security.roles import Role
from aurum_lims.services import auth
from aurum_lims.ui.first_run_dialog import FirstRunDialog
from aurum_lims.ui.lock_screen import LockDialog
from aurum_lims.ui.login_dialog import LoginDialog
from aurum_lims.ui.main_window import MainWindow
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD


def test_first_run_creates_admin(qtbot, db, monkeypatch):
    monkeypatch.setattr("aurum_lims.ui.first_run_dialog.QMessageBox.information", lambda *a, **k: None)
    dialog = FirstRunDialog(db)
    qtbot.addWidget(dialog)
    dialog.username.setText("jmonahan")
    dialog.full_name.setText("Jeremy Monahan")
    dialog.password.setText("Gravimetric2026")
    dialog.confirm.setText("Gravimetric2026")
    dialog._submit()
    assert dialog.result() == QDialog.Accepted
    with db.session() as s:
        assert auth.authenticate(s, "jmonahan", "Gravimetric2026").ok


def test_login_dialog_success_and_failure(qtbot, db, admin):
    dialog = LoginDialog(db)
    qtbot.addWidget(dialog)
    dialog.username.setText("admin")
    dialog.password.setText("not-the-password-1")
    qtbot.mouseClick(dialog.sign_in, Qt.LeftButton)
    assert dialog.result() != QDialog.Accepted
    assert "Invalid" in dialog.error.text()

    dialog.password.setText(ADMIN_PASSWORD)
    qtbot.mouseClick(dialog.sign_in, Qt.LeftButton)
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_value.user.username == "admin"


def test_admin_main_window_has_admin_pages(qtbot, db, admin):
    window = MainWindow(db, admin)
    qtbot.addWidget(window)
    titles = [p.title for p in window.pages]
    assert {"Dashboard", "Users", "Audit Trail", "Settings", "My Account"} <= set(titles)
    for title in titles:
        assert window.show_page(title)  # each page builds and refreshes without error
    window.sign_out("test")


def test_analyst_main_window_hides_admin_pages(qtbot, db, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    window = MainWindow(db, analyst)
    qtbot.addWidget(window)
    titles = {p.title for p in window.pages}
    assert "Users" not in titles
    assert "Audit Trail" not in titles
    assert "Settings" not in titles
    window.sign_out("test")


def test_lock_dialog_unlocks_with_password(qtbot, db, make_user):
    analyst = make_user("jsmith")
    dialog = LockDialog(db, analyst)
    qtbot.addWidget(dialog)
    dialog.password.setText("wrong-password-1")
    dialog._unlock()
    assert dialog.result() != QDialog.Accepted
    dialog.password.setText(USER_PASSWORD)
    dialog._unlock()
    assert dialog.result() == QDialog.Accepted
