"""Database upgrades that move existing data."""

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from aurum_lims.db.session import MIGRATIONS_DIR, Database
from aurum_lims.services import bug_reports
from tests.test_documents import PNG


def _upgrade_to(db: Database, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with db.engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)


def test_0007_moves_existing_screenshots(tmp_path, admin):
    """Existing single screenshots survive the move to several-per-report (BUG-0004)."""
    db = Database(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    _upgrade_to(db, "0006")
    with db.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, full_name, role, password_hash, is_active, is_locked, "
            "failed_login_count, must_change_password, password_changed_at, created_at) "
            "VALUES (1, 'old', 'Old User', 'administrator', 'x', 1, 0, 0, 0, '2026-10-01', '2026-10-01')"))
        for number, shot in (("BUG-0001", PNG), ("BUG-0002", None)):
            conn.execute(text(
                "INSERT INTO bug_reports (number, title, area, severity, description, screenshot, app_version, "
                "status, reported_at, reported_by_id) VALUES (:n, 'old report', 'General', 'Low', 'x', :s, "
                "'0.1.0', 'New', '2026-10-28', 1)"), {"n": number, "s": shot})
    db.upgrade()  # 0006 -> latest
    with db.session() as s:
        rows = {r.number: r for r in bug_reports.list_reports(s, None)}
        assert rows["BUG-0001"].screenshot_count == 1 and rows["BUG-0002"].screenshot_count == 0
        assert bug_reports.screenshots(s, None, rows["BUG-0001"].id) == [PNG]
    with db.engine.connect() as conn:
        columns = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(bug_reports)")]
        assert "screenshot" not in columns
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    db.dispose()
