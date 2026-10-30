"""Database backups: manual, automatic at start-up, before an update, retention and settings."""

import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy import select

from aurum_lims.db.models import AppSetting, AuditEntry, utcnow
from aurum_lims.db.session import Database
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.services import backup
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.backup import BackupKind, BackupSettings
from aurum_lims.ui.views.settings import SettingsView
from tests.test_migrations import _upgrade_to


def _users_in(path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT username FROM users ORDER BY id")]
    finally:
        conn.close()


def test_backup_now_copies_and_checks_the_database(db, admin):
    path = backup.backup_now(db, admin)
    assert path.parent == backup.default_folder()
    assert backup.NAME_PATTERN.match(path.name) and path.name.endswith("-manual.db")
    assert _users_in(path) == ["admin"]
    assert backup.verify(path)[0]
    [info] = backup.list_backups(path.parent)
    assert info.kind is BackupKind.MANUAL and info.size_bytes > 0
    with db.session() as s:
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.BACKUP_CREATED)).one()
        assert entry.username == "admin" and entry.entity_id == path.name
        assert backup.last_backup_time(s) is not None


def test_only_administrators_back_up(db, make_user):
    analyst = make_user("ann")
    with pytest.raises(PermissionDenied):
        backup.backup_now(db, analyst)
    with db.session() as s, pytest.raises(PermissionDenied):
        backup.set_settings(s, analyst, BackupSettings(keep=5), "x")


def test_verify_detects_a_damaged_backup(db, admin):
    path = backup.backup_now(db, admin)
    data = bytearray(path.read_bytes())
    data[:16] = b"not a database!!"
    path.write_bytes(bytes(data))
    ok, message = backup.verify(path)
    assert not ok and "Could not read" in message


def test_automatic_backup_runs_when_due(db, admin):
    first = backup.automatic_backup_if_due(db)
    assert first is not None and first.name.endswith("-auto.db")
    assert backup.automatic_backup_if_due(db) is None  # not due again yet
    with db.session() as s:  # pretend the last one was 25 hours ago
        s.get(AppSetting, backup.LAST_KEY).value = (utcnow() - timedelta(hours=25)).isoformat()
    second = backup.automatic_backup_if_due(db)
    assert second is not None and second != first
    with db.session() as s:
        entries = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.BACKUP_CREATED)).all()
        assert [e.username for e in entries] == ["system", "system"]


def test_automatic_backups_can_be_turned_off(db, admin):
    with db.session() as s:
        backup.set_settings(s, admin, BackupSettings(interval_hours=0), "Backed up by IT instead")
    assert backup.automatic_backup_if_due(db) is None
    assert backup.list_backups(backup.default_folder()) == []


def test_old_backups_are_removed_beyond_the_limit(db, admin, tmp_path):
    folder = tmp_path / "elsewhere"
    with db.session() as s:
        backup.set_settings(s, admin, BackupSettings(folder=str(folder), keep=3), "Keep fewer on the share")
    # Some older backups, including pre-update ones (kept separately) and an unrelated file.
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (folder / f"aurum-{stamp}-auto.db").write_bytes(b"x")
    (folder / "aurum-20250101-000000-pre-update.db").write_bytes(b"x")
    (folder / "notes.txt").write_text("not a backup")
    newest = backup.backup_now(db, admin)
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["aurum-20250101-000000-pre-update.db", "aurum-20260102-000000-auto.db",
                     "aurum-20260103-000000-auto.db", newest.name, "notes.txt"]


def test_settings_are_validated_and_audited(db, admin, tmp_path):
    with db.session() as s:
        assert backup.get_settings(s) == BackupSettings()
        with pytest.raises(ValidationError, match="reason"):
            backup.set_settings(s, admin, BackupSettings(keep=10), " ")
        with pytest.raises(ValidationError, match="between 1 and 365"):
            backup.set_settings(s, admin, BackupSettings(keep=0), "x")
        with pytest.raises(ValidationError, match="Nothing"):
            backup.set_settings(s, admin, BackupSettings(), "x")
        backup.set_settings(s, admin, BackupSettings(folder=str(tmp_path / "share"), keep=10, interval_hours=12),
                            "Move backups to the share")
    with db.session() as s:
        settings = backup.get_settings(s)
        assert settings.path == tmp_path / "share" and settings.keep == 10 and settings.interval_hours == 12
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.SETTING_CHANGED)).one()
        assert entry.entity_id == "backups" and entry.reason == "Move backups to the share"


def test_backup_is_taken_before_an_update(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    try:
        _upgrade_to(db, "0006")
        assert backup.upgrade_pending(db)
        path = backup.pre_update_backup(db)
        assert path is not None and path.name.endswith("-pre-update.db")
        db.upgrade()
        backup.record_pre_update_backup(db, path)
        # The backup is still the old schema, so the update can be undone.
        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0006"
        finally:
            conn.close()
        assert not backup.upgrade_pending(db)
        assert backup.pre_update_backup(db) is None  # nothing to update now
    finally:
        db.dispose()


def test_no_pre_update_backup_for_a_new_database(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'new.db').as_posix()}")
    try:
        assert backup.pre_update_backup(db) is None
    finally:
        db.dispose()


def test_backups_tab(qtbot, db, admin, make_user):
    view = SettingsView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    titles = [view.tabs.tabText(i) for i in range(view.tabs.count())]
    assert "Backups" in titles
    tab = view.backups_tab
    assert tab.table.rowCount() == 0
    backup.backup_now(db, admin)
    tab.refresh()
    assert tab.table.rowCount() == 1 and tab.table.item(0, 1).text() == "Manual"
    # Analysts do not see the tab.
    analyst_view = SettingsView(db, make_user("ann"))
    qtbot.addWidget(analyst_view)
    assert "Backups" not in [analyst_view.tabs.tabText(i) for i in range(analyst_view.tabs.count())]
