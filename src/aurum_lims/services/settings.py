"""Security settings administrators can change from the Settings page."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import AppSetting, utcnow
from aurum_lims.errors import ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction


@dataclass(frozen=True)
class SecuritySettings:
    session_timeout_minutes: int = 15
    max_failed_logins: int = 5
    password_min_length: int = 10
    password_expiry_days: int = 90
    password_history_count: int = 5


@dataclass(frozen=True)
class SettingInfo:
    label: str
    help: str
    minimum: int
    maximum: int


SETTING_INFO = {
    "session_timeout_minutes": SettingInfo(
        "Lock screen after inactivity (minutes)", "The screen locks and the password must be re-entered.", 1, 240
    ),
    "max_failed_logins": SettingInfo(
        "Failed sign-ins before lockout", "An administrator must unlock the account afterwards.", 3, 10
    ),
    "password_min_length": SettingInfo("Minimum password length", "Applies to new passwords.", 8, 64),
    "password_expiry_days": SettingInfo(
        "Password expiry (days)", "Users must choose a new password after this many days. 0 = never.", 0, 365
    ),
    "password_history_count": SettingInfo(
        "Remembered previous passwords", "Users cannot re-use this many of their recent passwords.", 0, 24
    ),
}


def get_security_settings(session: Session) -> SecuritySettings:
    stored = {row.key: row.value for row in session.scalars(select(AppSetting))}
    values = {}
    for f in fields(SecuritySettings):
        if f.name in stored:
            try:
                values[f.name] = int(stored[f.name])
            except ValueError:
                pass
    return replace(SecuritySettings(), **values)


def update_security_settings(
    session: Session, actor: SessionUser, changes: dict[str, int], reason: str
) -> SecuritySettings:
    require(actor, Permission.MANAGE_SETTINGS)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    current = asdict(get_security_settings(session))
    for key, value in changes.items():
        if key not in SETTING_INFO:
            raise ValidationError(f"Unknown setting: {key}")
        info = SETTING_INFO[key]
        if not info.minimum <= int(value) <= info.maximum:
            raise ValidationError(f"{info.label} must be between {info.minimum} and {info.maximum}.")

    for key, value in changes.items():
        value = int(value)
        if current[key] == value:
            continue
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=str(value))
            session.add(row)
        row.value = str(value)
        row.updated_at = utcnow()
        row.updated_by_id = actor.id
        audit.record(
            session,
            AuditAction.SETTING_CHANGED,
            actor=actor,
            entity_type="setting",
            entity_id=key,
            old={key: current[key]},
            new={key: value},
            reason=reason.strip(),
        )
    session.flush()
    return get_security_settings(session)
