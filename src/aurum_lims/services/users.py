"""User account management. Accounts are never deleted, only deactivated."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import User, utcnow
from aurum_lims.errors import NotFound, PermissionDenied, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import PRIVILEGED_ROLES, Permission, Role, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.auth import check_new_password, normalize_username, set_password
from aurum_lims.services.settings import get_security_settings

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
USERNAME_RULES = "2-64 characters: letters, numbers, dots, dashes or underscores, starting with a letter or number."


def has_any_users(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(User)) > 0


def _clean_username(session: Session, username: str) -> str:
    name = normalize_username(username)
    if not USERNAME_PATTERN.match(name):
        raise ValidationError(f"Username must be {USERNAME_RULES}")
    if session.scalar(select(User.id).where(User.username == name)) is not None:
        raise ValidationError(f"The username '{name}' is already taken (usernames are never re-used).")
    return name


def _clean_full_name(full_name: str) -> str:
    name = " ".join(full_name.split())
    if not name:
        raise ValidationError("Full name is required.")
    if len(name) > 128:
        raise ValidationError("Full name must be 128 characters or fewer.")
    return name


def _require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    return reason.strip()


def _get(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    return user


ADMIN_ROLES = (Role.MASTER_ADMINISTRATOR, Role.ADMINISTRATOR)


def _other_active(session: Session, excluding_id: int, roles) -> int:
    return session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role.in_([r.value for r in roles]), User.is_active.is_(True), User.id != excluding_id)
    )


def _other_active_admins(session: Session, excluding_id: int) -> int:
    return _other_active(session, excluding_id, ADMIN_ROLES)


def _require_privilege_for(actor: SessionUser, *roles: Role | str) -> None:
    """Only a Master Administrator may give, take away or manage the Master Administrator / IT Support roles."""
    if any(Role(r) in PRIVILEGED_ROLES for r in roles) and not actor.can(Permission.MANAGE_PRIVILEGED_ROLES):
        raise PermissionDenied("Only a Master Administrator can manage Master Administrator and IT Support accounts.")


def ensure_master_administrator(session: Session) -> User | None:
    """Make sure the system has a Master Administrator. If there is none (e.g. a database
    set up before the role existed), the Administrator created at first-run set-up is
    promoted. Returns the promoted user, or ``None`` if nothing changed."""
    if session.scalar(select(User.id).where(User.role == Role.MASTER_ADMINISTRATOR,
                                            User.is_active.is_(True)).limit(1)) is not None:
        return None
    first_admin = session.scalar(
        select(User).where(User.role == Role.ADMINISTRATOR, User.is_active.is_(True)).order_by(User.id).limit(1))
    if first_admin is None:
        return None
    first_admin.role = Role.MASTER_ADMINISTRATOR
    audit.record(
        session, AuditAction.USER_UPDATED, entity_type="user", entity_id=first_admin.id,
        old={"role": Role.ADMINISTRATOR.value}, new={"role": Role.MASTER_ADMINISTRATOR.value},
        reason="Master Administrator role introduced; given to the administrator created at first-run set-up",
    )
    session.flush()
    return first_admin


def describe(user: User) -> dict:
    return {"username": user.username, "full_name": user.full_name, "role": user.role}


def create_initial_admin(session: Session, username: str, full_name: str, password: str) -> SessionUser:
    """First-run set-up: create the very first account, as Master Administrator."""
    if has_any_users(session):
        raise ValidationError("The initial administrator has already been created.")
    name = _clean_username(session, username)
    full = _clean_full_name(full_name)
    check_new_password(session, None, name, password, get_security_settings(session))
    user = User(
        username=name, full_name=full, role=Role.MASTER_ADMINISTRATOR, password_hash="", is_active=True,
        is_locked=False, failed_login_count=0, must_change_password=False, password_changed_at=utcnow(),
    )
    session.add(user)
    session.flush()
    set_password(session, user, password, must_change=False)
    audit.record(
        session, AuditAction.USER_CREATED, user_id=user.id, username=user.username, entity_type="user",
        entity_id=user.id, new=describe(user), reason="Initial administrator created during first-run set-up",
    )
    return SessionUser.from_user(user)


def list_users(session: Session, actor: SessionUser) -> list[User]:
    require(actor, Permission.MANAGE_USERS)
    return list(session.scalars(select(User).order_by(User.is_active.desc(), User.username)))


def count_users(session: Session) -> dict[str, int]:
    rows = session.execute(select(User.is_active, User.is_locked)).all()
    return {
        "active": sum(1 for active, _ in rows if active),
        "locked": sum(1 for active, locked in rows if active and locked),
        "inactive": sum(1 for active, _ in rows if not active),
    }


def create_user(
    session: Session,
    actor: SessionUser,
    *,
    username: str,
    full_name: str,
    role: Role | str,
    temporary_password: str,
    reason: str | None = None,
) -> User:
    """Create an account. The person must choose their own password at first sign-in."""
    require(actor, Permission.MANAGE_USERS)
    name = _clean_username(session, username)
    full = _clean_full_name(full_name)
    role = Role(role)
    _require_privilege_for(actor, role)
    check_new_password(session, None, name, temporary_password, get_security_settings(session))
    user = User(
        username=name, full_name=full, role=role, password_hash="", is_active=True, is_locked=False,
        failed_login_count=0, must_change_password=True, password_changed_at=utcnow(), created_by_id=actor.id,
    )
    session.add(user)
    session.flush()
    set_password(session, user, temporary_password, must_change=True)
    audit.record(
        session, AuditAction.USER_CREATED, actor=actor, entity_type="user", entity_id=user.id,
        new=describe(user), reason=(reason or "").strip() or None,
    )
    return user


def update_user(
    session: Session,
    actor: SessionUser,
    user_id: int,
    *,
    full_name: str | None = None,
    role: Role | str | None = None,
    reason: str,
) -> User:
    require(actor, Permission.MANAGE_USERS)
    reason = _require_reason(reason)
    user = _get(session, user_id)
    _require_privilege_for(actor, user.role, *([role] if role is not None else []))
    old = describe(user)
    if full_name is not None:
        user.full_name = _clean_full_name(full_name)
    if role is not None:
        role = Role(role)
        if user.role in ADMIN_ROLES and role not in ADMIN_ROLES and user.is_active:
            if _other_active_admins(session, user.id) == 0:
                raise ValidationError("At least one active Administrator must remain.")
        if user.role == Role.MASTER_ADMINISTRATOR and role != Role.MASTER_ADMINISTRATOR and user.is_active:
            if _other_active(session, user.id, [Role.MASTER_ADMINISTRATOR]) == 0:
                raise ValidationError("At least one active Master Administrator must remain.")
        user.role = role
    new = describe(user)
    changed_old = {k: v for k, v in old.items() if new[k] != v}
    if not changed_old:
        raise ValidationError("Nothing was changed.")
    audit.record(
        session, AuditAction.USER_UPDATED, actor=actor, entity_type="user", entity_id=user.id,
        old=changed_old, new={k: new[k] for k in changed_old}, reason=reason,
    )
    return user


def reset_password(session: Session, actor: SessionUser, user_id: int, temporary_password: str, reason: str) -> User:
    """Give a user a new temporary password. Also clears any lockout."""
    require(actor, Permission.MANAGE_USERS)
    reason = _require_reason(reason)
    user = _get(session, user_id)
    _require_privilege_for(actor, user.role)  # no taking over a Master Administrator / IT account
    check_new_password(session, None, user.username, temporary_password, get_security_settings(session))
    set_password(session, user, temporary_password, must_change=True)
    was_locked = user.is_locked
    user.is_locked = False
    user.failed_login_count = 0
    audit.record(
        session, AuditAction.PASSWORD_RESET, actor=actor, entity_type="user", entity_id=user.id,
        new={"username": user.username, "must_change_password": True, "unlocked": was_locked}, reason=reason,
    )
    return user


def unlock_user(session: Session, actor: SessionUser, user_id: int, reason: str) -> User:
    require(actor, Permission.MANAGE_USERS)
    reason = _require_reason(reason)
    user = _get(session, user_id)
    if not user.is_locked:
        raise ValidationError("This account is not locked.")
    user.is_locked = False
    user.failed_login_count = 0
    audit.record(
        session, AuditAction.USER_UNLOCKED, actor=actor, entity_type="user", entity_id=user.id,
        new={"username": user.username}, reason=reason,
    )
    return user


def deactivate_user(session: Session, actor: SessionUser, user_id: int, reason: str) -> User:
    require(actor, Permission.MANAGE_USERS)
    reason = _require_reason(reason)
    user = _get(session, user_id)
    if user.id == actor.id:
        raise ValidationError("You cannot deactivate your own account.")
    _require_privilege_for(actor, user.role)
    if not user.is_active:
        raise ValidationError("This account is already inactive.")
    if user.role in ADMIN_ROLES and _other_active_admins(session, user.id) == 0:
        raise ValidationError("At least one active Administrator must remain.")
    if user.role == Role.MASTER_ADMINISTRATOR and _other_active(session, user.id, [Role.MASTER_ADMINISTRATOR]) == 0:
        raise ValidationError("At least one active Master Administrator must remain.")
    user.is_active = False
    audit.record(
        session, AuditAction.USER_DEACTIVATED, actor=actor, entity_type="user", entity_id=user.id,
        new={"username": user.username}, reason=reason,
    )
    return user


def reactivate_user(session: Session, actor: SessionUser, user_id: int, reason: str) -> User:
    require(actor, Permission.MANAGE_USERS)
    reason = _require_reason(reason)
    user = _get(session, user_id)
    _require_privilege_for(actor, user.role)
    if user.is_active:
        raise ValidationError("This account is already active.")
    user.is_active = True
    user.failed_login_count = 0
    audit.record(
        session, AuditAction.USER_REACTIVATED, actor=actor, entity_type="user", entity_id=user.id,
        new={"username": user.username}, reason=reason,
    )
    return user
