import pytest

from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import auth, users


def test_initial_admin_only_once(db, admin):
    with db.session() as s:
        with pytest.raises(ValidationError):
            users.create_initial_admin(s, "second", "Second Admin", "AnotherPass2026")


def test_analyst_cannot_manage_users(db, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            users.create_user(s, analyst, username="x.y", full_name="X Y", role=Role.VIEWER,
                              temporary_password="Temporary2026x")
        with pytest.raises(PermissionDenied):
            users.list_users(s, analyst)


def test_usernames_are_unique_and_validated(db, admin, make_user):
    make_user("jsmith")
    with db.session() as s:
        with pytest.raises(ValidationError, match="taken"):
            users.create_user(s, admin, username="JSmith", full_name="J", role=Role.VIEWER,
                              temporary_password="Temporary2026x")
        with pytest.raises(ValidationError, match="Username"):
            users.create_user(s, admin, username="bad name!", full_name="J", role=Role.VIEWER,
                              temporary_password="Temporary2026x")


def test_reason_required_for_changes(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            users.update_user(s, admin, analyst.id, role=Role.QA_REVIEWER, reason="  ")


def test_update_user_role(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        users.update_user(s, admin, analyst.id, role=Role.QA_REVIEWER, reason="Promoted to QA")
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", "Chemistry2026")
    assert result.user.role is Role.QA_REVIEWER


def test_last_admin_is_protected(db, admin):
    with db.session() as s:
        with pytest.raises(ValidationError, match="Administrator"):
            users.update_user(s, admin, admin.id, role=Role.ANALYST, reason="test")
        with pytest.raises(ValidationError, match="your own"):
            users.deactivate_user(s, admin, admin.id, reason="test")


def test_reset_password_unlocks_and_forces_change(db, admin, make_user):
    analyst = make_user("jsmith")
    for _ in range(5):
        with db.session() as s:
            auth.authenticate(s, "jsmith", "wrong-password-1")
    with db.session() as s:
        users.reset_password(s, admin, analyst.id, "ResetTemp2026", reason="Forgot password")
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", "ResetTemp2026")
    assert result.ok and result.must_change_password


def test_unlock_user(db, admin, make_user):
    analyst = make_user("jsmith")
    for _ in range(5):
        with db.session() as s:
            auth.authenticate(s, "jsmith", "wrong-password-1")
    with db.session() as s:
        users.unlock_user(s, admin, analyst.id, reason="Verified identity by phone")
    with db.session() as s:
        assert auth.authenticate(s, "jsmith", "Chemistry2026").ok


def test_deactivate_and_reactivate(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        users.deactivate_user(s, admin, analyst.id, reason="On leave")
    with db.session() as s:
        users.reactivate_user(s, admin, analyst.id, reason="Back from leave")
    with db.session() as s:
        assert auth.authenticate(s, "jsmith", "Chemistry2026").ok
