"""Preventive maintenance: recurring task lists (plans) for instruments.

A plan belongs to one instrument and has a name ("Monthly PM"), an interval in
days and a checklist of tasks. A task can ask for a reading and state the
acceptance criterion (e.g. "Lamp energy" – "≥ 1500 counts").

Performing a plan means going through the checklist: each task is marked
**OK**, **Problem found** or **N/A**, with the reading where one is asked for.
The completed checklist is stored with the instrument's (immutable) event
history. Any "Problem found" makes the maintenance a fail, which takes the
instrument out of service until it is returned to service with a note. A pass
moves the plan's next due date on by its interval. An overdue plan stops the
instrument being used in tests, just like an overdue calibration.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import Instrument, InstrumentEvent, MaintenancePlan, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require, require_any
from aurum_lims.services import audit, instruments
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.instruments import EventType, InstrumentStatus

MAX_TASKS = 50


class Outcome(StrEnum):
    OK = "OK"
    PROBLEM = "Problem found"
    NOT_APPLICABLE = "N/A"


@dataclass
class TaskSpec:
    text: str
    needs_value: bool = False
    acceptance: str = ""


@dataclass
class PlanSpec:
    name: str
    interval_days: int
    tasks: list[TaskSpec] = field(default_factory=list)
    first_due: date | None = None  # new plans only; default today + interval


@dataclass
class TaskResult:
    outcome: str
    value: str = ""
    note: str = ""


def tasks_of(plan: MaintenancePlan) -> list[TaskSpec]:
    return [TaskSpec(**t) for t in json.loads(plan.tasks)]


def _clean_spec(spec: PlanSpec) -> PlanSpec:
    name = " ".join((spec.name or "").split())
    if not name:
        raise ValidationError("Give the plan a name, e.g. Monthly preventive maintenance.")
    if not 1 <= int(spec.interval_days or 0) <= 3650:
        raise ValidationError("The interval must be between 1 and 3650 days.")
    tasks = []
    for index, task in enumerate(spec.tasks, start=1):
        text = " ".join((task.text or "").split())
        if not text:
            continue
        tasks.append(TaskSpec(text, bool(task.needs_value), " ".join((task.acceptance or "").split())))
    if not tasks:
        raise ValidationError("Add at least one task to the checklist.")
    if len(tasks) > MAX_TASKS:
        raise ValidationError(f"A plan can have at most {MAX_TASKS} tasks.")
    return PlanSpec(name, int(spec.interval_days), tasks, spec.first_due)


def _tasks_json(tasks: list[TaskSpec]) -> str:
    return json.dumps([asdict(t) for t in tasks], ensure_ascii=False)


def _get(session: Session, plan_id: int) -> MaintenancePlan:
    plan = session.get(MaintenancePlan, plan_id)
    if plan is None:
        raise NotFound("Maintenance plan not found.")
    return plan


def _label(session: Session, plan: MaintenancePlan) -> str:
    return f"{session.get(Instrument, plan.instrument_id).code} / {plan.name}"


def _check_name(session: Session, instrument_id: int, name: str, plan_id: int | None = None) -> None:
    clash = session.scalar(select(MaintenancePlan.id).where(MaintenancePlan.instrument_id == instrument_id,
                                                            MaintenancePlan.name == name))
    if clash is not None and clash != plan_id:
        raise ValidationError(f"This instrument already has a plan called '{name}'.")


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def create_plan(session: Session, actor: SessionUser, instrument_id: int, spec: PlanSpec) -> MaintenancePlan:
    require(actor, Permission.MANAGE_MASTER_DATA)
    instrument = instruments.get(session, instrument_id)
    if instrument.status == InstrumentStatus.RETIRED:
        raise ValidationError(f"{instrument.code} is retired.")
    spec = _clean_spec(spec)
    _check_name(session, instrument.id, spec.name)
    first_due = spec.first_due or date.today() + timedelta(days=spec.interval_days)
    plan = MaintenancePlan(instrument_id=instrument.id, name=spec.name, interval_days=spec.interval_days,
                           tasks=_tasks_json(spec.tasks), due_date=first_due, is_active=True, created_by_id=actor.id)
    session.add(plan)
    session.flush()
    instruments.sync_plan_due(session, instrument)
    audit.record(session, AuditAction.MAINTENANCE_PLAN_CREATED, actor=actor, entity_type="maintenance_plan",
                 entity_id=f"{instrument.code} / {plan.name}",
                 new={"interval_days": plan.interval_days, "first_due": first_due,
                      "tasks": [asdict(t) for t in spec.tasks]})
    return plan


def update_plan(session: Session, actor: SessionUser, plan_id: int, spec: PlanSpec, reason: str,
                due_date: date | None = None) -> MaintenancePlan:
    """Change a plan's name, interval or checklist (and optionally its next due date)."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    plan = _get(session, plan_id)
    spec = _clean_spec(spec)
    _check_name(session, plan.instrument_id, spec.name, plan.id)
    old = {"name": plan.name, "interval_days": plan.interval_days, "tasks": json.loads(plan.tasks),
           "due_date": plan.due_date}
    new = {"name": spec.name, "interval_days": spec.interval_days, "tasks": [asdict(t) for t in spec.tasks],
           "due_date": due_date or plan.due_date}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    plan.name, plan.interval_days, plan.tasks = spec.name, spec.interval_days, _tasks_json(spec.tasks)
    plan.due_date = new["due_date"]
    plan.updated_at, plan.updated_by_id = utcnow(), actor.id
    instruments.sync_plan_due(session, session.get(Instrument, plan.instrument_id))
    audit.record(session, AuditAction.MAINTENANCE_PLAN_UPDATED, actor=actor, entity_type="maintenance_plan",
                 entity_id=_label(session, plan), old=changed, new={k: new[k] for k in changed},
                 reason=reason.strip())
    return plan


def set_active(session: Session, actor: SessionUser, plan_id: int, active: bool, reason: str) -> MaintenancePlan:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    plan = _get(session, plan_id)
    if plan.is_active == active:
        raise ValidationError("The plan is already " + ("active." if active else "inactive."))
    plan.is_active, plan.updated_at, plan.updated_by_id = active, utcnow(), actor.id
    instruments.sync_plan_due(session, session.get(Instrument, plan.instrument_id))
    audit.record(session, AuditAction.MAINTENANCE_PLAN_UPDATED, actor=actor, entity_type="maintenance_plan",
                 entity_id=_label(session, plan), old={"active": not active}, new={"active": active},
                 reason=reason.strip())
    return plan


def copy_plan(session: Session, actor: SessionUser, plan_id: int, instrument_ids: list[int]) -> list[str]:
    """Give other instruments the same plan (first due one interval from today).
    Returns the codes of the instruments that received it."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    source = _get(session, plan_id)
    spec = PlanSpec(source.name, source.interval_days, tasks_of(source))
    copied = []
    for instrument_id in dict.fromkeys(instrument_ids):
        if instrument_id == source.instrument_id:
            continue
        instrument = instruments.get(session, instrument_id)
        if instrument.status == InstrumentStatus.RETIRED or session.scalar(select(MaintenancePlan.id).where(
                MaintenancePlan.instrument_id == instrument_id, MaintenancePlan.name == source.name)):
            continue
        create_plan(session, actor, instrument_id, spec)
        copied.append(instrument.code)
    return copied


def plans_for(session: Session, instrument_id: int, include_inactive: bool = False) -> list[MaintenancePlan]:
    stmt = select(MaintenancePlan).where(MaintenancePlan.instrument_id == instrument_id).order_by(
        MaintenancePlan.due_date, MaintenancePlan.name)
    if not include_inactive:
        stmt = stmt.where(MaintenancePlan.is_active.is_(True))
    return list(session.scalars(stmt))


@dataclass
class ScheduleRow:
    plan_id: int
    instrument_id: int
    instrument_code: str
    instrument_name: str
    plan_name: str
    interval_days: int
    due_date: date | None
    last_done: date | None
    task_count: int

    def state(self, today: date | None = None) -> str:
        today = today or date.today()
        if self.due_date is None:
            return "Not scheduled"
        if self.due_date < today:
            return "Overdue"
        if self.due_date <= today + timedelta(days=14):
            return "Due soon"
        return "Scheduled"


def schedule(session: Session) -> list[ScheduleRow]:
    """Every active plan on instruments that are not retired, soonest due first."""
    stmt = (select(MaintenancePlan, Instrument).join(Instrument, Instrument.id == MaintenancePlan.instrument_id)
            .where(MaintenancePlan.is_active.is_(True), Instrument.status != InstrumentStatus.RETIRED)
            .order_by(MaintenancePlan.due_date, Instrument.code, MaintenancePlan.name))
    return [ScheduleRow(p.id, i.id, i.code, i.name, p.name, p.interval_days, p.due_date, p.last_done,
                        len(json.loads(p.tasks))) for p, i in session.execute(stmt)]


# ---------------------------------------------------------------------------
# performing a plan
# ---------------------------------------------------------------------------


def perform(session: Session, actor: SessionUser, plan_id: int, results: list[TaskResult], *,
            performed_on: date, performed_by: str | None = None, notes: str | None = None) -> InstrumentEvent:
    """Record that the plan's checklist was carried out."""
    require_any(actor, Permission.EDIT_RECORDS, Permission.MANAGE_MASTER_DATA)
    plan = _get(session, plan_id)
    if not plan.is_active:
        raise ValidationError("This plan is inactive.")
    tasks = tasks_of(plan)
    if len(results) != len(tasks):
        raise ValidationError("Complete every task on the checklist.")
    checklist, problems = [], []
    for index, (task, result) in enumerate(zip(tasks, results), start=1):
        if result.outcome not in tuple(Outcome):
            raise ValidationError(f"Task {index} ({task.text}): choose OK, Problem found or N/A.")
        outcome = Outcome(result.outcome)
        value, note = (result.value or "").strip(), (result.note or "").strip()
        if task.needs_value and outcome is not Outcome.NOT_APPLICABLE and not value:
            raise ValidationError(f"Task {index} ({task.text}): enter the reading.")
        if outcome is Outcome.PROBLEM:
            if not note:
                raise ValidationError(f"Task {index} ({task.text}): describe the problem found.")
            problems.append(task.text)
        if outcome is Outcome.NOT_APPLICABLE and not note:
            raise ValidationError(f"Task {index} ({task.text}): say why it does not apply.")
        checklist.append({"task": task.text, "acceptance": task.acceptance, "outcome": outcome.value,
                          "value": value, "note": note})
    result = "Fail" if problems else "Pass"
    notes = (notes or "").strip()
    if problems:
        summary = "Problems found: " + "; ".join(problems)
        notes = f"{summary}. {notes}" if notes else summary
    return instruments.record_event(
        session, actor, plan.instrument_id, event_type=EventType.MAINTENANCE, performed_on=performed_on,
        result=result, performed_by=performed_by, notes=f"{plan.name}: {notes}" if notes else plan.name,
        plan=plan, checklist=json.dumps(checklist, ensure_ascii=False))
