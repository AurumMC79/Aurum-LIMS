"""Reloading the app keeps the user signed in via a single-use, short-lived code."""

from datetime import timedelta

import pytest

from aurum_lims.db.models import AppSetting, User, utcnow
from aurum_lims.errors import PermissionDenied
from aurum_lims.security.roles import Role
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
    it_user = make_user("it.support", Role.IT_SUPPORT)
    with db.session() as s:
        token = auth.create_resume_token(s, it_user)
        s.get(User, it_user.id).is_locked = True
    with db.session() as s:
        assert auth.resume_session(s, token) is None


def test_only_master_admin_and_it_support_can_reload(db, admin, make_user):
    it_user = make_user("it.support", Role.IT_SUPPORT)
    for role in (Role.ADMINISTRATOR, Role.QA_REVIEWER, Role.ANALYST, Role.VIEWER):
        other = make_user(f"user.{role.value}", role)
        with db.session() as s:
            with pytest.raises(PermissionDenied):
                auth.create_resume_token(s, other)
    for allowed in (admin, it_user):  # the first-run account is the Master Administrator
        with db.session() as s:
            token = auth.create_resume_token(s, allowed)
        with db.session() as s:
            assert auth.resume_session(s, token) == allowed


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


@pytest.mark.parametrize("role, can_reload", [
    (Role.IT_SUPPORT, True), (Role.ADMINISTRATOR, False), (Role.QA_REVIEWER, False), (Role.ANALYST, False),
])
def test_reload_button_only_for_permitted_roles(qtbot, db, admin, make_user, role, can_reload):
    user = make_user(f"user.{role.value}", role)
    window = MainWindow(db, user)
    qtbot.addWidget(window)
    assert window.reload_button.isHidden() != can_reload
    assert (window.update_watcher is not None) == can_reload  # no update banner for others
    window.sign_out("test")


from PySide6.QtWidgets import QApplication  # noqa: E402


def test_new_copy_waits_until_the_old_copy_has_exited(monkeypatch):
    """The two copies must never have the database open together (saves could go missing from disk)."""
    import subprocess
    import sys
    import time

    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    monkeypatch.setenv(reload_module.PARENT_ENV, str(old.pid))
    started = time.monotonic()
    assert reload_module.wait_for_previous_instance()
    assert time.monotonic() - started >= 0.4 and old.poll() is not None
    assert reload_module.PARENT_ENV not in __import__("os").environ  # used once
    # Gives up (and carries on) if the old copy hangs.
    stuck = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        monkeypatch.setenv(reload_module.PARENT_ENV, str(stuck.pid))
        assert reload_module.wait_for_previous_instance(timeout=0.3) is False
    finally:
        stuck.kill()
    assert reload_module.wait_for_previous_instance()  # not started by Reload: nothing to wait for


def test_old_copy_closes_the_database_before_starting_the_new_one(qtbot, db, admin, monkeypatch):
    order = []
    monkeypatch.setattr("aurum_lims.app.start_new_instance", lambda token, page: order.append("start"))
    app = QApplication.instance()
    monkeypatch.setattr(app, "quit", lambda: order.append("quit"))
    controller = AppController(app, db)
    controller._open_main_window(admin)
    qtbot.addWidget(controller.window)
    original = db.dispose
    monkeypatch.setattr(db, "dispose", lambda: (order.append("dispose"), original()))
    controller.window.request_reload(confirm=False)
    assert order == ["dispose", "start", "quit"]


def test_start_new_instance_passes_its_process_id(monkeypatch):
    import os
    import subprocess

    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, env, **kw: seen.update(env=env))
    reload_module.start_new_instance("token", "Samples")
    assert seen["env"][reload_module.PARENT_ENV] == str(os.getpid())
