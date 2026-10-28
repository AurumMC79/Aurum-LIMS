"""Electronic signatures (21 CFR Part 11 style).

A signature requires the signer to re-enter their password at the moment of
signing, and permanently records who signed, when, and what the signature
means. Approval workflows in later phases call ``sign`` before changing a
record's status, and only proceed if the result is ``ok``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import ElectronicSignature
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.auth import reauthenticate


class SignatureMeaning(StrEnum):
    PERFORMED = "Performed by"
    REVIEWED = "Reviewed by"
    APPROVED = "Approved by"
    REJECTED = "Rejected by"


MEANING_PERMISSION = {
    SignatureMeaning.PERFORMED: Permission.SIGN_RECORDS,
    SignatureMeaning.REVIEWED: Permission.REVIEW_RECORDS,
    SignatureMeaning.APPROVED: Permission.APPROVE_RECORDS,
    SignatureMeaning.REJECTED: Permission.REVIEW_RECORDS,
}


@dataclass(frozen=True)
class SignatureResult:
    ok: bool
    message: str = ""
    signature: ElectronicSignature | None = None


def sign(
    session: Session,
    signer: SessionUser,
    password: str,
    meaning: SignatureMeaning | str,
    entity_type: str,
    entity_id,
    reason: str | None = None,
) -> SignatureResult:
    """Apply an electronic signature. Failed attempts are audited and count
    towards account lockout, so the caller must commit either way."""
    meaning = SignatureMeaning(meaning)
    require(signer, MEANING_PERMISSION[meaning])
    context = {"meaning": str(meaning), "entity_type": entity_type, "entity_id": str(entity_id)}
    check = reauthenticate(session, signer.id, password, failure_action=AuditAction.ESIGN_FAILED, context=context)
    if not check.ok:
        return SignatureResult(False, check.message)

    entry = audit.record(
        session, AuditAction.ESIGN_APPLIED, actor=signer, entity_type=entity_type, entity_id=entity_id,
        new={"meaning": str(meaning), "signer": signer.full_name}, reason=reason,
    )
    signature = ElectronicSignature(
        user_id=signer.id,
        username=signer.username,
        full_name=signer.full_name,
        meaning=str(meaning),
        entity_type=entity_type,
        entity_id=str(entity_id),
        reason=reason,
        audit_entry_id=entry.id,
    )
    session.add(signature)
    session.flush()
    return SignatureResult(True, "Signature applied.", signature)


def signatures_for(session: Session, entity_type: str, entity_id) -> list[ElectronicSignature]:
    return list(
        session.scalars(
            select(ElectronicSignature)
            .where(ElectronicSignature.entity_type == entity_type, ElectronicSignature.entity_id == str(entity_id))
            .order_by(ElectronicSignature.id)
        )
    )
