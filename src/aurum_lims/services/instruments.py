"""Instruments, their calibration / maintenance schedules and history.

An instrument can be used for a test only while it is *In service* and its
calibration and maintenance are not overdue. A failed calibration,
maintenance or qualification takes it out of service automatically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import Instrument, InstrumentEvent, MaintenancePlan, User
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require, require_any
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_./]{0,39}$")

CATEGORIES = [
    "HPLC", "UPLC", "GC", "GC-MS", "LC-MS", "UV-Vis spectrophotometer", "FTIR", "AAS / ICP", "Balance",
    "pH meter", "Conductivity meter", "Karl Fischer titrator", "Titrator", "Dissolution apparatus",
    "Disintegration tester", "Hardness tester", "Friability tester", "Melting point apparatus", "Polarimeter",
    "Refractometer", "TOC analyser", "Particle size analyser", "Viscometer", "Oven", "Incubator",
    "Stability chamber", "Refrigerator / freezer", "Water bath", "Centrifuge", "Pipette", "Thermometer", "Other",
]


class InstrumentStatus(StrEnum):
    IN_SERVICE = "In service"
    OUT_OF_SERVICE = "Out of service"
    RETIRED = "Retired"


class EventType(StrEnum):
    CALIBRATION = "Calibration"
    MAINTENANCE = "Maintenance"
    QUALIFICATION = "Qualification (IQ/OQ/PQ)"
    PERFORMANCE_CHECK = "Performance check"
    REPAIR = "Repair"
    OUT_OF_SERVICE = "Taken out of service"
    RETURNED = "Returned to service"
    RETIRED = "Retired"


RESULTS = ["Pass", "Fail", "N/A"]
# Events whose failure takes the instrument out of service.
CHECK_EVENTS = {EventType.CALIBRATION, EventType.MAINTENANCE, EventType.QUALIFICATION, EventType.PERFORMANCE_CHECK}


@dataclass
class InstrumentDetails:
    name: str
    category: str
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    location: str | None = None
    calibration_interval_days: int | None = None
    maintenance_interval_days: int | None = None
    notes: str | None = None


def _clean(value: str | None) -> str | None:
    return " ".join(value.split()) if value and value.strip() else None


def _validate(details: InstrumentDetails) -> InstrumentDetails:
    name = _clean(details.name)
    if not name:
        raise ValidationError("Instrument name is required.")
    if details.category not in CATEGORIES:
        raise ValidationError("Choose an instrument category.")
    for label, value in (("Calibration interval", details.calibration_interval_days),
                         ("Maintenance interval", details.maintenance_interval_days)):
        if value is not None and not 1 <= int(value) <= 3650:
            raise ValidationError(f"{label} must be between 1 and 3650 days (or empty for none).")
    return InstrumentDetails(
        name=name, category=details.category, manufacturer=_clean(details.manufacturer), model=_clean(details.model),
        serial_number=_clean(details.serial_number), location=_clean(details.location),
        calibration_interval_days=details.calibration_interval_days or None,
        maintenance_interval_days=details.maintenance_interval_days or None,
        notes=(details.notes or "").strip() or None,
    )


def describe(instrument: Instrument) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in {
        "code": instrument.code, "name": instrument.name, "category": instrument.category,
        "manufacturer": instrument.manufacturer, "model": instrument.model,
        "serial_number": instrument.serial_number, "location": instrument.location, "status": instrument.status,
        "calibration_interval_days": instrument.calibration_interval_days,
        "maintenance_interval_days": instrument.maintenance_interval_days,
        "calibration_due": instrument.calibration_due, "maintenance_due": instrument.maintenance_due,
        "notes": instrument.notes,
    }.items()}


def problems(instrument: Instrument, today: date | None = None) -> list[str]:
    """Why the instrument cannot be used today (empty list = usable)."""
    today = today or date.today()
    out = []
    if instrument.status != InstrumentStatus.IN_SERVICE:
        out.append(f"{instrument.code} is {instrument.status.lower()}")
    if instrument.calibration_interval_days and instrument.calibration_due is None:
        out.append(f"{instrument.code} has no calibration recorded")
    elif instrument.calibration_due and instrument.calibration_due < today:
        out.append(f"{instrument.code} calibration was due {instrument.calibration_due.isoformat()}")
    if instrument.maintenance_interval_days and instrument.maintenance_due is None:
        out.append(f"{instrument.code} has no maintenance recorded")
    elif instrument.maintenance_due and instrument.maintenance_due < today:
        out.append(f"{instrument.code} maintenance was due {instrument.maintenance_due.isoformat()}")
    if instrument.plan_due and instrument.plan_due < today:
        out.append(f"{instrument.code} maintenance '{instrument.plan_due_name}' was due {instrument.plan_due.isoformat()}")
    return out


def sync_plan_due(session: Session, instrument: Instrument) -> None:
    """Keep the instrument's earliest maintenance-plan due date in step with its plans."""
    plan = session.scalar(select(MaintenancePlan).where(
        MaintenancePlan.instrument_id == instrument.id, MaintenancePlan.is_active.is_(True),
        MaintenancePlan.due_date.is_not(None)).order_by(MaintenancePlan.due_date, MaintenancePlan.name).limit(1))
    instrument.plan_due = plan.due_date if plan else None
    instrument.plan_due_name = plan.name if plan else None


def get(session: Session, instrument_id: int) -> Instrument:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise NotFound("Instrument not found.")
    return instrument


def list_instruments(session: Session, include_retired: bool = False, category: str | None = None) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.code)
    if not include_retired:
        stmt = stmt.where(Instrument.status != InstrumentStatus.RETIRED)
    if category:
        stmt = stmt.where(Instrument.category == category)
    return list(session.scalars(stmt))


def due_soon(session: Session, within_days: int = 14) -> tuple[list[Instrument], list[Instrument]]:
    """(out of service, overdue or never done; due within ``within_days``). Retired ones are ignored."""
    today = date.today()
    overdue, soon = [], []
    for instrument in list_instruments(session):
        if problems(instrument, today):
            overdue.append(instrument)
            continue
        dues = [d for d in (instrument.calibration_due, instrument.maintenance_due, instrument.plan_due) if d]
        if dues and min(dues) <= today + timedelta(days=within_days):
            soon.append(instrument)
    return overdue, soon


def create_instrument(session: Session, actor: SessionUser, code: str, details: InstrumentDetails, *,
                      calibration_due: date | None = None, maintenance_due: date | None = None) -> Instrument:
    """Register an instrument. Current due dates (e.g. from its calibration label) can be entered."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    code = (code or "").strip().upper()
    if not CODE_PATTERN.match(code):
        raise ValidationError("Instrument ID must be 1-40 capital letters, numbers, - _ . or / (e.g. HPLC-01).")
    if session.scalar(select(Instrument.id).where(Instrument.code == code)) is not None:
        raise ValidationError(f"An instrument with ID {code} already exists.")
    details = _validate(details)
    instrument = Instrument(code=code, status=InstrumentStatus.IN_SERVICE, calibration_due=calibration_due,
                            maintenance_due=maintenance_due, created_by_id=actor.id, **vars(details))
    session.add(instrument)
    session.flush()
    audit.record(session, AuditAction.INSTRUMENT_CREATED, actor=actor, entity_type="instrument", entity_id=code,
                 new=describe(instrument))
    return instrument


def update_instrument(session: Session, actor: SessionUser, instrument_id: int, details: InstrumentDetails,
                      reason: str) -> Instrument:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    instrument = get(session, instrument_id)
    old = describe(instrument)
    for key, value in vars(_validate(details)).items():
        setattr(instrument, key, value)
    new = describe(instrument)
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    audit.record(session, AuditAction.INSTRUMENT_UPDATED, actor=actor, entity_type="instrument",
                 entity_id=instrument.code, old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    return instrument


def record_event(
    session: Session,
    actor: SessionUser,
    instrument_id: int,
    *,
    event_type: EventType | str,
    performed_on: date,
    result: str | None = None,
    performed_by: str | None = None,
    certificate_ref: str | None = None,
    next_due: date | None = None,
    notes: str | None = None,
    plan=None,
    checklist: str | None = None,
) -> InstrumentEvent:
    """Record calibration, maintenance etc. and update the instrument's status and due dates.
    ``plan`` (a MaintenancePlan) is given when maintenance was done from a plan's checklist:
    the plan's own due date moves on instead of the instrument's general maintenance date."""
    require_any(actor, Permission.EDIT_RECORDS, Permission.MANAGE_MASTER_DATA)
    instrument = get(session, instrument_id)
    event_type = EventType(event_type)
    notes = (notes or "").strip() or None
    if instrument.status == InstrumentStatus.RETIRED:
        raise ValidationError(f"{instrument.code} is retired.")
    if performed_on > date.today():
        raise ValidationError("The date cannot be in the future.")
    if event_type in CHECK_EVENTS:
        if result not in RESULTS:
            raise ValidationError("Choose the result: Pass, Fail or N/A.")
    else:
        result = None
    if event_type in (EventType.OUT_OF_SERVICE, EventType.RETURNED, EventType.RETIRED, EventType.REPAIR) and not notes:
        raise ValidationError("Add a note explaining this event.")
    if event_type is EventType.RETURNED and instrument.status != InstrumentStatus.OUT_OF_SERVICE:
        raise ValidationError(f"{instrument.code} is not out of service.")

    old = {"status": instrument.status, "calibration_due": instrument.calibration_due,
           "maintenance_due": instrument.maintenance_due, "plan_due": instrument.plan_due}
    passed = result in ("Pass", "N/A")
    if plan is not None:
        if event_type is not EventType.MAINTENANCE:
            raise ValidationError("A maintenance plan can only be recorded as maintenance.")
        if passed:
            next_due = performed_on + timedelta(days=plan.interval_days)
            plan.due_date, plan.last_done = next_due, performed_on
        else:
            next_due = None
    elif event_type is EventType.CALIBRATION and passed:
        interval = instrument.calibration_interval_days
        next_due = next_due or (performed_on + timedelta(days=interval) if interval else None)
        if interval and next_due is None:
            raise ValidationError("Enter the next calibration due date.")
        instrument.calibration_due = next_due
    elif event_type is EventType.MAINTENANCE and passed:
        interval = instrument.maintenance_interval_days
        next_due = next_due or (performed_on + timedelta(days=interval) if interval else None)
        instrument.maintenance_due = next_due
    elif event_type not in (EventType.CALIBRATION, EventType.MAINTENANCE):
        next_due = None
    if event_type in CHECK_EVENTS and result == "Fail":
        instrument.status = InstrumentStatus.OUT_OF_SERVICE
    elif event_type is EventType.OUT_OF_SERVICE:
        instrument.status = InstrumentStatus.OUT_OF_SERVICE
    elif event_type is EventType.RETURNED:
        instrument.status = InstrumentStatus.IN_SERVICE
    elif event_type is EventType.RETIRED:
        instrument.status = InstrumentStatus.RETIRED

    event = InstrumentEvent(
        instrument_id=instrument.id, event_type=event_type.value, performed_on=performed_on,
        performed_by=_clean(performed_by), result=result, certificate_ref=_clean(certificate_ref),
        next_due=next_due if result != "Fail" else None, notes=notes, recorded_by_id=actor.id,
        plan_id=plan.id if plan is not None else None, checklist=checklist,
    )
    session.add(event)
    session.flush()
    sync_plan_due(session, instrument)
    new = {"status": instrument.status, "calibration_due": instrument.calibration_due,
           "maintenance_due": instrument.maintenance_due, "plan_due": instrument.plan_due}
    audit.record(session, AuditAction.INSTRUMENT_EVENT, actor=actor, entity_type="instrument",
                 entity_id=instrument.code,
                 old={k: v for k, v in old.items() if new[k] != v} or None,
                 new={"event": event_type.value, "performed_on": performed_on.isoformat(), "result": result,
                      **({"plan": plan.name} if plan is not None else {}),
                      **{k: v for k, v in new.items() if old[k] != v}},
                 reason=notes)
    return event


@dataclass
class EventRow:
    event_type: str
    performed_on: date
    result: str | None
    performed_by: str | None
    certificate_ref: str | None
    next_due: date | None
    notes: str | None
    recorded_by: str
    plan_name: str | None = None
    checklist: list[dict] | None = None


def history(session: Session, instrument_id: int) -> list[EventRow]:
    rows = session.execute(
        select(InstrumentEvent, User.full_name, MaintenancePlan.name)
        .join(User, InstrumentEvent.recorded_by_id == User.id)
        .outerjoin(MaintenancePlan, InstrumentEvent.plan_id == MaintenancePlan.id)
        .where(InstrumentEvent.instrument_id == instrument_id)
        .order_by(InstrumentEvent.performed_on.desc(), InstrumentEvent.id.desc())
    )
    return [EventRow(e.event_type, e.performed_on, e.result, e.performed_by, e.certificate_ref, e.next_due, e.notes,
                     who, plan, json.loads(e.checklist) if e.checklist else None) for e, who, plan in rows]
