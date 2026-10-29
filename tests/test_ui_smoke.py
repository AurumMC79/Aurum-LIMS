"""Smoke tests that build the real screens (offscreen) and click through them."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel

from aurum_lims.security.roles import Role
from aurum_lims.services import auth, sample_types, storage
from aurum_lims.ui.first_run_dialog import FirstRunDialog
from aurum_lims.ui.lock_screen import LockDialog
from aurum_lims.ui.login_dialog import LoginDialog
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.sample_dialogs import RegisterSampleDialog, SampleDetailDialog
from aurum_lims.ui.views.sample_types import SampleTypesView
from aurum_lims.ui.views.samples import SamplesView
from aurum_lims.ui.views.storage import StorageView
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
    assert {"Dashboard", "Samples", "Storage", "Sample Types", "Users", "Audit Trail", "Settings",
            "My Account"} <= set(titles)
    for title in titles:
        assert window.show_page(title)  # each page builds and refreshes without error
    window.sign_out("test")


def test_analyst_main_window_hides_admin_pages(qtbot, db, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    window = MainWindow(db, analyst)
    qtbot.addWidget(window)
    titles = {p.title for p in window.pages}
    window.sign_out("test")  # before asserting, so a failure can't leave a window asking to sign out
    assert "Users" not in titles
    assert "Audit Trail" not in titles
    assert "Sample Types" not in titles
    assert {"Samples", "Storage", "Settings"} <= titles  # Settings holds 'Report a bug' for everyone
    settings = next(p for p in window.pages if p.title == "Settings")
    assert [settings.tabs.tabText(i) for i in range(settings.tabs.count())] == ["Report a bug"]


def test_register_dialog_and_sample_screens(qtbot, db, admin, make_user, monkeypatch):
    with db.session() as s:
        sample_types.seed_defaults(s)
        fridge = storage.create_location(s, admin, name="Fridge 1", kind="Refrigerator").id
    analyst = make_user("jsmith", Role.ANALYST)

    dialog = RegisterSampleDialog(db, analyst)
    qtbot.addWidget(dialog)
    rm_index = next(i for i, t in enumerate(dialog.types) if t[2] == "RM")
    dialog.type_combo.setCurrentIndex(rm_index)
    assert dialog.next_id.text().startswith("AU-")
    dialog.fields.category_edits["material_name"].setText("Citric acid")
    dialog.fields.category_edits["lot_number"].setText("CA-778")
    dialog.count.setValue(2)
    dialog.receipt.location.setCurrentIndex(dialog.receipt.location.findData(fridge))
    dialog._submit()
    assert dialog.result() == QDialog.Accepted, dialog.error.text()
    assert len(dialog.created) == 2

    view = SamplesView(db, analyst)
    qtbot.addWidget(view)
    view.refresh()
    assert view.table.rowCount() == 2
    view.table.selectRow(0)
    assert view.transfer_button.isEnabled() and not view.receive_button.isEnabled()

    detail = SampleDetailDialog(db, analyst, view.rows[0].id)
    qtbot.addWidget(detail)
    label_texts = [label.text() for label in detail.findChildren(QLabel)]
    assert "Citric acid" in label_texts
    assert any("Fridge 1" in text for text in label_texts)

    storage_view = StorageView(db, analyst)
    qtbot.addWidget(storage_view)
    storage_view.refresh()
    assert storage_view.tree.topLevelItemCount() == 1
    assert storage_view.add_button.isHidden()  # analysts cannot change storage


def test_sample_types_view_format_preview(qtbot, db, admin):
    with db.session() as s:
        sample_types.seed_defaults(s)
    view = SampleTypesView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    assert view.table.rowCount() == 7
    view.lab_format.pattern.setText("{TYPE}-{YY}-{SEQ:3}")
    assert "RM-" in view.lab_format.preview.text()
    view.lab_format.pattern.setText("NO-SEQUENCE")
    assert "exactly once" in view.lab_format.preview.text()


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
