import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.errors import PermissionDenied
from aurum_lims.services import audit, auth
from aurum_lims.services.audit import AuditAction


def _drop_triggers(db):
    # Simulates someone editing the database file directly with another tool.
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER audit_entries_no_update")
        conn.exec_driver_sql("DROP TRIGGER audit_entries_no_delete")


def _generate_activity(db):
    for _ in range(3):
        with db.session() as s:
            auth.authenticate(s, "admin", "wrong-password-1")


def test_chain_verifies(db, admin):
    _generate_activity(db)
    with db.session() as s:
        report = audit.verify_integrity(s, admin)
    assert report.ok, report.problems
    assert report.entries_checked >= 4


def test_audit_rows_cannot_be_updated_or_deleted(db, admin):
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE audit_entries SET reason = 'edited'"))
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_entries"))


def test_tampered_entry_is_detected(db, admin):
    _generate_activity(db)
    _drop_triggers(db)
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE audit_entries SET username = 'someone_else' WHERE id = 2"))
    with db.session() as s:
        report = audit.verify_integrity(s, admin, record_result=False)
    assert not report.ok
    assert any("Entry 2 has been altered" in p for p in report.problems)


def test_deleted_middle_entry_is_detected(db, admin):
    _generate_activity(db)
    _drop_triggers(db)
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_entries WHERE id = 2"))
    with db.session() as s:
        report = audit.verify_integrity(s, admin, record_result=False)
    assert any("missing" in p for p in report.problems)


def test_deleted_last_entry_is_detected(db, admin):
    _generate_activity(db)
    _drop_triggers(db)
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_entries WHERE id = (SELECT MAX(id) FROM audit_entries)"))
    with db.session() as s:
        report = audit.verify_integrity(s, admin, record_result=False)
    assert any("end of the trail" in p for p in report.problems)


def test_query_filters_and_permissions(db, admin, make_user):
    analyst = make_user("jsmith")
    _generate_activity(db)
    with db.session() as s:
        failed = audit.query_entries(s, admin, action=AuditAction.LOGIN_FAILED)
        assert len(failed) == 3
        assert all(e.action == AuditAction.LOGIN_FAILED for e in failed)
        assert audit.query_entries(s, admin, search="jsmith")
        with pytest.raises(PermissionDenied):
            audit.query_entries(s, analyst)


def test_export_csv(db, admin, tmp_path):
    out = tmp_path / "audit.csv"
    with db.session() as s:
        entries = audit.query_entries(s, admin)
        audit.export_csv(s, admin, out, entries)
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("id,timestamp_utc")
    assert len(lines) == len(entries) + 1
    with db.session() as s:
        assert audit.query_entries(s, admin)[0].action == AuditAction.AUDIT_EXPORTED
