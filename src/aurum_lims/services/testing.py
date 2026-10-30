"""Tests on samples: assigning methods, entering results, calculating, signing.

Flow of one analysis (a method run on a sample)::

    Pending ──first value saved──> In Progress ──analyst signs──> Complete
       └──────────────cancel (with reason)──────────────> Cancelled

Reportable results are calculated from the entered replicate values:
calculated parameters are evaluated per replicate, the replicate results are
averaged, and the mean is rounded USP-style before comparison with the
specification limit. Results are frozen when the analyst signs. Any result
outside its limit opens an OOS investigation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from aurum_lims.db.models import (
    Analysis,
    IdCounter,
    OOSInvestigation,
    ReportableResult,
    ResultValue,
    Sample,
    SpecLimit,
    Specification,
    TestMethod,
    User,
    utcnow,
)
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, esign, formulas
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult
from aurum_lims.services.formulas import Conformance, FormulaError
from aurum_lims.services.methods import ParameterKind, ParameterSpec, ResultType, parameters_of, to_spec
from aurum_lims.services.samples import SampleStatus
from aurum_lims.services.versioning import VersionStatus

PASS_FAIL_VALUES = ("Pass", "Fail")
MAX_VALUE_LENGTH = 64


class AnalysisStatus(StrEnum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    RETURNED = "Returned"
    COMPLETE = "Complete"
    REVIEWED = "Reviewed"
    CANCELLED = "Cancelled"


# Tests whose results can still be entered or changed.
OPEN_ANALYSIS = (AnalysisStatus.PENDING, AnalysisStatus.IN_PROGRESS, AnalysisStatus.RETURNED)
# Sample statuses in which tests may be assigned / results entered.
TESTABLE = {SampleStatus.RECEIVED, SampleStatus.IN_TESTING, SampleStatus.TESTING_COMPLETE, SampleStatus.REVIEWED}
ASSIGNABLE = TESTABLE | {SampleStatus.REGISTERED}


# ---------------------------------------------------------------------------
# calculation (pure functions, also used for live preview while typing)
# ---------------------------------------------------------------------------


@dataclass
class LimitInfo:
    limit_type: str
    low: str | None
    high: str | None
    expected_text: str | None

    def text(self, unit: str | None) -> str:
        return formulas.limit_text(self.limit_type, self.low, self.high, self.expected_text, unit)


@dataclass
class ParameterResult:
    key: str
    name: str
    kind: str
    result_type: str
    unit: str | None
    decimals: int | None
    calculated: bool
    replicate_values: list[str] = field(default_factory=list)
    mean: Decimal | None = None
    rsd: Decimal | None = None
    reported: str | None = None
    spec_text: str | None = None
    conformance: Conformance | None = None
    missing: bool = False
    error: str | None = None
    lod: str | None = None
    loq: str | None = None
    below_bound: Decimal | None = None  # set when reported as ND / "< LOQ"

    @property
    def is_result(self) -> bool:
        return self.kind == ParameterKind.RESULT


@dataclass
class Calculation:
    parameters: list[ParameterResult]

    @property
    def results(self) -> list[ParameterResult]:
        return [p for p in self.parameters if p.is_result]

    @property
    def complete(self) -> bool:
        return all(not p.missing and not p.error for p in self.parameters)

    @property
    def failures(self) -> list[ParameterResult]:
        return [p for p in self.results if p.conformance is Conformance.FAIL]

    def problems(self) -> list[str]:
        out = []
        for p in self.parameters:
            if p.error:
                out.append(f"{p.name}: {p.error}")
            elif p.missing:
                out.append(f"{p.name}: values missing")
        return out


def _display(value: Decimal | None, decimals: int | None) -> str:
    if value is None:
        return ""
    # Intermediate values are shown with two guard digits beyond the reporting precision.
    return formulas.format_decimal(value, None if decimals is None else decimals + 2)


def calculate(
    parameters: list[ParameterSpec], values: dict[tuple[str, int], str], limits: dict[str, LimitInfo]
) -> Calculation:
    numbers: dict[str, list[Decimal | None]] = {}
    out: list[ParameterResult] = []
    for p in parameters:
        r = ParameterResult(p.key, p.name, p.kind, p.result_type, p.unit, p.decimals, p.calculated,
                            lod=p.lod, loq=p.loq)
        if p.result_type != ResultType.NUMERIC:
            text = (values.get((p.key, 1)) or "").strip()
            r.replicate_values = [text]
            r.missing = not text
            r.reported = text or None
        elif not p.calculated:
            series: list[Decimal | None] = []
            for i in range(1, p.replicates + 1):
                raw = values.get((p.key, i))
                try:
                    series.append(formulas.parse_decimal(raw))
                except ValidationError:
                    series.append(None)
                    r.error = f"replicate {i} is not a number"
                r.replicate_values.append((raw or "").strip())
            numbers[p.key] = series
            r.missing = any(v is None for v in series)
        else:
            names = formulas.names_in(p.formula)
            series = []
            for i in range(p.replicates):
                variables = {}
                for name in names:
                    source = numbers.get(name, [None])
                    variables[name] = source[i] if len(source) > 1 else source[0]
                if any(v is None for v in variables.values()):
                    series.append(None)
                    continue
                try:
                    series.append(formulas.evaluate(p.formula, variables))
                except FormulaError as exc:
                    series.append(None)
                    r.error = str(exc)
            numbers[p.key] = series
            r.replicate_values = [_display(v, p.decimals) for v in series]
            r.missing = any(v is None for v in series) and not r.error

        if p.result_type == ResultType.NUMERIC and not r.missing and not r.error:
            series = numbers[p.key]
            r.mean = formulas.mean(series)
            r.rsd = formulas.rsd_percent(series)
            r.reported = formulas.format_decimal(r.mean, p.decimals)
            if r.is_result:
                below = formulas.detection_text(formulas.parse_decimal(r.reported), p.lod, p.loq)
                if below is not None:
                    r.reported, r.below_bound = below

        if r.is_result:
            limit = limits.get(p.key)
            if limit is not None:
                r.spec_text = limit.text(p.unit)
                if r.below_bound is not None:
                    r.conformance = formulas.check_below(limit.limit_type, limit.low, limit.high, r.below_bound)
                elif r.reported is not None and not r.error:
                    r.conformance = formulas.check(limit.limit_type, limit.low, limit.high, limit.expected_text,
                                                   r.reported)
        out.append(r)
    return Calculation(out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _get_analysis(session: Session, analysis_id: int) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFound("Test not found.")
    return analysis


def _get_sample(session: Session, sample_pk: int) -> Sample:
    sample = session.get(Sample, sample_pk)
    if sample is None:
        raise NotFound("Sample not found.")
    return sample


def _label(session: Session, analysis: Analysis) -> str:
    method = session.get(TestMethod, analysis.method_id)
    sample = session.get(Sample, analysis.sample_id)
    return f"{sample.sample_id} / {method.code} v{method.version} (#{analysis.id})"


def limits_for(session: Session, sample: Sample, method_code: str) -> dict[str, LimitInfo]:
    if not sample.specification_id:
        return {}
    rows = session.scalars(select(SpecLimit).where(SpecLimit.spec_id == sample.specification_id,
                                                   SpecLimit.method_code == method_code))
    return {r.parameter_key: LimitInfo(r.limit_type, r.low, r.high, r.expected_text) for r in rows}


def stored_values(session: Session, analysis_id: int) -> dict[tuple[str, int], str]:
    rows = session.scalars(select(ResultValue).where(ResultValue.analysis_id == analysis_id))
    return {(r.parameter_key, r.replicate): r.value for r in rows}


def calculation_for(session: Session, analysis: Analysis,
                    values: dict[tuple[str, int], str] | None = None) -> Calculation:
    method = session.get(TestMethod, analysis.method_id)
    sample = session.get(Sample, analysis.sample_id)
    params = [to_spec(p) for p in parameters_of(session, method.id)]
    return calculate(params, stored_values(session, analysis.id) if values is None else values,
                     limits_for(session, sample, method.code))


def _refresh_sample_status(session: Session, sample: Sample) -> None:
    """Keep the sample status in step with its tests:
    Received → In Testing → Testing Complete (all signed) → Reviewed (all reviewed)."""
    statuses = list(session.scalars(select(Analysis.status).where(Analysis.sample_id == sample.id)))
    if sample.status not in TESTABLE:
        return
    live = [s for s in statuses if s != AnalysisStatus.CANCELLED]
    if not live:
        new = SampleStatus.RECEIVED
    elif all(s == AnalysisStatus.REVIEWED for s in live):
        new = SampleStatus.REVIEWED
    elif all(s in (AnalysisStatus.COMPLETE, AnalysisStatus.REVIEWED) for s in live):
        new = SampleStatus.TESTING_COMPLETE
    elif any(s != AnalysisStatus.PENDING for s in live):
        new = SampleStatus.IN_TESTING
    else:
        new = SampleStatus.RECEIVED if sample.status == SampleStatus.RECEIVED else SampleStatus.IN_TESTING
    if new != sample.status:
        audit.record(session, AuditAction.SAMPLE_UPDATED, entity_type="sample", entity_id=sample.sample_id,
                     old={"status": sample.status}, new={"status": new.value}, reason="Testing status changed")
        sample.status = new
        sample.updated_at = utcnow()


# ---------------------------------------------------------------------------
# assigning
# ---------------------------------------------------------------------------


def set_specification(session: Session, actor: SessionUser, sample_pk: int, spec_id: int | None,
                      reason: str | None = None) -> Sample:
    require(actor, Permission.EDIT_RECORDS)
    sample = _get_sample(session, sample_pk)
    if sample.status not in ASSIGNABLE:
        raise ValidationError(f"The specification of {sample.sample_id} cannot be changed while it is {sample.status}.")
    if spec_id == sample.specification_id:
        return sample
    if spec_id is not None:
        spec = session.get(Specification, spec_id)
        if spec is None or spec.status != VersionStatus.ACTIVE:
            raise ValidationError("Choose an active specification.")
    if sample.specification_id is not None and not (reason or "").strip():
        raise ValidationError("A reason is required to change the specification of a sample.")
    old = sample.specification_id
    sample.specification_id = spec_id
    sample.updated_at = utcnow()

    def label(i):
        if i is None:
            return None
        s = session.get(Specification, i)
        return f"{s.code} v{s.version}"

    audit.record(session, AuditAction.SAMPLE_SPEC_SET, actor=actor, entity_type="sample", entity_id=sample.sample_id,
                 old={"specification": label(old)} if old else None, new={"specification": label(spec_id)},
                 reason=(reason or "").strip() or None)
    return sample


def assign_tests(session: Session, actor: SessionUser, sample_pk: int, method_ids: list[int],
                 assigned_to_id: int | None = None) -> list[Analysis]:
    require(actor, Permission.CREATE_RECORDS)
    sample = _get_sample(session, sample_pk)
    if sample.status not in ASSIGNABLE:
        raise ValidationError(f"Tests cannot be added to {sample.sample_id} while it is {sample.status}.")
    if not method_ids:
        raise ValidationError("Choose at least one test.")
    if assigned_to_id is not None:
        person = session.get(User, assigned_to_id)
        if person is None or not person.is_active:
            raise ValidationError("Choose an active user to assign the tests to.")
    created = []
    for method_id in method_ids:
        method = session.get(TestMethod, method_id)
        if method is None or method.status != VersionStatus.ACTIVE:
            raise ValidationError("Only active methods can be assigned.")
        analysis = Analysis(sample_id=sample.id, method_id=method.id, status=AnalysisStatus.PENDING,
                            assigned_to_id=assigned_to_id, created_by_id=actor.id)
        session.add(analysis)
        created.append((analysis, method))
    session.flush()
    audit.record(session, AuditAction.TESTS_ASSIGNED, actor=actor, entity_type="sample", entity_id=sample.sample_id,
                 new={"tests": [f"{m.code} v{m.version}" for _, m in created], "assigned_to_id": assigned_to_id})
    if sample.status in (SampleStatus.TESTING_COMPLETE, SampleStatus.REVIEWED):
        _refresh_sample_status(session, sample)
    return [a for a, _ in created]


def cancel_analysis(session: Session, actor: SessionUser, analysis_id: int, reason: str) -> Analysis:
    require(actor, Permission.EDIT_RECORDS)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to cancel a test.")
    analysis = _get_analysis(session, analysis_id)
    if analysis.status not in OPEN_ANALYSIS:
        raise ValidationError(f"This test is {analysis.status} and cannot be cancelled.")
    analysis.status = AnalysisStatus.CANCELLED
    analysis.cancel_reason = reason.strip()
    audit.record(session, AuditAction.TEST_CANCELLED, actor=actor, entity_type="test",
                 entity_id=_label(session, analysis), reason=reason.strip())
    _refresh_sample_status(session, session.get(Sample, analysis.sample_id))
    return analysis


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


def save_values(session: Session, actor: SessionUser, analysis_id: int,
                values: dict[tuple[str, int], str | None], reason: str | None = None) -> Calculation:
    """Save entered values. New values need no reason; changing or clearing a
    value that was already saved requires one (old and new values are audited)."""
    require(actor, Permission.EDIT_RECORDS)
    analysis = _get_analysis(session, analysis_id)
    if analysis.status not in OPEN_ANALYSIS:
        raise ValidationError(f"This test is {analysis.status}; its results can no longer be changed.")
    sample = session.get(Sample, analysis.sample_id)
    if sample.status not in TESTABLE:
        raise ValidationError(f"Results cannot be entered while the sample is {sample.status}.")
    params = {p.key: to_spec(p) for p in parameters_of(session, analysis.method_id)}
    from aurum_lims.services.usage import lot_filled_keys  # local import: usage depends on this module

    lot_filled = lot_filled_keys(session, analysis.method_id)
    existing = {(r.parameter_key, r.replicate): r for r in
                session.scalars(select(ResultValue).where(ResultValue.analysis_id == analysis.id))}

    # Work out every change first; nothing is written until all checks pass.
    added, changed_old, changed_new = {}, {}, {}
    inserts, updates, deletes = [], [], []
    now = utcnow()
    for (key, replicate), raw in values.items():
        p = params.get(key)
        if p is None or p.calculated or not 1 <= replicate <= p.replicates:
            raise ValidationError(f"'{key}' replicate {replicate} is not an entry field of this method.")
        if key in lot_filled:
            raise ValidationError(f"{p.name} is filled in automatically from the {lot_filled[key].label} lot.")
        text = (raw or "").strip()
        label = f"{p.name} #{replicate}" if p.replicates > 1 else p.name
        if text:
            if len(text) > MAX_VALUE_LENGTH:
                raise ValidationError(f"{label}: the value is too long.")
            if p.result_type == ResultType.NUMERIC:
                try:
                    formulas.parse_decimal(text)
                except ValidationError:
                    raise ValidationError(f"{label}: '{text}' is not a number.") from None
            elif p.result_type == ResultType.PASS_FAIL and text not in PASS_FAIL_VALUES:
                raise ValidationError(f"{label}: choose Pass or Fail.")
        row = existing.get((key, replicate))
        if row is None:
            if text:
                inserts.append((key, replicate, text))
                added[label] = text
        elif row.value != text:
            changed_old[label], changed_new[label] = row.value, text or None
            (updates.append((row, text)) if text else deletes.append(row))

    if changed_old and not (reason or "").strip():
        raise ValidationError("A reason is required to change values that were already saved: "
                              + ", ".join(changed_old) + ".")
    for key, replicate, text in inserts:
        session.add(ResultValue(analysis_id=analysis.id, parameter_key=key, replicate=replicate, value=text,
                                entered_by_id=actor.id, updated_by_id=actor.id))
    for row, text in updates:
        row.value, row.updated_at, row.updated_by_id = text, now, actor.id
    for row in deletes:
        session.delete(row)
    label = _label(session, analysis)
    if added:
        audit.record(session, AuditAction.RESULTS_ENTERED, actor=actor, entity_type="test", entity_id=label, new=added)
    if changed_old:
        audit.record(session, AuditAction.RESULTS_CHANGED, actor=actor, entity_type="test", entity_id=label,
                     old=changed_old, new=changed_new, reason=reason.strip())
    if (added or changed_old) and analysis.status == AnalysisStatus.PENDING:
        analysis.status = AnalysisStatus.IN_PROGRESS
        analysis.started_at = now
        _refresh_sample_status(session, sample)
    session.flush()
    return calculation_for(session, analysis)


def _next_oos_number(session: Session) -> str:
    year = datetime.now().astimezone().year
    counter = session.get(IdCounter, ("oos", str(year)))
    if counter is None:
        counter = IdCounter(scope="oos", period=str(year), last_value=0)
        session.add(counter)
    counter.last_value += 1
    session.flush()
    return f"OOS-{year}-{counter.last_value:04d}"


def complete_analysis(session: Session, actor: SessionUser, analysis_id: int, password: str,
                      comments: dict[str, str] | None = None) -> SignatureResult:
    """Sign the test as performed. Every out-of-specification result needs a
    comment and opens an OOS investigation. The caller must commit even when
    the signature fails (failed attempts are audited)."""
    require(actor, Permission.EDIT_RECORDS)
    analysis = _get_analysis(session, analysis_id)
    if analysis.status not in OPEN_ANALYSIS:
        raise ValidationError(f"This test is already {analysis.status}.")
    sample = session.get(Sample, analysis.sample_id)
    if sample.status not in TESTABLE:
        raise ValidationError(f"The sample is {sample.status}.")
    from aurum_lims.services.usage import readiness_problems  # local import: usage depends on this module

    not_ready = readiness_problems(session, analysis)
    if not_ready:
        raise ValidationError("Equipment and materials:\n" + "\n".join(not_ready))
    calc = calculation_for(session, analysis)
    if not calc.complete:
        raise ValidationError("The results are not complete:\n" + "\n".join(calc.problems()))
    comments = {k: (v or "").strip() for k, v in (comments or {}).items()}
    missing = [p.name for p in calc.failures if not comments.get(p.key)]
    if missing:
        raise ValidationError("Add a comment for each out-of-specification result: " + ", ".join(missing) + ".")
    label = _label(session, analysis)
    result = esign.sign(session, actor, password, SignatureMeaning.PERFORMED, "test", label,
                        reason="Test performed; results complete")
    if not result.ok:
        return result

    now = utcnow()
    # A test returned by the reviewer and signed again gets a new revision of its
    # results; earlier revisions stay on record.
    previous = session.scalar(select(func.max(ReportableResult.revision))
                              .where(ReportableResult.analysis_id == analysis.id))
    revision = (previous or 0) + 1
    analysis.revision = revision
    for p in calc.results:
        session.add(ReportableResult(
            analysis_id=analysis.id, revision=revision, parameter_key=p.key, parameter_name=p.name, unit=p.unit,
            replicate_values=json.dumps(p.replicate_values, ensure_ascii=False),
            mean=_display(p.mean, p.decimals) if p.mean is not None else None,
            rsd=formulas.format_decimal(p.rsd, 2) if p.rsd is not None else None,
            reported_value=p.reported or "", spec_text=p.spec_text,
            conformance=(p.conformance or Conformance.REPORT).value, comment=comments.get(p.key) or None,
            lod=p.lod, loq=p.loq,
        ))
    analysis.status = AnalysisStatus.COMPLETE
    analysis.completed_at = now
    analysis.completed_by_id = actor.id
    analysis.signature_id = result.signature.id
    audit.record(session, AuditAction.TEST_COMPLETED, actor=actor, entity_type="test", entity_id=label,
                 new={"revision": revision,
                      **{p.name: f"{p.reported} ({(p.conformance or Conformance.REPORT).value})" for p in calc.results}})
    already_open = set(session.scalars(select(OOSInvestigation.parameter_key).where(
        OOSInvestigation.analysis_id == analysis.id, OOSInvestigation.status == "Open")))
    for p in calc.failures:
        if p.key in already_open:
            continue  # the existing investigation covers the re-signed result
        number = _next_oos_number(session)
        session.add(OOSInvestigation(
            number=number, sample_id=sample.id, analysis_id=analysis.id, parameter_key=p.key,
            parameter_name=p.name, result_value=formulas.with_unit(p.reported, p.unit), spec_text=p.spec_text,
            status="Open", phase="Phase I – laboratory investigation", description=comments[p.key],
            opened_by_id=actor.id,
        ))
        audit.record(session, AuditAction.OOS_OPENED, actor=actor, entity_type="oos", entity_id=number,
                     new={"sample": sample.sample_id, "result": f"{p.name} = {p.reported}", "limit": p.spec_text},
                     reason=comments[p.key])
    _refresh_sample_status(session, sample)
    session.flush()
    return result


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


@dataclass
class AnalysisRow:
    id: int
    sample_pk: int
    sample_id: str
    material: str | None
    method_code: str
    method_name: str
    method_version: int
    status: str
    assigned_to: str | None
    created_at: datetime
    completed_at: datetime | None
    priority: str
    due_date: date | None
    sample_status: str
    completed_by: str | None = None


def _rows(session: Session, stmt) -> list[AnalysisRow]:
    rows = []
    for a, m, s, assignee, signer in session.execute(stmt):
        rows.append(AnalysisRow(
            a.id, s.id, s.sample_id, s.material_name or s.sampling_point, m.code, m.name, m.version, a.status,
            assignee, a.created_at, a.completed_at, s.priority, s.due_date, s.status, signer,
        ))
    return rows


def _base():
    assignee = aliased(User)
    signer = aliased(User)
    return (
        select(Analysis, TestMethod, Sample, assignee.full_name, signer.full_name)
        .join(TestMethod, Analysis.method_id == TestMethod.id)
        .join(Sample, Analysis.sample_id == Sample.id)
        .outerjoin(assignee, Analysis.assigned_to_id == assignee.id)
        .outerjoin(signer, Analysis.completed_by_id == signer.id)
    )


def worklist(session: Session, actor: SessionUser, *, mine_only: bool = False, status: str | None = None,
             limit: int = 2000) -> list[AnalysisRow]:
    """Open tests (pending / in progress), urgent and oldest first."""
    require(actor, Permission.VIEW_RECORDS)
    stmt = _base()
    stmt = stmt.where(Analysis.status == status) if status else stmt.where(Analysis.status.in_(OPEN_ANALYSIS))
    if mine_only:
        stmt = stmt.where(Analysis.assigned_to_id == actor.id)
    stmt = stmt.order_by((Sample.priority == "Urgent").desc(), Sample.due_date.is_(None), Sample.due_date,
                         Analysis.id).limit(limit)
    return _rows(session, stmt)


def analyses_for_sample(session: Session, actor: SessionUser, sample_pk: int) -> list[AnalysisRow]:
    require(actor, Permission.VIEW_RECORDS)
    return _rows(session, _base().where(Analysis.sample_id == sample_pk).order_by(Analysis.id))


def reportable_results(session: Session, analysis_id: int, revision: int | None = None) -> list[ReportableResult]:
    """The signed results of a test (current revision unless one is given)."""
    if revision is None:
        revision = session.scalar(select(Analysis.revision).where(Analysis.id == analysis_id)) or 1
    return list(session.scalars(select(ReportableResult).where(ReportableResult.analysis_id == analysis_id,
                                                               ReportableResult.revision == revision)
                                .order_by(ReportableResult.id)))


def open_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Analysis.status, func.count()).where(Analysis.status.in_(OPEN_ANALYSIS))
                           .group_by(Analysis.status))
    return {status: count for status, count in rows}
