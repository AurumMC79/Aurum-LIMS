"""Signing in and out, account lockout, re-authentication and password changes."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import AppSetting, PasswordHistory, User, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security import passwords
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.settings import SecuritySettings, get_security_settings

INVALID_CREDENTIALS_MESSAGE = "Invalid username or password."


class LoginStatus(Enum):
    SUCCESS = "success"
    INVALID_CREDENTIALS = "invalid_credentials"
    LOCKED = "locked"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class LoginResult:
    status: LoginStatus
    message: str = ""
    user: SessionUser | None = None
    must_change_password: bool = False
    password_expired: bool = False

    @property
    def ok(self) -> bool:
        return self.status is LoginStatus.SUCCESS


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _locked_message() -> str:
    return "This account is locked. Contact an administrator to unlock it."


def _register_failure(
    session: Session, user: User, settings: SecuritySettings, action: AuditAction, context: dict
) -> LoginResult:
    """Count a wrong password against ``user``; lock the account at the limit."""
    user.failed_login_count += 1
    audit.record(
        session,
        action,
        user_id=user.id,
        username=user.username,
        entity_type="user",
        entity_id=user.id,
        new={**context, "failed_attempts": user.failed_login_count},
    )
    if user.failed_login_count >= settings.max_failed_logins:
        user.is_locked = True
        audit.record(
            session,
            AuditAction.ACCOUNT_LOCKED,
            user_id=user.id,
            username=user.username,
            entity_type="user",
            entity_id=user.id,
            new={"failed_attempts": user.failed_login_count},
            reason="Too many failed password attempts",
        )
        return LoginResult(
            LoginStatus.LOCKED,
            f"Account locked after {user.failed_login_count} failed attempts. Contact an administrator.",
        )
    remaining = settings.max_failed_logins - user.failed_login_count
    return LoginResult(
        LoginStatus.INVALID_CREDENTIALS,
        f"{INVALID_CREDENTIALS_MESSAGE} {remaining} attempt{'s' if remaining != 1 else ''} left before the account is locked.",
    )


def password_expired(user: User, settings: SecuritySettings) -> bool:
    if settings.password_expiry_days <= 0:
        return False
    return utcnow() >= user.password_changed_at + timedelta(days=settings.password_expiry_days)


def authenticate(session: Session, username: str, password: str) -> LoginResult:
    """Check a sign-in attempt. Every outcome is written to the audit trail,
    so the caller must commit even when the result is a failure."""
    settings = get_security_settings(session)
    name = normalize_username(username)
    user = session.scalar(select(User).where(User.username == name))

    if user is None:
        passwords.verify_dummy(password)
        audit.record(session, AuditAction.LOGIN_FAILED, username=name or "(blank)", new={"cause": "unknown username"})
        return LoginResult(LoginStatus.INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE)

    if not passwords.verify_password(user.password_hash, password):
        if not user.is_active or user.is_locked:
            cause = "account inactive" if not user.is_active else "account locked"
            audit.record(
                session, AuditAction.LOGIN_FAILED, user_id=user.id, username=user.username,
                entity_type="user", entity_id=user.id, new={"cause": f"wrong password ({cause})"},
            )
            return LoginResult(LoginStatus.INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE)
        return _register_failure(session, user, settings, AuditAction.LOGIN_FAILED, {"cause": "wrong password"})

    if not user.is_active:
        audit.record(
            session, AuditAction.LOGIN_FAILED, user_id=user.id, username=user.username,
            entity_type="user", entity_id=user.id, new={"cause": "account inactive"},
        )
        return LoginResult(LoginStatus.INACTIVE, "This account has been deactivated. Contact an administrator.")

    if user.is_locked:
        audit.record(
            session, AuditAction.LOGIN_FAILED, user_id=user.id, username=user.username,
            entity_type="user", entity_id=user.id, new={"cause": "account locked"},
        )
        return LoginResult(LoginStatus.LOCKED, _locked_message())

    user.failed_login_count = 0
    user.last_login_at = utcnow()
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    expired = password_expired(user, settings)
    audit.record(
        session, AuditAction.LOGIN_SUCCESS, user_id=user.id, username=user.username,
        entity_type="user", entity_id=user.id,
    )
    return LoginResult(
        LoginStatus.SUCCESS,
        user=SessionUser.from_user(user),
        must_change_password=user.must_change_password,
        password_expired=expired,
    )


def reauthenticate(
    session: Session,
    user_id: int,
    password: str,
    *,
    failure_action: AuditAction,
    success_action: AuditAction | None = None,
    context: dict | None = None,
) -> LoginResult:
    """Confirm the signed-in user's password again (screen unlock, e-signature).
    Wrong passwords count towards lockout just like at sign-in."""
    settings = get_security_settings(session)
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    context = context or {}
    if not user.is_active or user.is_locked:
        audit.record(
            session, failure_action, user_id=user.id, username=user.username, entity_type="user",
            entity_id=user.id, new={**context, "cause": "account inactive or locked"},
        )
        return LoginResult(LoginStatus.LOCKED, _locked_message())
    if not passwords.verify_password(user.password_hash, password):
        result = _register_failure(session, user, settings, failure_action, {**context, "cause": "wrong password"})
        if result.status is LoginStatus.INVALID_CREDENTIALS:
            remaining = settings.max_failed_logins - user.failed_login_count
            result = LoginResult(
                LoginStatus.INVALID_CREDENTIALS,
                f"Incorrect password. {remaining} attempt{'s' if remaining != 1 else ''} left before the account is locked.",
            )
        return result
    user.failed_login_count = 0
    if success_action is not None:
        audit.record(
            session, success_action, user_id=user.id, username=user.username,
            entity_type="user", entity_id=user.id, new=context or None,
        )
    return LoginResult(LoginStatus.SUCCESS, user=SessionUser.from_user(user))


def logout(session: Session, user: SessionUser, trigger: str = "user") -> None:
    audit.record(session, AuditAction.LOGOUT, actor=user, entity_type="user", entity_id=user.id, new={"trigger": trigger})


def record_session_locked(session: Session, user: SessionUser, trigger: str = "inactivity") -> None:
    audit.record(session, AuditAction.SESSION_LOCKED, actor=user, entity_type="user", entity_id=user.id,
                 new={"trigger": trigger})


def check_new_password(
    session: Session, user: User | None, username: str, new_password: str, settings: SecuritySettings
) -> None:
    """Raise ``ValidationError`` if ``new_password`` breaks the policy or was used recently."""
    problems = passwords.password_problems(new_password, min_length=settings.password_min_length, username=username)
    if problems:
        raise ValidationError("The password must " + "; ".join(problems) + ".")
    if user is not None and settings.password_history_count > 0:
        recent = session.scalars(
            select(PasswordHistory.password_hash)
            .where(PasswordHistory.user_id == user.id)
            .order_by(PasswordHistory.id.desc())
            .limit(settings.password_history_count)
        )
        if any(passwords.verify_password(old_hash, new_password) for old_hash in recent):
            raise ValidationError(
                f"The new password must be different from your last {settings.password_history_count} passwords."
            )


def set_password(session: Session, user: User, new_password: str, *, must_change: bool) -> None:
    """Store a new password hash and remember it in the password history."""
    user.password_hash = passwords.hash_password(new_password)
    user.password_changed_at = utcnow()
    user.must_change_password = must_change
    session.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
    session.flush()


def change_password(session: Session, user_id: int, current_password: str, new_password: str) -> None:
    """A user changing their own password."""
    settings = get_security_settings(session)
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    if not passwords.verify_password(user.password_hash, current_password):
        raise ValidationError("The current password is incorrect.")
    if passwords.verify_password(user.password_hash, new_password):
        raise ValidationError("The new password must be different from the current password.")
    check_new_password(session, user, user.username, new_password, settings)
    set_password(session, user, new_password, must_change=False)
    audit.record(
        session, AuditAction.PASSWORD_CHANGED, user_id=user.id, username=user.username,
        entity_type="user", entity_id=user.id,
    )


def password_expires_in_days(session: Session, user_id: int) -> int | None:
    """Days until the user's password expires, or ``None`` if it never does."""
    settings = get_security_settings(session)
    user = session.get(User, user_id)
    if user is None or settings.password_expiry_days <= 0:
        return None
    remaining = user.password_changed_at + timedelta(days=settings.password_expiry_days) - utcnow()
    return max(0, remaining.days)


# ---------------------------------------------------------------------------
# Reload: hand the signed-in session over to the restarted app
# ---------------------------------------------------------------------------

RESUME_KEY = "resume_token"
RESUME_LIFETIME = timedelta(seconds=60)


def create_resume_token(session: Session, user: SessionUser) -> str:
    """A single-use code (valid for one minute) that lets the restarted app carry on
    the same user's session without a password. Only its hash is stored.
    Only Master Administrators and IT Support may reload the app."""
    require(user, Permission.RELOAD_APP)
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    value = f"{user.id}|{digest}|{(utcnow() + RESUME_LIFETIME).isoformat()}"
    row = session.get(AppSetting, RESUME_KEY)
    if row is None:
        session.add(AppSetting(key=RESUME_KEY, value=value, updated_by_id=user.id))
    else:
        row.value, row.updated_at, row.updated_by_id = value, utcnow(), user.id
    session.flush()
    return token


def resume_session(session: Session, token: str | None) -> SessionUser | None:
    """Exchange a reload code for the signed-in user. The code is consumed whether or
    not it is valid; ``None`` means the user must sign in normally."""
    row = session.get(AppSetting, RESUME_KEY)
    if row is None or not token:
        return None
    stored = row.value
    session.delete(row)
    session.flush()
    try:
        user_id, digest, expires = stored.split("|")
        valid = (secrets.compare_digest(digest, hashlib.sha256(token.encode()).hexdigest())
                 and utcnow() <= datetime.fromisoformat(expires))
    except ValueError:
        valid = False
    if not valid:
        return None
    user = session.get(User, int(user_id))
    if user is None or not user.is_active or user.is_locked or user.must_change_password \
            or password_expired(user, get_security_settings(session)) \
            or not SessionUser.from_user(user).can(Permission.RELOAD_APP):
        return None
    user.last_login_at = utcnow()
    audit.record(session, AuditAction.LOGIN_SUCCESS, user_id=user.id, username=user.username,
                 entity_type="user", entity_id=user.id, new={"method": "app reload (same session)"})
    return SessionUser.from_user(user)
