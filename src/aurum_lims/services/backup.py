"""Database backups.

A backup is a complete, consistent copy of the SQLite database made with
SQLite's online backup API (safe while the app is in use), then checked
with ``PRAGMA integrity_check``. Backups are taken:

* automatically when the app starts, if the last one is older than the
  configured interval (default every 24 hours);
* just before a database upgrade, so an update can always be undone;
* on request from Settings → Backups.

Old backups beyond the retention count are removed automatically (only
files this module created, matched by name). Pre-update backups are kept
separately and never counted against that limit.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from aurum_lims import config
from aurum_lims.db.models import AppSetting, utcnow
from aurum_lims.db.session import MIGRATIONS_DIR, Database
from aurum_lims.errors import AurumError, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

FOLDER_KEY, KEEP_KEY, INTERVAL_KEY, LAST_KEY = "backup_folder", "backup_keep", "backup_interval_hours", "backup_last"
NAME_PATTERN = re.compile(r"^aurum-(\d{8}-\d{6})-(auto|manual|pre-update)\.db$")
PRE_UPDATE_KEEP = 10


class BackupKind(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    PRE_UPDATE = "pre-update"

    @property
    def label(self) -> str:
        return {"auto": "Automatic", "manual": "Manual", "pre-update": "Before update"}[self.value]


class BackupError(AurumError):
    pass


@dataclass(frozen=True)
class BackupSettings:
    folder: str = ""  # empty = the default folder next to the database
    keep: int = 30
    interval_hours: int = 24  # 0 = no automatic backups

    @property
    def path(self) -> Path:
        return Path(self.folder) if self.folder else default_folder()


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    kind: BackupKind
    created: datetime
    size_bytes: int


def default_folder() -> Path:
    return config.data_dir() / "backups"


def _sqlite_path(db: Database) -> Path | None:
    if db.engine.dialect.name != "sqlite" or not db.url.startswith("sqlite:///") or ":memory:" in db.url:
        return None
    return Path(db.url.removeprefix("sqlite:///"))


# ----- settings ----------------------------------------------------------------

def get_settings(session: Session) -> BackupSettings:
    def value(key, default):
        row = session.get(AppSetting, key)
        return row.value if row is not None else default

    try:
        keep = int(value(KEEP_KEY, BackupSettings.keep))
        interval = int(value(INTERVAL_KEY, BackupSettings.interval_hours))
    except ValueError:
        keep, interval = BackupSettings.keep, BackupSettings.interval_hours
    return BackupSettings(folder=value(FOLDER_KEY, ""), keep=keep, interval_hours=interval)


def set_settings(session: Session, actor: SessionUser, settings: BackupSettings, reason: str) -> BackupSettings:
    require(actor, Permission.MANAGE_SETTINGS)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    if not 1 <= settings.keep <= 365:
        raise ValidationError("Keep between 1 and 365 backups.")
    if not 0 <= settings.interval_hours <= 24 * 30:
        raise ValidationError("The interval must be between 0 (off) and 720 hours.")
    folder = settings.folder.strip()
    if folder:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(f"The backup folder cannot be used: {exc}") from None
    new = {FOLDER_KEY: folder, KEEP_KEY: str(settings.keep), INTERVAL_KEY: str(settings.interval_hours)}
    old_settings = get_settings(session)
    old = {FOLDER_KEY: old_settings.folder, KEEP_KEY: str(old_settings.keep),
           INTERVAL_KEY: str(old_settings.interval_hours)}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key in changed:
        _put(session, key, new[key], actor.id)
    audit.record(session, AuditAction.SETTING_CHANGED, actor=actor, entity_type="setting", entity_id="backups",
                 old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    return BackupSettings(folder, settings.keep, settings.interval_hours)


def _put(session: Session, key: str, value: str, user_id: int | None) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value, updated_by_id=user_id))
    else:
        row.value, row.updated_at, row.updated_by_id = value, utcnow(), user_id
    session.flush()


def last_backup_time(session: Session) -> datetime | None:
    row = session.get(AppSetting, LAST_KEY)
    return datetime.fromisoformat(row.value) if row is not None else None


# ----- making and checking backups ----------------------------------------------

def verify(path: Path) -> tuple[bool, str]:
    """Open a backup read-only and run SQLite's integrity check."""
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"Could not read the backup: {exc}"
    if result != "ok":
        return False, f"Integrity check failed: {result}"
    return True, f"OK – integrity check passed ({users} user account(s))"


def _copy(db: Database, folder: Path, kind: BackupKind) -> Path:
    if _sqlite_path(db) is None:
        raise BackupError("Automatic backups are only available for the built-in SQLite database.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"The backup folder {folder} cannot be used: {exc}") from None
    stamp = datetime.now()
    path = folder / f"aurum-{stamp:%Y%m%d-%H%M%S}-{kind.value}.db"
    while path.exists():  # two backups in the same second
        stamp += timedelta(seconds=1)
        path = folder / f"aurum-{stamp:%Y%m%d-%H%M%S}-{kind.value}.db"
    target = sqlite3.connect(path)
    try:
        with db.engine.connect() as conn:
            conn.connection.dbapi_connection.backup(target)
        # The copy inherits WAL mode; switch it back so a backup is one self-contained file.
        target.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        target.close()
        path.unlink(missing_ok=True)
        raise BackupError(f"The backup could not be written: {exc}") from None
    target.close()
    ok, message = verify(path)
    if not ok:
        raise BackupError(f"The backup {path.name} failed its check: {message}")
    return path


def list_backups(folder: Path) -> list[BackupInfo]:
    """Backups in ``folder``, newest first."""
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = NAME_PATTERN.match(path.name)
        if match:
            found.append(BackupInfo(path, BackupKind(match.group(2)),
                                    datetime.strptime(match.group(1), "%Y%m%d-%H%M%S"), path.stat().st_size))
    return sorted(found, key=lambda b: b.created, reverse=True)


def prune(folder: Path, keep: int) -> list[Path]:
    """Remove the oldest backups beyond the retention count (pre-update ones separately)."""
    removed = []
    backups = list_backups(folder)
    regular = [b for b in backups if b.kind is not BackupKind.PRE_UPDATE]
    pre_update = [b for b in backups if b.kind is BackupKind.PRE_UPDATE]
    for old in regular[keep:] + pre_update[PRE_UPDATE_KEEP:]:
        try:
            old.path.unlink()
            removed.append(old.path)
        except OSError:
            pass
    return removed


def _record(db: Database, path: Path, kind: BackupKind, actor: SessionUser | None) -> None:
    with db.session() as session:
        settings = get_settings(session)
        _put(session, LAST_KEY, utcnow().isoformat(), actor.id if actor else None)
        removed = prune(settings.path, settings.keep)
        audit.record(session, AuditAction.BACKUP_CREATED, actor=actor, entity_type="backup", entity_id=path.name,
                     new={"kind": kind.value, "file": str(path), "size_bytes": path.stat().st_size,
                          "old_backups_removed": len(removed)})


def backup_now(db: Database, actor: SessionUser) -> Path:
    """A manual backup from Settings → Backups."""
    require(actor, Permission.MANAGE_SETTINGS)
    with db.session() as session:
        folder = get_settings(session).path
    path = _copy(db, folder, BackupKind.MANUAL)
    _record(db, path, BackupKind.MANUAL, actor)
    return path


def automatic_backup_if_due(db: Database) -> Path | None:
    """Called at start-up: back up if the last backup is older than the interval."""
    if _sqlite_path(db) is None:
        return None
    with db.session() as session:
        settings = get_settings(session)
        last = last_backup_time(session)
    if settings.interval_hours == 0:
        return None
    if last is not None and utcnow() - last < timedelta(hours=settings.interval_hours):
        return None
    path = _copy(db, settings.path, BackupKind.AUTO)
    _record(db, path, BackupKind.AUTO, None)
    return path


def upgrade_pending(db: Database) -> bool:
    """True when an existing database is about to be changed by a migration."""
    path = _sqlite_path(db)
    if path is None or not path.exists():
        return False
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with db.engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current is not None and current != head


def pre_update_backup(db: Database) -> Path | None:
    """Back up just before migrations run. Uses the default folder settings that the
    (not yet upgraded) database can provide; the audit entry is added after the upgrade."""
    if not upgrade_pending(db):
        return None
    folder = default_folder()
    try:
        with db.engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT value FROM app_settings WHERE key = ?", (FOLDER_KEY,)).fetchone()
        if row and row[0]:
            folder = Path(row[0])
    except Exception:  # noqa: BLE001 - very old schemas: fall back to the default folder
        pass
    return _copy(db, folder, BackupKind.PRE_UPDATE)


def record_pre_update_backup(db: Database, path: Path) -> None:
    _record(db, path, BackupKind.PRE_UPDATE, None)
