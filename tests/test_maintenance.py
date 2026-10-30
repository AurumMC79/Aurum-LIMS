"""Preventive-maintenance plans and their checklists."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import AuditEntry, Instrument
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import instruments, maintenance
from aurum_lims.services.instruments import EventType, InstrumentStatus
from aurum_lims.services.maintenance import Outcome, PlanSpec, TaskResult, TaskSpec
from tests.test_equipment import _instrument

TODAY = date.today()
MONTHLY = PlanSpec("Monthly PM", 30, [
    TaskSpec("Replace pump seals"),
    TaskSpec("Lamp energy", needs_value=True, acceptance="≥ 1500 counts"),
    TaskSpec("Clean autosampler needle"),
])
ALL_OK = [TaskResult("OK"), TaskResult("OK", "1734"), TaskResult("OK")]


def _plan(db, admin, instrument_id, spec=MONTHLY):
    with db.session() as s:
        return maintenance.create_plan(s, admin, instrument_id, spec).id


def test_plan_validation_and_permissions(db, admin, make_user):
    hplc = _instrument(db, admin)
    analyst = make_user("ann", Role.ANALYST)
    with db.session() as s:
        for spec, message in [(PlanSpec("", 30, MONTHLY.tasks), "name"), (PlanSpec("PM", 0, MONTHLY.tasks), "interval"),
                              (PlanSpec("PM", 30, [TaskSpec("  ")]), "at least one task")]:
            with pytest.raises(ValidationError, match=message):
                maintenance.create_plan(s, admin, hplc, spec)
        with pytest.raises(PermissionDenied):
            maintenance.create_plan(s, analyst, hplc, MONTHLY)
    plan_id = _plan(db, admin, hplc)
    with db.session() as s:
        with pytest.raises(ValidationError, match="already has a plan"):
            maintenance.create_plan(s, admin, hplc, MONTHLY)
        plan = s.get(maintenance.MaintenancePlan, plan_id)
        assert plan.due_date == TODAY + timedelta(days=30)
        assert [t.text for t in maintenance.tasks_of(plan)] == ["Replace pump seals", "Lamp energy",
                                                               "Clean autosampler needle"]
        instrument = s.get(Instrument, hplc)
        assert (instrument.plan_due, instrument.plan_due_name) == (TODAY + timedelta(days=30), "Monthly PM")


def test_perform_a_plan(db, admin, make_user):
    hplc = _instrument(db, admin)
    plan_id = _plan(db, admin, hplc)
    analyst = make_user("ann", Role.ANALYST)
    with db.session() as s:
        with pytest.raises(ValidationError, match="every task"):
            maintenance.perform(s, analyst, plan_id, ALL_OK[:2], performed_on=TODAY)
        with pytest.raises(ValidationError, match="enter the reading"):
            maintenance.perform(s, analyst, plan_id, [TaskResult("OK"), TaskResult("OK"), TaskResult("OK")],
                                performed_on=TODAY)
        with pytest.raises(ValidationError, match="does not apply"):
            maintenance.perform(s, analyst, plan_id, [TaskResult("N/A"), *ALL_OK[1:]], performed_on=TODAY)
        maintenance.perform(s, analyst, plan_id, ALL_OK, performed_on=TODAY - timedelta(days=2),
                            performed_by="Agilent field engineer")
    with db.session() as s:
        plan = s.get(maintenance.MaintenancePlan, plan_id)
        assert plan.last_done == TODAY - timedelta(days=2) and plan.due_date == TODAY + timedelta(days=28)
        assert s.get(Instrument, hplc).plan_due == plan.due_date
        [event] = instruments.history(s, hplc)
        assert event.event_type == EventType.MAINTENANCE and event.result == "Pass"
        assert event.plan_name == "Monthly PM" and event.next_due == plan.due_date
        assert event.checklist[1] == {"task": "Lamp energy", "acceptance": "≥ 1500 counts", "outcome": "OK",
                                      "value": "1734", "note": ""}
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == "INSTRUMENT_EVENT")).one()
        assert '"plan": "Monthly PM"' in entry.new_values
    with db.session() as s, pytest.raises(DBAPIError, match="immutable"):
        s.execute(text("UPDATE instrument_events SET checklist = '[]'"))


def test_problem_found_takes_instrument_out_of_service(db, admin):
    hplc = _instrument(db, admin)
    plan_id = _plan(db, admin, hplc)
    results = [TaskResult("OK"), TaskResult(Outcome.PROBLEM, "1210", ""), TaskResult("OK")]
    with db.session() as s:
        with pytest.raises(ValidationError, match="describe the problem"):
            maintenance.perform(s, admin, plan_id, results, performed_on=TODAY)
        results[1].note = "Lamp energy below 1500; lamp ordered"
        maintenance.perform(s, admin, plan_id, results, performed_on=TODAY)
    with db.session() as s:
        instrument = s.get(Instrument, hplc)
        assert instrument.status == InstrumentStatus.OUT_OF_SERVICE
        assert s.get(maintenance.MaintenancePlan, plan_id).due_date == TODAY + timedelta(days=30)  # unchanged
        [event] = instruments.history(s, hplc)
        assert event.result == "Fail" and "Problems found: Lamp energy" in event.notes


def test_overdue_plan_blocks_use_and_shows_as_due(db, admin):
    hplc = _instrument(db, admin)
    plan_id = _plan(db, admin, hplc, PlanSpec("Weekly check", 7, [TaskSpec("Leak check")],
                                              first_due=TODAY - timedelta(days=1)))
    with db.session() as s:
        instrument = s.get(Instrument, hplc)
        assert instruments.problems(instrument) == [
            f"HPLC-01 maintenance 'Weekly check' was due {(TODAY - timedelta(days=1)).isoformat()}"]
        overdue, _ = instruments.due_soon(s)
        assert [i.code for i in overdue] == ["HPLC-01"]
        [row] = maintenance.schedule(s)
        assert row.state() == "Overdue" and row.task_count == 1
        maintenance.set_active(s, admin, plan_id, False, "Covered by the monthly PM")
    with db.session() as s:
        assert instruments.problems(s.get(Instrument, hplc)) == []
        assert maintenance.schedule(s) == []


def test_update_and_copy(db, admin):
    hplc1 = _instrument(db, admin)
    hplc2 = _instrument(db, admin, code="HPLC-02")
    plan_id = _plan(db, admin, hplc1)
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            maintenance.update_plan(s, admin, plan_id, MONTHLY, "")
        with pytest.raises(ValidationError, match="Nothing"):
            maintenance.update_plan(s, admin, plan_id, MONTHLY, "x")
        maintenance.update_plan(s, admin, plan_id, PlanSpec("Monthly PM", 31, MONTHLY.tasks), "Vendor schedule",
                                due_date=TODAY + timedelta(days=5))
    with db.session() as s:
        plan = s.get(maintenance.MaintenancePlan, plan_id)
        assert plan.interval_days == 31 and s.get(Instrument, hplc1).plan_due == TODAY + timedelta(days=5)
        assert maintenance.copy_plan(s, admin, plan_id, [hplc1, hplc2, hplc2]) == ["HPLC-02"]
    with db.session() as s:
        assert maintenance.copy_plan(s, admin, plan_id, [hplc2]) == []  # already has it
        [copy] = maintenance.plans_for(s, hplc2)
        assert copy.interval_days == 31 and len(maintenance.tasks_of(copy)) == 3
