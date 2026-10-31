"""The database file on disk is always complete after a save."""

import shutil
import sqlite3

from aurum_lims.services import bug_reports


def test_saves_reach_the_main_database_file_straight_away(db, admin, tmp_path):
    """Saves must not live only in the -wal file: other programs (backups, the bug-report reader) read the
    main file, and the -wal file is not guaranteed to survive (seen after an app Reload)."""
    with db.session() as s:
        bug_reports.submit(s, admin, title="Checkpoint check", area="General", severity="Low",
                           description="must be in the main file")
    main_file = db.url.removeprefix("sqlite:///")
    shutil.copyfile(main_file, tmp_path / "only-main.db")  # deliberately without the -wal file
    conn = sqlite3.connect(tmp_path / "only-main.db")
    try:
        assert [r[0] for r in conn.execute("SELECT title FROM bug_reports")] == ["Checkpoint check"]
    finally:
        conn.close()
