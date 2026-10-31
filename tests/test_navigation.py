"""Grouped sidebar navigation with flyout menus."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from aurum_lims.security.roles import Role
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.navigation import NAV_GROUPS, OTHER, NavigationPanel


def _window(qtbot, db, user):
    window = MainWindow(db, user)
    qtbot.addWidget(window)
    window._signing_out = True  # close quietly at the end
    window.show()
    return window


def test_every_page_has_a_heading(qtbot, db, admin):
    window = _window(qtbot, db, admin)
    grouped = {t for _, members in NAV_GROUPS for t in members}
    assert {p.title for p in window.pages} <= grouped
    assert [name for name, _ in window.nav.groups] == [name for name, _ in NAV_GROUPS]


def test_headings_follow_the_role(qtbot, db, admin, make_user):
    viewer = _window(qtbot, db, make_user("viewer1", Role.VIEWER))
    groups = dict(viewer.nav.groups)
    assert "Setup" not in groups  # nothing in it a viewer may use
    assert [t for _, t in groups["Lab work"]] == ["Samples", "Testing", "OOS", "Certificates"]


def test_choosing_pages_from_the_menu(qtbot, db, admin):
    window = _window(qtbot, db, admin)
    nav = window.nav
    assert window.page_title.text() == "Dashboard" and nav.headings["Dashboard"].isChecked()
    nav.headings["Lab work"].click()  # clicking opens the menu and keeps it open
    assert nav.flyout.isVisible() and nav.flyout.group == "Lab work" and nav.pinned
    [samples] = [b for b in nav.flyout.buttons if b.text() == "Samples"]
    samples.click()
    assert window.page_title.text() == "Samples" and not nav.flyout.isVisible()
    assert nav.headings["Lab work"].isChecked() and not nav.headings["Dashboard"].isChecked()
    assert nav.current_labels["Lab work"].text() == "Samples"
    nav.headings["Dashboard"].click()  # a single-page heading opens its page directly
    assert window.page_title.text() == "Dashboard" and not nav.current_labels["Lab work"].isVisible()


def test_menu_closes_with_escape_or_a_click_elsewhere(qtbot, db, admin):
    window = _window(qtbot, db, admin)
    nav = window.nav
    nav.headings["Setup"].click()
    assert nav.flyout.isVisible()
    QApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not nav.flyout.isVisible()
    nav.headings["Setup"].click()
    qtbot.mouseClick(window.page_title, Qt.LeftButton)
    assert not nav.flyout.isVisible()
    nav.headings["Setup"].click()
    nav.headings["Setup"].click()  # clicking the open heading again closes it
    assert not nav.flyout.isVisible() and not nav.headings["Setup"].isChecked()


def test_hovering_switches_between_menus(qtbot, db, admin):
    window = _window(qtbot, db, admin)
    nav = window.nav
    nav._hovered("Lab work")
    qtbot.waitUntil(lambda: nav.flyout.isVisible(), timeout=1000)
    assert nav.flyout.group == "Lab work" and not nav.pinned
    nav._hovered("Setup")  # moving to another heading switches straight away
    assert nav.flyout.group == "Setup"


def test_show_page_highlights_the_heading(qtbot, db, admin):
    window = _window(qtbot, db, admin)
    assert window.show_page("Audit Trail")
    assert window.nav.headings["Quality & reports"].isChecked()
    assert window.nav.headings["Quality & reports"].text() == "Quality && reports"  # shown as "Quality & reports"


def test_unknown_pages_go_under_more(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    nav = NavigationPanel(["Dashboard", "Samples", "Something new"], host)
    assert [name for name, _ in nav.groups] == ["Dashboard", "Lab work", OTHER]
