"""Out-of-specification (OOS) investigations.

An investigation is opened automatically when an analyst signs a test with a
result outside its limit. Analysts and QA record the investigation; QA closes
it with a conclusion and an electronic signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import OOSInvestigation, Sample, User, utcnow
from aurum_lims.errors import NotFound, PermissionDenied, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, esign
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult

OPEN = "Open"
CLOSED = "Closed"
PHASES = ["Phase I – laboratory investigation", "Phase II – full-scale investigation"]
CONCLUSIONS = ["Confirmed OOS", "Invalidated – assignable laboratory error", "Inconclusive"]


@dataclass
class OOSRow:
    id: int
    number: str
    sample_pk: int
    sample_id: str
    parameter_name: str
    result_value: str
    spec_text: str | None
    status: str
    phase: str
    conclusion: str | None
    opened_at: datetime
    opened_by: str


def get(session: Session, actor: SessionUser, oos_id: int) -> OOSInvestigation:
    require(actor, Permission.VIEW_RECORDS)
    record = session.get(OOSInvestigation, oos_id)
    if record is None:
        raise NotFound("OOS investigation not found.")
    return record


def list_investigations(session: Session, actor: SessionUser, include_closed: bool = False) -> list[OOSRow]:
    require(actor, Permission.VIEW_RECORDS)
    stmt = (
        select(OOSInvestigation, Sample.sample_id, User.full_name)
        .join(Sample, OOSInvestigation.sample_id == Sample.id)
        .join(User, OOSInvestigation.opened_by_id == User.id)
        .order_by(OOSInvestigation.id.desc())
    )
    if not include_closed:
        stmt = stmt.where(OOSInvestigation.status == OPEN)
    return [
        OOSRow(o.id, o.number, o.sample_id, sid, o.parameter_name, o.result_value, o.spec_text, o.status, o.phase,
               o.conclusion, o.opened_at, opener)
        for o, sid, opener in session.execute(stmt)
    ]


def open_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(OOSInvestigation).where(OOSInvestigation.status == OPEN))


def _can_edit(actor: SessionUser) -> None:
    if not (actor.can(Permission.EDIT_RECORDS) or actor.can(Permission.REVIEW_RECORDS)):
        raise PermissionDenied("Your role does not allow updating OOS investigations.")


def update(session: Session, actor: SessionUser, oos_id: int, *, phase: str, investigation_notes: str | None,
           root_cause: str | None) -> OOSInvestigation:
    _can_edit(actor)
    record = get(session, actor, oos_id)
    if record.status != OPEN:
        raise ValidationError("This investigation is closed.")
    if phase not in PHASES:
        raise ValidationError("Choose the investigation phase.")
    new = {"phase": phase, "investigation_notes": (investigation_notes or "").strip() or None,
           "root_cause": (root_cause or "").strip() or None}
    old = {k: getattr(record, k) for k in new}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key, value in new.items():
        setattr(record, key, value)
    audit.record(session, AuditAction.OOS_UPDATED, actor=actor, entity_type="oos", entity_id=record.number,
                 old=changed, new={k: new[k] for k in changed})
    return record


def close(session: Session, actor: SessionUser, oos_id: int, *, conclusion: str, root_cause: str,
          password: str) -> SignatureResult:
    """Close with a conclusion (QA electronic signature, meaning 'Approved by').
    Test reviewers such as Supervisors and Lead Analysts cannot close OOS investigations."""
    require(actor, Permission.APPROVE_RECORDS)
    record = get(session, actor, oos_id)
    if record.status != OPEN:
        raise ValidationError("This investigation is already closed.")
    if conclusion not in CONCLUSIONS:
        raise ValidationError("Choose a conclusion.")
    root_cause = (root_cause or "").strip()
    if not root_cause:
        raise ValidationError("Describe the root cause / justification before closing.")
    result = esign.sign(session, actor, password, SignatureMeaning.APPROVED, "oos", record.number,
                        reason=f"OOS closed: {conclusion}")
    if not result.ok:
        return result
    record.status = CLOSED
    record.conclusion = conclusion
    record.root_cause = root_cause
    record.closed_at = utcnow()
    record.closed_by_id = actor.id
    record.signature_id = result.signature.id
    audit.record(session, AuditAction.OOS_CLOSED, actor=actor, entity_type="oos", entity_id=record.number,
                 new={"conclusion": conclusion, "root_cause": root_cause})
    return result
