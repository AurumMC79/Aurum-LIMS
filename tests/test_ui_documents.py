"""Smoke tests for the SOP Index page and the Settings / Report a bug tabs."""

from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QDialog

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


def _paste_image(tab, colour: int) -> None:
    image = QImage(40, 20, QImage.Format_RGB32)
    image.fill(colour)
    QGuiApplication.clipboard().setImage(image)
    tab._paste()


def test_report_a_bug_with_several_pasted_screenshots(qtbot, db, admin, make_user, monkeypatch):
    """BUG-0004: pasting a second screenshot used to replace the first."""
    analyst = make_user("jsmith", Role.ANALYST)
    monkeypatch.setattr("aurum_lims.ui.views.bug_report.QMessageBox.information", lambda *a, **k: None)
    settings = SettingsView(db, analyst)
    qtbot.addWidget(settings)
    tab = settings.bug_tab
    for colour in (0x9A7B1C, 0x1C2430, 0x067647):
        _paste_image(tab, colour)
    assert len(tab.screenshots) == 3 and all(s.startswith(b"\x89PNG") for s in tab.screenshots)
    assert tab.shot_list.count() == 3 and tab.preview.text().startswith("3 screenshots attached")
    tab.shot_list.item(1).setSelected(True)  # remove the middle one
    tab._remove_selected_screenshot()
    assert len(tab.screenshots) == 2 and [tab.shot_list.item(i).text() for i in range(2)] == ["1", "2"]
    tab.title_edit.setText("Cannot open SOP")
    tab.area.setCurrentText("SOP index")
    tab.description.setPlainText("Double-clicking an SOP does nothing.")
    tab._submit()
    assert tab.error.text() == "" and tab.table.rowCount() == 1
    assert tab.screenshots == [] and tab.preview.text() == "No screenshots attached"
    with db.session() as s:
        [row] = bug_reports.list_reports(s, admin)
        assert row.area == "SOP index" and row.screenshot_count == 2
        assert len(bug_reports.screenshots(s, admin, row.id)) == 2


def test_update_status_window_stays_open_on_missing_resolution(qtbot, db, admin):
    """BUG-0003: the status window closed when the resolution note was missing."""
    from aurum_lims.ui.views.bug_report import StatusDialog

    with db.session() as s:
        bug_reports.submit(s, admin, title="Example", area="Settings", severity="Low", description="x")
    with db.session() as s:
        [row] = bug_reports.list_reports(s, admin)
    dialog = StatusDialog(db, admin, row)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.status.setCurrentText("Fixed")
    dialog._save()
    assert dialog.isVisible() and dialog.result() != QDialog.Accepted
    assert "resolution" in dialog.error.text()
    assert dialog.status.currentText() == "Fixed"  # what was chosen is kept
    dialog.resolution.setPlainText("Corrected in version 0.1.1")
    dialog._save()
    assert dialog.result() == QDialog.Accepted
    with db.session() as s:
        [row] = bug_reports.list_reports(s, admin)
        assert row.status == "Fixed" and row.resolution == "Corrected in version 0.1.1"


def test_everyone_sees_sop_index_and_settings(qtbot, db, admin, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    window = MainWindow(db, viewer)
    qtbot.addWidget(window)
    titles = [p.title for p in window.pages]
    assert {"SOP Index", "Settings"} <= set(titles)
    for title in titles:
        assert window.show_page(title)
    window.sign_out("test")
