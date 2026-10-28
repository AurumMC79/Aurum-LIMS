"""Append-only, tamper-evident audit trail.

Each entry stores a SHA-256 hash of its own contents plus the hash of the entry
before it. Changing, removing or inserting a row anywhere in the trail breaks
the chain, which ``verify_integrity`` detects. Database triggers additionally
reject UPDATE and DELETE statements on the table.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from aurum_lims.db.models import AuditEntry
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require

GENESIS_HASH = "0" * 64
SYSTEM_USERNAME = "system"


class AuditAction(StrEnum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    SESSION_LOCKED = "SESSION_LOCKED"
    SESSION_UNLOCKED = "SESSION_UNLOCKED"
    SESSION_UNLOCK_FAILED = "SESSION_UNLOCK_FAILED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET = "PASSWORD_RESET"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_UNLOCKED = "USER_UNLOCKED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    USER_REACTIVATED = "USER_REACTIVATED"
    SETTING_CHANGED = "SETTING_CHANGED"
    ESIGN_APPLIED = "ESIGN_APPLIED"
    ESIGN_FAILED = "ESIGN_FAILED"
    AUDIT_EXPORTED = "AUDIT_EXPORTED"
    AUDIT_VERIFIED = "AUDIT_VERIFIED"
    SAMPLE_REGISTERED = "SAMPLE_REGISTERED"
    SAMPLE_UPDATED = "SAMPLE_UPDATED"
    SAMPLE_RECEIVED = "SAMPLE_RECEIVED"
    SAMPLE_TRANSFERRED = "SAMPLE_TRANSFERRED"
    SAMPLE_ALIQUOTED = "SAMPLE_ALIQUOTED"
    SAMPLE_DISPOSED = "SAMPLE_DISPOSED"
    SAMPLE_CANCELLED = "SAMPLE_CANCELLED"
    SAMPLE_TYPE_CREATED = "SAMPLE_TYPE_CREATED"
    SAMPLE_TYPE_UPDATED = "SAMPLE_TYPE_UPDATED"
    LOCATION_CREATED = "LOCATION_CREATED"
    LOCATION_UPDATED = "LOCATION_UPDATED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_json(values: dict[str, Any] | None) -> str | None:
    if values is None:
        return None
    return json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)


def compute_hash(entry: AuditEntry) -> str:
    payload = {
        "timestamp_utc": entry.timestamp_utc,
        "user_id": entry.user_id,
        "username": entry.username,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "old_values": entry.old_values,
        "new_values": entry.new_values,
        "reason": entry.reason,
        "prev_hash": entry.prev_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record(
    session: Session,
    action: AuditAction | str,
    *,
    actor: SessionUser | None = None,
    user_id: int | None = None,
    username: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditEntry:
    """Append an entry to the audit trail (in the caller's transaction)."""
    if actor is not None:
        user_id, username = actor.id, actor.username
    prev_hash = session.scalar(select(AuditEntry.entry_hash).order_by(AuditEntry.id.desc()).limit(1))
    entry = AuditEntry(
        timestamp_utc=_now_iso(),
        user_id=user_id,
        username=(username or SYSTEM_USERNAME)[:64],
        action=str(action),
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        old_values=_to_json(old),
        new_values=_to_json(new),
        reason=reason or None,
        prev_hash=prev_hash or GENESIS_HASH,
    )
    entry.entry_hash = compute_hash(entry)
    session.add(entry)
    session.flush()
    return entry


@dataclass
class IntegrityReport:
    entries_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_integrity(session: Session, actor: SessionUser | None = None, *, record_result: bool = True) -> IntegrityReport:
    """Re-walk the whole hash chain and report anything that does not add up."""
    if actor is not None:
        require(actor, Permission.VIEW_AUDIT_TRAIL)
    report = IntegrityReport()
    expected_prev = GENESIS_HASH
    previous_id = 0
    for entry in session.scalars(select(AuditEntry).order_by(AuditEntry.id)).yield_per(500):
        report.entries_checked += 1
        if entry.id != previous_id + 1:
            report.problems.append(f"Entries {previous_id + 1}-{entry.id - 1} are missing.")
        if entry.prev_hash != expected_prev:
            report.problems.append(f"Entry {entry.id} is not linked to the entry before it.")
        if compute_hash(entry) != entry.entry_hash:
            report.problems.append(f"Entry {entry.id} has been altered (its contents do not match its hash).")
        expected_prev = entry.entry_hash
        previous_id = entry.id

    if session.get_bind().dialect.name == "sqlite":
        # sqlite_sequence remembers the highest id ever issued, which exposes
        # entries deleted from the end of the trail.
        highest_issued = session.execute(
            text("SELECT seq FROM sqlite_sequence WHERE name = 'audit_entries'")
        ).scalar()
        if highest_issued and highest_issued > previous_id:
            report.problems.append(f"Entries {previous_id + 1}-{highest_issued} are missing from the end of the trail.")

    if record_result and actor is not None:
        record(
            session,
            AuditAction.AUDIT_VERIFIED,
            actor=actor,
            entity_type="audit_trail",
            new={"entries_checked": report.entries_checked, "ok": report.ok, "problems": len(report.problems)},
        )
    return report


def query_entries(
    session: Session,
    actor: SessionUser,
    *,
    search: str | None = None,
    action: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = 5000,
) -> list[AuditEntry]:
    """Newest-first audit entries matching the filters."""
    require(actor, Permission.VIEW_AUDIT_TRAIL)
    stmt = select(AuditEntry).order_by(AuditEntry.id.desc())
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    if start is not None:
        stmt = stmt.where(AuditEntry.timestamp_utc >= _iso(start))
    if end is not None:
        stmt = stmt.where(AuditEntry.timestamp_utc < _iso(end))
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                AuditEntry.username.ilike(pattern),
                AuditEntry.entity_type.ilike(pattern),
                AuditEntry.entity_id.ilike(pattern),
                AuditEntry.old_values.ilike(pattern),
                AuditEntry.new_values.ilike(pattern),
                AuditEntry.reason.ilike(pattern),
            )
        )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


CSV_COLUMNS = [
    "id", "timestamp_utc", "user_id", "username", "action", "entity_type", "entity_id",
    "old_values", "new_values", "reason", "prev_hash", "entry_hash",
]


def export_csv(session: Session, actor: SessionUser, path: Path, entries: list[AuditEntry]) -> None:
    """Write ``entries`` to a CSV file and record that the export happened."""
    require(actor, Permission.EXPORT_AUDIT_TRAIL)
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for entry in entries:
            writer.writerow([getattr(entry, column) for column in CSV_COLUMNS])
    record(
        session,
        AuditAction.AUDIT_EXPORTED,
        actor=actor,
        entity_type="audit_trail",
        new={"file": str(path), "entries": len(entries)},
    )
