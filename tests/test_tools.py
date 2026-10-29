"""The developer tool that reads bug reports."""

import pytest
from sqlalchemy import func, select

from aurum_lims.db.models import BugReport
from aurum_lims.services import bug_reports
from aurum_lims.tools import bug_reports as tool
from tests.test_documents import PNG


@pytest.fixture
def reports(db, admin, monkeypatch):
    monkeypatch.setenv("AURUM_LIMS_DATABASE_URL", db.url)
    with db.session() as s:
        bug_reports.submit(s, admin, title="Lot expiry shown in red too early", area="Inventory", severity="Low",
                           description="Expiry is 31 days away but shows amber.", screenshots=[PNG])
        fixed = bug_reports.submit(s, admin, title="Typo on dashboard", area="Dashboard", severity="Low",
                                   description="'recieved'")
        bug_reports.update_status(s, admin, fixed.id, "Fixed", "Corrected spelling")
    return db


def test_list_open_reports(reports, capsys):
    assert tool.main([]) == 0
    out = capsys.readouterr().out
    assert "BUG-0001" in out and "BUG-0002" not in out
    assert tool.main(["--all"]) == 0
    assert "BUG-0002" in capsys.readouterr().out


def test_show_and_export(reports, capsys, tmp_path):
    assert tool.main(["bug-0001"]) == 0
    out = capsys.readouterr().out
    assert "# BUG-0001: Lot expiry shown in red too early" in out and "Expiry is 31 days away" in out
    assert tool.main(["--all", "--export", str(tmp_path)]) == 0
    assert (tmp_path / "BUG-0001.md").read_text(encoding="utf-8").count("![screenshot 1](BUG-0001.png)") == 1
    assert (tmp_path / "BUG-0001.png").read_bytes() == PNG
    assert "Corrected spelling" in (tmp_path / "BUG-0002.md").read_text(encoding="utf-8")


def test_tool_is_read_only(reports):
    with reports.session() as s:
        before = s.scalar(select(func.count()).select_from(BugReport))
    session = tool._open_session()
    with pytest.raises(Exception, match="readonly"):
        session.execute(BugReport.__table__.delete())
        session.commit()
    session.close()
    with reports.session() as s:
        assert s.scalar(select(func.count()).select_from(BugReport)) == before
