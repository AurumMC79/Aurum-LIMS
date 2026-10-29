"""Smoke tests for the SOP Index page and the Settings / Report a bug tabs."""

from PySide6.QtGui import QGuiApplication, QImage

from aurum_lims.security.roles import Role
from aurum_lims.services import bug_reports
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.views.documents import DocumentsView
from aurum_lims.ui.views.settings import SettingsView
from tests.test_documents import _sop


def test_sop_index_tree_and_permissions(qtbot, db, admin, make_user):
    sop = _sop(db, admin)
    _sop(db, admin, "FORM-QC-101-01", parent_id=sop, doc_type="Form")
    analyst = make_user("jsmith", Role.ANALYST)
    view = DocumentsView(db, analyst)
    qtbot.addWidget(view)
    view.refresh()
    assert view.tree.topLevelItemCount() == 1 and view.tree.topLevelItem(0).childCount() == 1
    assert view.add_button.isHidden() and view.revision_button.isHidden()
    view.search.setText("nothing-matches")
    assert view.tree.topLevelItemCount() == 0

    manager_view = DocumentsView(db, admin)
    qtbot.addWidget(manager_view)
    manager_view.refresh()
    assert not manager_view.add_button.isHidden()


def test_settings_tabs_by_role(qtbot, db, admin, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    analyst_settings = SettingsView(db, analyst)
    qtbot.addWidget(analyst_settings)
    assert [analyst_settings.tabs.tabText(i) for i in range(analyst_settings.tabs.count())] == ["Report a bug"]
    admin_settings = SettingsView(db, admin)
    qtbot.addWidget(admin_settings)
    admin_settings.refresh()
    assert [admin_settings.tabs.tabText(i) for i in range(admin_settings.tabs.count())] == \
        ["Security", "Laboratory details", "Report a bug"]


def test_report_a_bug_with_pasted_screenshot(qtbot, db, admin, make_user, monkeypatch):
    analyst = make_user("jsmith", Role.ANALYST)
    monkeypatch.setattr("aurum_lims.ui.views.bug_report.QMessageBox.information", lambda *a, **k: None)
    settings = SettingsView(db, analyst)
    qtbot.addWidget(settings)
    tab = settings.bug_tab
    image = QImage(40, 20, QImage.Format_RGB32)
    image.fill(0x9A7B1C)
    QGuiApplication.clipboard().setImage(image)
    tab._paste()
    assert tab.screenshot and tab.screenshot.startswith(b"\x89PNG")
    tab.title_edit.setText("Cannot open SOP")
    tab.area.setCurrentText("SOP index")
    tab.description.setPlainText("Double-clicking an SOP does nothing.")
    tab._submit()
    assert tab.error.text() == "" and tab.table.rowCount() == 1
    with db.session() as s:
        [row] = bug_reports.list_reports(s, admin)
        assert row.area == "SOP index" and row.has_screenshot


def test_everyone_sees_sop_index_and_settings(qtbot, db, admin, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    window = MainWindow(db, viewer)
    qtbot.addWidget(window)
    titles = [p.title for p in window.pages]
    assert {"SOP Index", "Settings"} <= set(titles)
    for title in titles:
        assert window.show_page(title)
    window.sign_out("test")
