"""Reloading the app keeps the user signed in via a single-use, short-lived code."""

from datetime import timedelta

from aurum_lims.db.models import AppSetting, User, utcnow
from aurum_lims.services import audit, auth
from aurum_lims.services.audit import AuditAction


def test_resume_token_round_trip_and_single_use(db, admin):
    with db.session() as s:
        token = auth.create_resume_token(s, admin)
    with db.session() as s:
        resumed = auth.resume_session(s, token)
        assert resumed == admin
        entry = audit.query_entries(s, admin, action=AuditAction.LOGIN_SUCCESS)[0]
        assert "reload" in entry.new_values
    with db.session() as s:
        assert auth.resume_session(s, token) is None  # cannot be used twice


def test_resume_token_rejected_when_wrong_or_expired(db, admin):
    with db.session() as s:
        auth.create_resume_token(s, admin)
    with db.session() as s:
        assert auth.resume_session(s, "not-the-token") is None
        assert s.get(AppSetting, auth.RESUME_KEY) is None  # a wrong guess burns the code
    with db.session() as s:
        token = auth.create_resume_token(s, admin)
        row = s.get(AppSetting, auth.RESUME_KEY)
        user_id, digest, _ = row.value.split("|")
        row.value = f"{user_id}|{digest}|{(utcnow() - timedelta(seconds=1)).isoformat()}"
    with db.session() as s:
        assert auth.resume_session(s, token) is None


def test_resume_refused_for_locked_or_inactive_user(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        token = auth.create_resume_token(s, analyst)
        s.get(User, analyst.id).is_locked = True
    with db.session() as s:
        assert auth.resume_session(s, token) is None


def test_no_token_means_normal_sign_in(db):
    with db.session() as s:
        assert auth.resume_session(s, None) is None


# ----- the app side -------------------------------------------------------------

import sys  # noqa: E402

from aurum_lims.app import AppController  # noqa: E402
from aurum_lims.ui import reload as reload_module  # noqa: E402
from aurum_lims.ui.main_window import MainWindow  # noqa: E402


def test_restart_command_runs_the_package():
    assert reload_module.restart_command() == [sys.executable, "-m", "aurum_lims"]


def test_reload_hands_session_to_new_copy(qtbot, db, admin, monkeypatch):
    """Reload: sign out here, start a new copy with a one-time code, which signs the same
    user straight back in on the same page."""
    started = {}
    monkeypatch.setattr("aurum_lims.app.start_new_instance",
                        lambda token, page: started.update(token=token, page=page))
    app = QApplication.instance()
    monkeypatch.setattr(app, "quit", lambda: started.update(quit=True))
    controller = AppController(app, db)
    controller._open_main_window(admin)
    qtbot.addWidget(controller.window)
    controller.window.show_page("Audit Trail")
    controller.window.request_reload(confirm=False)
    assert started["page"] == "Audit Trail" and started["quit"] and started["token"]

    # The new copy picks up the code from its environment.
    monkeypatch.setenv(reload_module.RESUME_ENV, started["token"])
    monkeypatch.setenv(reload_module.PAGE_ENV, started["page"])
    new_controller = AppController(app, db)
    assert new_controller.start()
    qtbot.addWidget(new_controller.window)
    assert new_controller.user == admin
    assert new_controller.window.stack.currentWidget().title == "Audit Trail"
    assert reload_module.RESUME_ENV not in __import__("os").environ  # never passed on again
    with db.session() as s:
        actions = [e.action for e in audit.query_entries(s, admin, limit=4)]
    assert actions[:2] == [AuditAction.LOGIN_SUCCESS, AuditAction.LOGOUT]
    # Let the test close the window without the controller showing the sign-in screen.
    new_controller.window._signing_out = True


def test_update_watcher_notices_code_changes(qtbot, tmp_path):
    (tmp_path / "module.py").write_text("x = 1\n")
    watcher = reload_module.UpdateWatcher(folder=tmp_path, interval_ms=60_000)
    assert not watcher.check()
    newer = tmp_path / "module.py"
    import os
    stamp = newer.stat().st_mtime + 5
    os.utime(newer, (stamp, stamp))
    with qtbot.waitSignal(watcher.updated, timeout=1000):
        assert watcher.check()


def test_update_banner_and_shortcuts(qtbot, db, admin):
    window = MainWindow(db, admin)
    qtbot.addWidget(window)
    assert window.update_banner.isHidden()
    window.update_watcher.updated.emit()
    assert not window.update_banner.isHidden()
    window.sign_out("test")


from PySide6.QtWidgets import QApplication  # noqa: E402
