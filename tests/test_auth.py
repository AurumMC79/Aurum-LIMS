from datetime import timedelta

import pytest
from sqlalchemy import select

from aurum_lims.db.models import AuditEntry, User, utcnow
from aurum_lims.errors import ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import auth, users
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.auth import LoginStatus
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD


def actions(db):
    with db.session() as s:
        return [e.action for e in s.scalars(select(AuditEntry).order_by(AuditEntry.id))]


def test_successful_login_is_audited(db, admin):
    with db.session() as s:
        result = auth.authenticate(s, "  ADMIN ", ADMIN_PASSWORD)
    assert result.ok
    assert result.user.role is Role.MASTER_ADMINISTRATOR  # the first-run account
    assert not result.must_change_password
    assert actions(db)[-1] == AuditAction.LOGIN_SUCCESS


def test_unknown_user_gets_generic_message(db, admin):
    with db.session() as s:
        result = auth.authenticate(s, "nobody", "whatever123")
    assert result.status is LoginStatus.INVALID_CREDENTIALS
    assert result.message == auth.INVALID_CREDENTIALS_MESSAGE
    assert actions(db)[-1] == AuditAction.LOGIN_FAILED


def test_five_wrong_passwords_lock_the_account(db, make_user):
    make_user("jsmith")
    for attempt in range(4):
        with db.session() as s:
            result = auth.authenticate(s, "jsmith", "wrong-password-1")
        assert result.status is LoginStatus.INVALID_CREDENTIALS
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", "wrong-password-1")
    assert result.status is LoginStatus.LOCKED
    assert AuditAction.ACCOUNT_LOCKED in actions(db)

    # Even the correct password is refused while locked.
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", USER_PASSWORD)
    assert result.status is LoginStatus.LOCKED


def test_successful_login_resets_failure_count(db, make_user):
    make_user("jsmith")
    for _ in range(3):
        with db.session() as s:
            auth.authenticate(s, "jsmith", "wrong-password-1")
    with db.session() as s:
        assert auth.authenticate(s, "jsmith", USER_PASSWORD).ok
        assert s.scalar(select(User.failed_login_count).where(User.username == "jsmith")) == 0


def test_deactivated_user_cannot_sign_in(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        users.deactivate_user(s, admin, analyst.id, reason="Left the company")
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", USER_PASSWORD)
    assert result.status is LoginStatus.INACTIVE


def test_new_user_must_change_password(db, admin):
    with db.session() as s:
        users.create_user(s, admin, username="new.user", full_name="New User", role=Role.VIEWER,
                          temporary_password="Temporary2026x")
    with db.session() as s:
        result = auth.authenticate(s, "new.user", "Temporary2026x")
    assert result.ok and result.must_change_password


def test_change_password_rules(db, make_user):
    user = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(ValidationError, match="current password"):
            auth.change_password(s, user.id, "wrong-current-1", "Brandnew2026x")
    with db.session() as s:
        with pytest.raises(ValidationError, match="different"):
            auth.change_password(s, user.id, USER_PASSWORD, USER_PASSWORD)
    with db.session() as s:
        with pytest.raises(ValidationError, match="at least"):
            auth.change_password(s, user.id, USER_PASSWORD, "short1")
    with db.session() as s:
        auth.change_password(s, user.id, USER_PASSWORD, "Brandnew2026x")
    # Re-using a recent password is refused.
    with db.session() as s:
        with pytest.raises(ValidationError, match="last"):
            auth.change_password(s, user.id, "Brandnew2026x", USER_PASSWORD)
    assert actions(db).count(AuditAction.PASSWORD_CHANGED) == 2  # make_user's change + this one


def test_expired_password_is_flagged(db, make_user):
    user = make_user("jsmith")
    with db.session() as s:
        s.get(User, user.id).password_changed_at = utcnow() - timedelta(days=91)
    with db.session() as s:
        result = auth.authenticate(s, "jsmith", USER_PASSWORD)
    assert result.ok and result.password_expired


def test_reauthenticate_counts_failures(db, make_user):
    user = make_user("jsmith")
    for _ in range(4):
        with db.session() as s:
            result = auth.reauthenticate(s, user.id, "nope-nope-1", failure_action=AuditAction.SESSION_UNLOCK_FAILED)
        assert result.status is LoginStatus.INVALID_CREDENTIALS
    with db.session() as s:
        result = auth.reauthenticate(s, user.id, "nope-nope-1", failure_action=AuditAction.SESSION_UNLOCK_FAILED)
    assert result.status is LoginStatus.LOCKED
