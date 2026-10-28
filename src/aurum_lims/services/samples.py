"""Samples and their chain of custody.

Lifecycle (testing statuses are used from Phase 3 onwards)::

    Registered ──receive──> Received ──(testing)──> ... ──dispose──> Disposed
        └──cancel──> Cancelled

Every physical event (registration, receipt, transfer, aliquoting, disposal)
adds an immutable custody event and an audit-trail entry.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import CustodyEvent, Sample, SampleType, StorageLocation, User, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, esign, sample_ids
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult
from aurum_lims.services.sample_types import CATEGORY_FIELDS, FIELD_LABELS, Category

# Allowance for clocks on different PCs when checking "not in the future".
CLOCK_TOLERANCE = timedelta(minutes=5)
MAX_BATCH = 100
MAX_ALIQUOTS = 50


class SampleStatus(StrEnum):
    REGISTERED = "Registered"
    RECEIVED = "Received"
    IN_TESTING = "In Testing"
    TESTING_COMPLETE = "Testing Complete"
    REVIEWED = "Reviewed"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    DISPOSED = "Disposed"
    CANCELLED = "Cancelled"


CLOSED_STATUSES = {SampleStatus.DISPOSED, SampleStatus.CANCELLED}
# Statuses in which the sample is physically in the lab and can be moved.
ON_HAND_STATUSES = {
    SampleStatus.RECEIVED, SampleStatus.IN_TESTING, SampleStatus.TESTING_COMPLETE,
    SampleStatus.REVIEWED, SampleStatus.APPROVED, SampleStatus.REJECTED,
}


class CustodyEventType(StrEnum):
    REGISTERED = "Registered"
    RECEIVED = "Received"
    TRANSFERRED = "Transferred"
    ALIQUOTED = "Aliquots taken"
    CREATED_FROM_PARENT = "Created as aliquot"
    DISPOSED = "Disposed"
    CANCELLED = "Cancelled"
    DETAILS_CORRECTED = "Details corrected"


PRIORITIES = ["Normal", "Urgent"]
RECEIPT_CONDITIONS = ["Acceptable", "Damaged container", "Temperature excursion", "Insufficient quantity", "Other"]
DISPOSAL_METHODS = [
    "Destroyed / discarded", "Consumed in testing", "Returned to supplier / client", "Sent off-site", "Other",
]

# Fields a user may correct after registration.
EDITABLE_FIELDS = [
    "material_name", "lot_number", "supplier", "sampling_point", "stability_study", "stability_timepoint",
    "stability_condition", "quantity", "quantity_unit", "container", "priority", "due_date", "sampled_at",
    "sampled_by", "notes",
]
EXTRA_LABELS = {
    "quantity": "Quantity", "quantity_unit": "Unit", "container": "Container", "priority": "Priority",
    "due_date": "Due date", "sampled_at": "Sampled at", "sampled_by": "Sampled by", "notes": "Notes",
}
TEXT_LIMITS = {
    "material_name": 200, "lot_number": 100, "supplier": 200, "sampling_point": 200, "stability_study": 100,
    "stability_timepoint": 50, "stability_condition": 100, "quantity_unit": 16, "container": 100, "sampled_by": 128,
}


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name) or EXTRA_LABELS.get(name) or name.replace("_", " ").capitalize()


@dataclass
class SampleDetails:
    """Descriptive information entered when registering or correcting a sample."""

    material_name: str | None = None
    lot_number: str | None = None
    supplier: str | None = None
    sampling_point: str | None = None
    stability_study: str | None = None
    stability_timepoint: str | None = None
    stability_condition: str | None = None
    quantity: str | None = None
    quantity_unit: str | None = None
    container: str | None = None
    priority: str = "Normal"
    due_date: date | None = None
    sampled_at: datetime | None = None
    sampled_by: str | None = None
    notes: str | None = None


@dataclass
class Receipt:
    location_id: int
    received_at: datetime | None = None
    condition: str = "Acceptable"
    temperature: str | None = None
    notes: str | None = None


@dataclass
class SampleSummary:
    """A flat, session-independent view of a sample for lists and screens."""

    id: int
    sample_id: str
    type_name: str
    type_code: str
    category: str
    status: str
    material_name: str | None
    lot_number: str | None
    location_id: int | None
    holder_name: str | None
    priority: str
    due_date: date | None
    created_at: datetime
    received_at: datetime | None
    parent_sample_id: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _clean_text(value) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split()) if not isinstance(value, str) or "\n" not in value else value.strip()
    return value or None


def _normalize(details: SampleDetails) -> SampleDetails:
    data = asdict(details)
    for name in data:
        if name in ("due_date", "sampled_at"):
            continue
        data[name] = _clean_text(data[name])
    data["priority"] = data["priority"] or "Normal"
    return SampleDetails(**data)


def _validate_details(details: SampleDetails, category: Category) -> None:
    missing = [field_label(name) for name, required in CATEGORY_FIELDS[category] if required and not getattr(details, name)]
    if missing:
        raise ValidationError("Please fill in: " + ", ".join(missing) + ".")
    for name, limit in TEXT_LIMITS.items():
        value = getattr(details, name)
        if value and len(value) > limit:
            raise ValidationError(f"{field_label(name)} must be {limit} characters or fewer.")
    if details.quantity:
        try:
            if Decimal(details.quantity) <= 0:
                raise ValidationError("Quantity must be greater than zero.")
        except InvalidOperation:
            raise ValidationError("Quantity must be a number, e.g. 250 or 0.5.") from None
        if not details.quantity_unit:
            raise ValidationError("Enter a unit for the quantity (e.g. g, mL, units).")
    if details.priority not in PRIORITIES:
        raise ValidationError("Priority must be Normal or Urgent.")
    if details.sampled_at and details.sampled_at > utcnow() + CLOCK_TOLERANCE:
        raise ValidationError("The sampling time cannot be in the future.")


def _check_not_future(moment: datetime, label: str) -> None:
    if moment > utcnow() + CLOCK_TOLERANCE:
        raise ValidationError(f"The {label} time cannot be in the future.")


def _active_location(session: Session, location_id: int | None) -> StorageLocation:
    if location_id is None:
        raise ValidationError("Choose a storage location.")
    location = session.get(StorageLocation, location_id)
    if location is None or not location.is_active:
        raise ValidationError("Choose an active storage location.")
    return location


def _get(session: Session, sample_pk: int) -> Sample:
    sample = session.get(Sample, sample_pk)
    if sample is None:
        raise NotFound("Sample not found.")
    return sample


def _snapshot(sample: Sample) -> dict:
    data = {name: getattr(sample, name) for name in EDITABLE_FIELDS}
    data["status"] = sample.status
    data["location_id"] = sample.location_id
    data["holder_id"] = sample.holder_id
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in data.items() if v not in (None, "")}


def _custody(
    session: Session,
    sample: Sample,
    actor: SessionUser,
    event: CustodyEventType,
    *,
    occurred_at: datetime | None = None,
    from_location_id: int | None = None,
    to_location_id: int | None = None,
    from_holder_id: int | None = None,
    to_holder_id: int | None = None,
    details: dict | None = None,
    notes: str | None = None,
) -> CustodyEvent:
    record = CustodyEvent(
        sample_id=sample.id, event=event.value, occurred_at=occurred_at or utcnow(), user_id=actor.id,
        username=actor.username, from_location_id=from_location_id, to_location_id=to_location_id,
        from_holder_id=from_holder_id, to_holder_id=to_holder_id,
        details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
        notes=_clean_text(notes),
    )
    session.add(record)
    session.flush()
    return record


def _audit(session, action, actor, sample, *, old=None, new=None, reason=None):
    audit.record(
        session, action, actor=actor, entity_type="sample", entity_id=sample.sample_id, old=old, new=new,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def register_samples(
    session: Session,
    actor: SessionUser,
    sample_type_id: int,
    details: SampleDetails,
    *,
    count: int = 1,
    receipt: Receipt | None = None,
) -> list[Sample]:
    """Register ``count`` samples with the same details. If ``receipt`` is given
    they are also marked as received into storage straight away."""
    require(actor, Permission.CREATE_RECORDS)
    sample_type = session.get(SampleType, sample_type_id)
    if sample_type is None or not sample_type.is_active:
        raise ValidationError("Choose an active sample type.")
    if not 1 <= count <= MAX_BATCH:
        raise ValidationError(f"You can register between 1 and {MAX_BATCH} samples at a time.")
    details = _normalize(details)
    _validate_details(details, Category(sample_type.category))
    if receipt is not None:
        _validate_receipt(session, receipt)

    created = []
    for index in range(count):
        sample = Sample(
            sample_id=sample_ids.next_sample_id(session, sample_type),
            sample_type_id=sample_type.id,
            status=SampleStatus.REGISTERED,
            created_by_id=actor.id,
            **asdict(details),
        )
        session.add(sample)
        session.flush()
        _custody(session, sample, actor, CustodyEventType.REGISTERED,
                 details={"batch": f"{index + 1} of {count}"} if count > 1 else None)
        _audit(session, AuditAction.SAMPLE_REGISTERED, actor, sample,
               new={"sample_type": sample_type.name, **_snapshot(sample)})
        if receipt is not None:
            _apply_receipt(session, actor, sample, receipt)
        created.append(sample)
    return created


def _validate_receipt(session: Session, receipt: Receipt) -> None:
    _active_location(session, receipt.location_id)
    if receipt.received_at is not None:
        _check_not_future(receipt.received_at, "receipt")
    if receipt.condition not in RECEIPT_CONDITIONS:
        raise ValidationError("Choose the condition of the sample on receipt.")
    if receipt.condition != "Acceptable" and not _clean_text(receipt.notes):
        raise ValidationError("Describe the problem in the notes when the condition is not Acceptable.")


def _apply_receipt(session: Session, actor: SessionUser, sample: Sample, receipt: Receipt) -> None:
    received_at = receipt.received_at or utcnow()
    sample.status = SampleStatus.RECEIVED
    sample.received_at = received_at
    sample.received_by_id = actor.id
    sample.receipt_condition = receipt.condition
    sample.receipt_temperature = _clean_text(receipt.temperature)
    sample.location_id = receipt.location_id
    sample.holder_id = None
    sample.updated_at = utcnow()
    _custody(
        session, sample, actor, CustodyEventType.RECEIVED, occurred_at=received_at,
        to_location_id=receipt.location_id,
        details={"condition": receipt.condition, "temperature": sample.receipt_temperature},
        notes=receipt.notes,
    )
    _audit(session, AuditAction.SAMPLE_RECEIVED, actor, sample, new={
        "received_at": received_at.isoformat(), "condition": receipt.condition,
        "temperature": sample.receipt_temperature, "location_id": receipt.location_id,
    })


def receive_sample(session: Session, actor: SessionUser, sample_pk: int, receipt: Receipt) -> Sample:
    require(actor, Permission.EDIT_RECORDS)
    sample = _get(session, sample_pk)
    if sample.status != SampleStatus.REGISTERED:
        raise ValidationError(f"{sample.sample_id} has already been received or is closed ({sample.status}).")
    _validate_receipt(session, receipt)
    _apply_receipt(session, actor, sample, receipt)
    return sample


def transfer_sample(
    session: Session,
    actor: SessionUser,
    sample_pk: int,
    *,
    to_location_id: int | None = None,
    to_holder_id: int | None = None,
    occurred_at: datetime | None = None,
    notes: str | None = None,
) -> Sample:
    """Move a sample to another storage location, or hand it to a person."""
    require(actor, Permission.EDIT_RECORDS)
    sample = _get(session, sample_pk)
    if sample.status not in ON_HAND_STATUSES:
        raise ValidationError(f"{sample.sample_id} cannot be moved while it is {sample.status}.")
    if (to_location_id is None) == (to_holder_id is None):
        raise ValidationError("Choose either a storage location or a person to transfer the sample to.")
    if to_location_id is not None:
        _active_location(session, to_location_id)
        if to_location_id == sample.location_id:
            raise ValidationError("The sample is already in that location.")
    else:
        holder = session.get(User, to_holder_id)
        if holder is None or not holder.is_active:
            raise ValidationError("Choose an active user to hand the sample to.")
        if to_holder_id == sample.holder_id:
            raise ValidationError("That person already has the sample.")
    if occurred_at is not None:
        _check_not_future(occurred_at, "transfer")
    from_location, from_holder = sample.location_id, sample.holder_id
    sample.location_id, sample.holder_id = to_location_id, to_holder_id
    sample.updated_at = utcnow()
    _custody(
        session, sample, actor, CustodyEventType.TRANSFERRED, occurred_at=occurred_at,
        from_location_id=from_location, to_location_id=to_location_id,
        from_holder_id=from_holder, to_holder_id=to_holder_id, notes=notes,
    )
    _audit(session, AuditAction.SAMPLE_TRANSFERRED, actor, sample,
           old={"location_id": from_location, "holder_id": from_holder},
           new={"location_id": to_location_id, "holder_id": to_holder_id})
    return sample


def create_aliquots(
    session: Session,
    actor: SessionUser,
    parent_pk: int,
    *,
    count: int,
    location_id: int,
    quantity: str | None = None,
    quantity_unit: str | None = None,
    notes: str | None = None,
) -> list[Sample]:
    """Split a sample into sub-samples named <parent ID>-A1, -A2, ..."""
    require(actor, Permission.CREATE_RECORDS)
    parent = _get(session, parent_pk)
    if parent.status not in ON_HAND_STATUSES:
        raise ValidationError(f"Aliquots can only be taken from a received sample ({parent.sample_id} is {parent.status}).")
    if not 1 <= count <= MAX_ALIQUOTS:
        raise ValidationError(f"You can create between 1 and {MAX_ALIQUOTS} aliquots at a time.")
    _active_location(session, location_id)
    details = _normalize(SampleDetails(**{name: getattr(parent, name) for name in EDITABLE_FIELDS}))
    details.quantity = _clean_text(quantity)
    details.quantity_unit = _clean_text(quantity_unit)
    details.notes = _clean_text(notes)
    _validate_details(details, Category(session.get(SampleType, parent.sample_type_id).category))

    existing = session.scalar(select(func.count()).select_from(Sample).where(Sample.parent_id == parent.id))
    now = utcnow()
    children = []
    for n in range(existing + 1, existing + count + 1):
        child_id = f"{parent.sample_id}-A{n}"
        if session.scalar(select(Sample.id).where(Sample.sample_id == child_id)) is not None:
            raise ValidationError(f"The ID {child_id} is already in use.")
        child = Sample(
            sample_id=child_id, sample_type_id=parent.sample_type_id, parent_id=parent.id,
            status=SampleStatus.RECEIVED, created_by_id=actor.id, received_at=now, received_by_id=actor.id,
            receipt_condition="Acceptable", location_id=location_id, **asdict(details),
        )
        session.add(child)
        session.flush()
        _custody(session, child, actor, CustodyEventType.CREATED_FROM_PARENT, occurred_at=now,
                 to_location_id=location_id, details={"parent": parent.sample_id})
        _audit(session, AuditAction.SAMPLE_REGISTERED, actor, child,
               new={"aliquot_of": parent.sample_id, **_snapshot(child)})
        children.append(child)
    _custody(session, parent, actor, CustodyEventType.ALIQUOTED, occurred_at=now,
             details={"aliquots": [c.sample_id for c in children]}, notes=notes)
    _audit(session, AuditAction.SAMPLE_ALIQUOTED, actor, parent, new={"aliquots": [c.sample_id for c in children]})
    parent.updated_at = now
    return children


def dispose_sample(
    session: Session,
    actor: SessionUser,
    sample_pk: int,
    *,
    method: str,
    password: str,
    occurred_at: datetime | None = None,
    notes: str | None = None,
) -> SignatureResult:
    """Dispose of a sample. Requires the user's electronic signature; the
    caller must commit even when the signature fails (it is audited)."""
    require(actor, Permission.EDIT_RECORDS)
    sample = _get(session, sample_pk)
    if sample.status == SampleStatus.REGISTERED:
        raise ValidationError(f"{sample.sample_id} was never received. Cancel the registration instead.")
    if sample.status not in ON_HAND_STATUSES:
        raise ValidationError(f"{sample.sample_id} is already {sample.status}.")
    if method not in DISPOSAL_METHODS:
        raise ValidationError("Choose how the sample was disposed of.")
    if method == "Other" and not _clean_text(notes):
        raise ValidationError("Describe the disposal method in the notes.")
    if occurred_at is not None:
        _check_not_future(occurred_at, "disposal")
    result = esign.sign(session, actor, password, SignatureMeaning.PERFORMED, "sample", sample.sample_id,
                        reason=f"Disposal: {method}")
    if not result.ok:
        return result
    when = occurred_at or utcnow()
    from_location, from_holder = sample.location_id, sample.holder_id
    sample.status = SampleStatus.DISPOSED
    sample.disposed_at = when
    sample.disposed_by_id = actor.id
    sample.disposal_method = method
    sample.location_id = sample.holder_id = None
    sample.updated_at = utcnow()
    _custody(session, sample, actor, CustodyEventType.DISPOSED, occurred_at=when, from_location_id=from_location,
             from_holder_id=from_holder, details={"method": method}, notes=notes)
    _audit(session, AuditAction.SAMPLE_DISPOSED, actor, sample,
           old={"location_id": from_location, "holder_id": from_holder},
           new={"status": sample.status, "method": method, "disposed_at": when.isoformat()})
    return result


def cancel_sample(session: Session, actor: SessionUser, sample_pk: int, reason: str) -> Sample:
    """Cancel a registration for a sample that never arrived."""
    require(actor, Permission.EDIT_RECORDS)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to cancel a sample.")
    sample = _get(session, sample_pk)
    if sample.status != SampleStatus.REGISTERED:
        raise ValidationError("Only samples that have not been received can be cancelled. Dispose of it instead.")
    sample.status = SampleStatus.CANCELLED
    sample.updated_at = utcnow()
    _custody(session, sample, actor, CustodyEventType.CANCELLED, notes=reason)
    _audit(session, AuditAction.SAMPLE_CANCELLED, actor, sample, old={"status": SampleStatus.REGISTERED},
           new={"status": sample.status}, reason=reason.strip())
    return sample


def update_details(session: Session, actor: SessionUser, sample_pk: int, details: SampleDetails, reason: str) -> Sample:
    """Correct a sample's descriptive details (old and new values are audited)."""
    require(actor, Permission.EDIT_RECORDS)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    sample = _get(session, sample_pk)
    if sample.status in CLOSED_STATUSES:
        raise ValidationError(f"{sample.sample_id} is {sample.status} and can no longer be changed.")
    details = _normalize(details)
    _validate_details(details, Category(session.get(SampleType, sample.sample_type_id).category))
    old, new = {}, {}
    for name, value in asdict(details).items():
        current = getattr(sample, name)
        if current != value:
            old[name], new[name] = current, value
            setattr(sample, name, value)
    if not new:
        raise ValidationError("Nothing was changed.")
    sample.updated_at = utcnow()
    _custody(session, sample, actor, CustodyEventType.DETAILS_CORRECTED,
             details={"fields": [field_label(n) for n in new]}, notes=reason)
    _audit(session, AuditAction.SAMPLE_UPDATED, actor, sample, old=old, new=new, reason=reason.strip())
    return sample


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def get_sample(session: Session, actor: SessionUser, sample_pk: int) -> Sample:
    require(actor, Permission.VIEW_RECORDS)
    return _get(session, sample_pk)


def details_of(sample: Sample) -> SampleDetails:
    return SampleDetails(**{name: getattr(sample, name) for name in EDITABLE_FIELDS})


def search(
    session: Session,
    actor: SessionUser,
    *,
    text: str | None = None,
    status: str | None = None,
    sample_type_id: int | None = None,
    location_id: int | None = None,
    include_closed: bool = False,
    limit: int = 2000,
) -> list[SampleSummary]:
    require(actor, Permission.VIEW_RECORDS)
    Parent = Sample.__table__.alias("parent")
    stmt = (
        select(Sample, SampleType, User.full_name, Parent.c.sample_id)
        .join(SampleType, Sample.sample_type_id == SampleType.id)
        .outerjoin(User, Sample.holder_id == User.id)
        .outerjoin(Parent, Sample.parent_id == Parent.c.id)
        .order_by(Sample.id.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Sample.status == status)
    elif not include_closed:
        stmt = stmt.where(Sample.status.not_in([s.value for s in CLOSED_STATUSES]))
    if sample_type_id:
        stmt = stmt.where(Sample.sample_type_id == sample_type_id)
    if location_id:
        stmt = stmt.where(Sample.location_id == location_id)
    if text:
        pattern = f"%{text.strip()}%"
        stmt = stmt.where(or_(
            Sample.sample_id.ilike(pattern), Sample.material_name.ilike(pattern), Sample.lot_number.ilike(pattern),
            Sample.supplier.ilike(pattern), Sample.sampling_point.ilike(pattern),
            Sample.stability_study.ilike(pattern),
        ))
    return [
        SampleSummary(
            id=s.id, sample_id=s.sample_id, type_name=t.name, type_code=t.code, category=t.category, status=s.status,
            material_name=s.material_name or s.sampling_point, lot_number=s.lot_number, location_id=s.location_id,
            holder_name=holder, priority=s.priority, due_date=s.due_date, created_at=s.created_at,
            received_at=s.received_at, parent_sample_id=parent_id,
        )
        for s, t, holder, parent_id in session.execute(stmt)
    ]


def custody_history(session: Session, actor: SessionUser, sample_pk: int) -> list[CustodyEvent]:
    require(actor, Permission.VIEW_RECORDS)
    return list(session.scalars(
        select(CustodyEvent).where(CustodyEvent.sample_id == sample_pk).order_by(CustodyEvent.id)
    ))


def status_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Sample.status, func.count()).group_by(Sample.status))
    return {status: count for status, count in rows}


def due_counts(session: Session, within_days: int = 7) -> tuple[int, int]:
    """(overdue, due within ``within_days``) for samples still open."""
    today = date.today()
    open_filter = Sample.status.not_in([s.value for s in CLOSED_STATUSES])
    overdue = session.scalar(select(func.count()).select_from(Sample).where(open_filter, Sample.due_date < today))
    soon = session.scalar(select(func.count()).select_from(Sample).where(
        open_filter, Sample.due_date >= today, Sample.due_date <= today + timedelta(days=within_days)))
    return overdue or 0, soon or 0
