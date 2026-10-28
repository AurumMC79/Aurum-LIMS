"""Laboratory details printed on certificates (name, address, contact, statement)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from aurum_lims.db.models import AppSetting, utcnow
from aurum_lims.errors import ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

DEFAULT_STATEMENT = (
    "The results relate only to the sample tested. This certificate shall not be reproduced except in full "
    "without the written approval of the laboratory."
)
LIMITS = {"lab_name": 120, "lab_address": 400, "lab_contact": 200, "coa_statement": 1000, "paper_size": 10}
PAPER_SIZES = ("Letter", "A4")


@dataclass(frozen=True)
class LabInfo:
    lab_name: str = ""
    lab_address: str = ""
    lab_contact: str = ""
    coa_statement: str = DEFAULT_STATEMENT
    paper_size: str = "Letter"


def get_lab_info(session: Session) -> LabInfo:
    values = {}
    for key in LIMITS:
        row = session.get(AppSetting, key)
        if row is not None:
            values[key] = row.value
    return LabInfo(**values)


def set_lab_info(session: Session, actor: SessionUser, info: LabInfo, reason: str) -> LabInfo:
    require(actor, Permission.MANAGE_SETTINGS)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    new = {k: (v or "").strip() for k, v in asdict(info).items()}
    if not new["lab_name"]:
        raise ValidationError("The laboratory name is required; it is printed on every certificate.")
    if new["paper_size"] not in PAPER_SIZES:
        raise ValidationError("Paper size must be Letter or A4.")
    for key, limit in LIMITS.items():
        if len(new[key]) > limit:
            raise ValidationError(f"{key.replace('_', ' ').capitalize()} must be {limit} characters or fewer.")
    old = asdict(get_lab_info(session))
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key in changed:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=new[key])
            session.add(row)
        row.value = new[key]
        row.updated_at = utcnow()
        row.updated_by_id = actor.id
    audit.record(session, AuditAction.SETTING_CHANGED, actor=actor, entity_type="setting", entity_id="lab_details",
                 old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    session.flush()
    return LabInfo(**new)
