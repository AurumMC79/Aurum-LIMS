"""Nothing but the real windows may appear while the app starts or pages are opened.

Showing a control before it is placed on a page makes it flash up as a tiny window of its own (dozens did at
start-up once the Master Administrator could use every button). ``common.allow`` prevents that.
"""

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from aurum_lims.security.roles import Role
from aurum_lims.ui.common import allow
from aurum_lims.ui.login_dialog import LoginDialog
from aurum_lims.ui.main_window import MainWindow


class _WindowSpy(QObject):
    def __init__(self):
        super().__init__()
        self.shown: list[str] = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.shown.append(type(watched).__name__)
        return False


@pytest.fixture
def spy():
    spy = _WindowSpy()
    QApplication.instance().installEventFilter(spy)
    yield spy
    QApplication.instance().removeEventFilter(spy)


@pytest.mark.parametrize("role", [None, Role.ANALYST, Role.QA_REVIEWER, Role.VIEWER])
def test_only_the_main_window_appears(qtbot, db, admin, make_user, spy, role):
    user = admin if role is None else make_user(f"user.{role.value}", role)
    spy.shown.clear()
    window = MainWindow(db, user)
    qtbot.addWidget(window)
    window._signing_out = True
    window.show()
    for page in window.pages:
        window.show_page(page.title)
    assert spy.shown == ["MainWindow"]


def test_sign_in_notice_does_not_flash(qtbot, db, admin, spy):
    dialog = LoginDialog(db, notice="Aurum LIMS was reloaded. Please sign in again.")
    qtbot.addWidget(dialog)
    assert spy.shown == []
    dialog.show()
    assert spy.shown == ["LoginDialog"] and dialog.notice.isVisibleTo(dialog)


def test_allow_helper(qtbot):
    loose = QPushButton("not on a page yet")
    allow(loose, True)
    assert not loose.isVisible()  # left alone: it appears with the page it is added to
    allow(loose, False)
    assert loose.isHidden()
    page = QWidget()
    qtbot.addWidget(page)
    placed = QPushButton("on a page", page)
    page.show()
    allow(placed, False)
    assert not placed.isVisible()
    allow(placed, True)
    assert placed.isVisible()
