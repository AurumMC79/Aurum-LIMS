"""Instruments and inventory lots used on a test.

Each method version lists what it requires (e.g. one HPLC and one reference
standard). The analyst records which instrument and which lots were used;
a test cannot be signed until every requirement is met, and instruments that
are out of service or overdue, and lots that are expired or discarded, are
refused both when recorded and again at signing.

A reference-standard requirement can fill a method input (e.g. standard
purity) from the lot's certified purity, so it is never typed by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import (
    Analysis,
    AnalysisUsage,
    Instrument,
    InventoryItem,
    InventoryLot,
    MethodRequirement,
    ResultValue,
    Sample,
    User,
    utcnow,
)
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, formulas, instruments, inventory, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.inventory import LotStatus
from aurum_lims.services.methods import RequirementKind, requirements_of


def lot_filled_keys(session: Session, method_id: int) -> dict[str, MethodRequirement]:
    """Method inputs that are filled from a reference-standard lot."""
    return {r.fills_parameter: r for r in requirements_of(session, method_id) if r.fills_parameter}


def _open_analysis(session: Session, analysis_id: int) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFound("Test not found.")
    if analysis.status not in testing.OPEN_ANALYSIS:
        raise ValidationError(f"This test is {analysis.status}; what it used can no longer be changed.")
    sample = session.get(Sample, analysis.sample_id)
    if sample.status not in testing.TESTABLE:
        raise ValidationError(f"The sample is {sample.status}.")
    return analysis


def _requirement(session: Session, analysis: Analysis, requirement_id: int | None,
                 kind: RequirementKind) -> MethodRequirement | None:
    if requirement_id is None:
        return None
    requirement = session.get(MethodRequirement, requirement_id)
    if requirement is None or requirement.method_id != analysis.method_id or requirement.kind != kind:
        raise ValidationError("That requirement does not belong to this test.")
    return requirement


def _mark_started(session: Session, analysis: Analysis) -> None:
    if analysis.status == testing.AnalysisStatus.PENDING:
        analysis.status = testing.AnalysisStatus.IN_PROGRESS
        analysis.started_at = utcnow()
        testing._refresh_sample_status(session, session.get(Sample, analysis.sample_id))


def record_instrument(session: Session, actor: SessionUser, analysis_id: int, instrument_id: int,
                      requirement_id: int | None = None) -> AnalysisUsage:
    require(actor, Permission.EDIT_RECORDS)
    analysis = _open_analysis(session, analysis_id)
    requirement = _requirement(session, analysis, requirement_id, RequirementKind.INSTRUMENT)
    instrument = instruments.get(session, instrument_id)
    if requirement is not None and instrument.category != requirement.category:
        raise ValidationError(f"{requirement.label} must be a {requirement.category}; "
                              f"{instrument.code} is a {instrument.category}.")
    problems = instruments.problems(instrument)
    if problems:
        raise ValidationError("This instrument cannot be used: " + "; ".join(problems) + ".")
    if session.scalar(select(AnalysisUsage.id).where(AnalysisUsage.analysis_id == analysis.id,
                                                     AnalysisUsage.instrument_id == instrument.id)) is not None:
        raise ValidationError(f"{instrument.code} is already recorded on this test.")
    usage = AnalysisUsage(analysis_id=analysis.id, requirement_id=requirement_id, instrument_id=instrument.id,
                          recorded_by_id=actor.id)
    session.add(usage)
    session.flush()
    audit.record(session, AuditAction.USAGE_RECORDED, actor=actor, entity_type="test",
                 entity_id=testing._label(session, analysis),
                 new={"instrument": instrument.code, "calibration_due": instrument.calibration_due})
    _mark_started(session, analysis)
    return usage


def record_lot(session: Session, actor: SessionUser, analysis_id: int, lot_id: int, *,
               quantity_used: str | None = None, requirement_id: int | None = None) -> AnalysisUsage:
    require(actor, Permission.EDIT_RECORDS)
    analysis = _open_analysis(session, analysis_id)
    requirement = _requirement(session, analysis, requirement_id, RequirementKind.MATERIAL)
    lot = inventory.get_lot(session, lot_id)
    item = session.get(InventoryItem, lot.item_id)
    if requirement is not None and item.kind != requirement.category:
        raise ValidationError(f"{requirement.label} must be a {requirement.category.lower()}; "
                              f"{item.name} is a {item.kind.lower()}.")
    problems = inventory.lot_problems(session, lot)
    if lot.status == LotStatus.DEPLETED:
        problems.append(f"{lot.internal_id} has no stock left")
    if problems:
        raise ValidationError("This lot cannot be used: " + "; ".join(problems) + ".")
    if session.scalar(select(AnalysisUsage.id).where(AnalysisUsage.analysis_id == analysis.id,
                                                     AnalysisUsage.lot_id == lot.id)) is not None:
        raise ValidationError(f"{lot.internal_id} is already recorded on this test.")
    amount = formulas.parse_decimal(quantity_used)
    if amount is not None:
        if amount <= 0:
            raise ValidationError("The quantity used must be greater than zero.")
        inventory.consume(session, actor, lot, amount, analysis.id)
    usage = AnalysisUsage(analysis_id=analysis.id, requirement_id=requirement_id, lot_id=lot.id,
                          quantity_used=formulas.format_decimal(amount) if amount is not None else None,
                          recorded_by_id=actor.id)
    session.add(usage)
    session.flush()
    detail = {"lot": lot.internal_id, "item": item.name, "supplier_lot": lot.lot_number,
              "quantity_used": f"{usage.quantity_used} {item.unit}" if usage.quantity_used else None}
    if requirement is not None and requirement.fills_parameter:
        _fill_parameter(session, actor, analysis, requirement.fills_parameter, lot)
        detail["fills"] = f"{requirement.fills_parameter} = {lot.purity}"
    audit.record(session, AuditAction.USAGE_RECORDED, actor=actor, entity_type="test",
                 entity_id=testing._label(session, analysis), new=detail)
    _mark_started(session, analysis)
    return usage


def _fill_parameter(session: Session, actor: SessionUser, analysis: Analysis, key: str, lot: InventoryLot) -> None:
    if not lot.purity:
        raise ValidationError(f"{lot.internal_id} has no certified purity recorded.")
    row = session.scalar(select(ResultValue).where(ResultValue.analysis_id == analysis.id,
                                                   ResultValue.parameter_key == key, ResultValue.replicate == 1))
    now = utcnow()
    if row is None:
        session.add(ResultValue(analysis_id=analysis.id, parameter_key=key, replicate=1, value=lot.purity,
                                entered_by_id=actor.id, updated_by_id=actor.id))
    else:
        row.value, row.updated_at, row.updated_by_id = lot.purity, now, actor.id
    session.flush()


def remove_usage(session: Session, actor: SessionUser, usage_id: int, reason: str) -> None:
    """Remove a recorded instrument/lot (e.g. recorded by mistake). Stock taken is put back."""
    require(actor, Permission.EDIT_RECORDS)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    usage = session.get(AnalysisUsage, usage_id)
    if usage is None:
        raise NotFound("Usage record not found.")
    analysis = _open_analysis(session, usage.analysis_id)
    detail = {}
    if usage.instrument_id:
        detail["instrument"] = session.get(Instrument, usage.instrument_id).code
    if usage.lot_id:
        lot = session.get(InventoryLot, usage.lot_id)
        detail["lot"] = lot.internal_id
        if usage.quantity_used:
            inventory.consume(session, actor, lot, Decimal(usage.quantity_used), analysis.id, returned=True)
            detail["quantity_returned"] = usage.quantity_used
        requirement = session.get(MethodRequirement, usage.requirement_id) if usage.requirement_id else None
        if requirement is not None and requirement.fills_parameter:
            row = session.scalar(select(ResultValue).where(ResultValue.analysis_id == analysis.id,
                                                           ResultValue.parameter_key == requirement.fills_parameter))
            if row is not None:
                session.delete(row)
                detail["cleared"] = requirement.fills_parameter
    session.delete(usage)
    session.flush()
    audit.record(session, AuditAction.USAGE_REMOVED, actor=actor, entity_type="test",
                 entity_id=testing._label(session, analysis), old=detail, reason=reason.strip())


@dataclass
class UsageRow:
    id: int
    requirement_id: int | None
    kind: str
    code: str
    description: str
    quantity: str | None
    recorded_by: str
    problems: list[str]


def usages_for(session: Session, analysis_id: int) -> list[UsageRow]:
    rows = []
    for usage, who in session.execute(select(AnalysisUsage, User.full_name)
                                      .join(User, AnalysisUsage.recorded_by_id == User.id)
                                      .where(AnalysisUsage.analysis_id == analysis_id).order_by(AnalysisUsage.id)):
        if usage.instrument_id:
            instrument = session.get(Instrument, usage.instrument_id)
            due = f", calibration due {instrument.calibration_due.isoformat()}" if instrument.calibration_due else ""
            rows.append(UsageRow(usage.id, usage.requirement_id, RequirementKind.INSTRUMENT, instrument.code,
                                 f"{instrument.name} ({instrument.category}{due})", None, who,
                                 instruments.problems(instrument)))
        else:
            lot = session.get(InventoryLot, usage.lot_id)
            item = session.get(InventoryItem, lot.item_id)
            expiry = inventory.effective_expiry(lot, item)
            text = f"{item.name}, lot {lot.lot_number}" + (f", purity {lot.purity} %" if lot.purity else "") + \
                (f", expires {expiry.isoformat()}" if expiry else "")
            rows.append(UsageRow(usage.id, usage.requirement_id, RequirementKind.MATERIAL, lot.internal_id, text,
                                 f"{usage.quantity_used} {item.unit}" if usage.quantity_used else None, who,
                                 inventory.lot_problems(session, lot)))
    return rows


def readiness_problems(session: Session, analysis: Analysis) -> list[str]:
    """What stops the test being signed: unmet requirements, or recorded items no longer usable."""
    out = []
    used = usages_for(session, analysis.id)
    covered = {u.requirement_id for u in used if u.requirement_id}
    for requirement in requirements_of(session, analysis.method_id):
        if requirement.id not in covered:
            out.append(f"Record the {requirement.label} used")
    for u in used:
        out.extend(u.problems)
    return out
