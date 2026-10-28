"""Second-person review of tests and QA's final decision on samples.

1. **Test review** (QA Reviewer, never the person who did the work): each
   signed test is accepted with an electronic signature ("Reviewed by") or
   returned to the analyst with a comment. A returned test is corrected,
   signed again (new revision of the results) and comes back for review.
2. **Sample decision** (QA Reviewer): once every test is reviewed and no OOS
   investigation is open, the sample is Approved or Rejected with an
   electronic signature. Approval issues a Certificate of Analysis; rejection
   issues a Test Report. Only conforming samples can be approved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import (
    Analysis,
    OOSInvestigation,
    ResultValue,
    Sample,
    SampleDecision,
    TestMethod,
    User,
    utcnow,
)
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, certificates, esign, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult
from aurum_lims.services.samples import SampleStatus
from aurum_lims.services.testing import AnalysisStatus

APPROVED = "Approved"
REJECTED = "Rejected"


def _analysis(session: Session, analysis_id: int) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFound("Test not found.")
    return analysis


def _contributors(session: Session, analysis: Analysis) -> set[int]:
    """Everyone who entered, changed or signed values on the test."""
    people = {analysis.completed_by_id}
    for entered, updated in session.execute(
            select(ResultValue.entered_by_id, ResultValue.updated_by_id).where(ResultValue.analysis_id == analysis.id)):
        people.update((entered, updated))
    return {p for p in people if p is not None}


def _check_reviewable(session: Session, actor: SessionUser, analysis: Analysis) -> None:
    require(actor, Permission.REVIEW_RECORDS)
    if analysis.status != AnalysisStatus.COMPLETE:
        raise ValidationError(f"Only signed tests waiting for review can be reviewed (this one is {analysis.status}).")
    if actor.id in _contributors(session, analysis):
        raise ValidationError("You entered or signed results on this test, so you cannot review it. "
                              "A second person must do the review.")


def accept_test(session: Session, actor: SessionUser, analysis_id: int, password: str,
                comment: str | None = None) -> SignatureResult:
    analysis = _analysis(session, analysis_id)
    _check_reviewable(session, actor, analysis)
    label = testing._label(session, analysis)
    result = esign.sign(session, actor, password, SignatureMeaning.REVIEWED, "test", label,
                        reason=(comment or "").strip() or "Results reviewed and accepted")
    if not result.ok:
        return result
    analysis.status = AnalysisStatus.REVIEWED
    analysis.reviewed_at = utcnow()
    analysis.reviewed_by_id = actor.id
    analysis.review_signature_id = result.signature.id
    analysis.review_comment = (comment or "").strip() or None
    audit.record(session, AuditAction.TEST_REVIEWED, actor=actor, entity_type="test", entity_id=label,
                 new={"decision": "accepted", "revision": analysis.revision}, reason=analysis.review_comment)
    testing._refresh_sample_status(session, session.get(Sample, analysis.sample_id))
    return result


def return_test(session: Session, actor: SessionUser, analysis_id: int, comment: str) -> Analysis:
    """Send a test back to the analyst for correction (comment required)."""
    analysis = _analysis(session, analysis_id)
    _check_reviewable(session, actor, analysis)
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError("Explain what needs to be corrected.")
    analysis.status = AnalysisStatus.RETURNED
    analysis.review_comment = comment
    analysis.reviewed_at = utcnow()
    analysis.reviewed_by_id = actor.id
    analysis.review_signature_id = None
    audit.record(session, AuditAction.TEST_RETURNED, actor=actor, entity_type="test",
                 entity_id=testing._label(session, analysis), reason=comment)
    testing._refresh_sample_status(session, session.get(Sample, analysis.sample_id))
    return analysis


# ---------------------------------------------------------------------------
# queues
# ---------------------------------------------------------------------------


def tests_awaiting_review(session: Session, actor: SessionUser) -> list[testing.AnalysisRow]:
    require(actor, Permission.REVIEW_RECORDS)
    stmt = testing._base().where(Analysis.status == AnalysisStatus.COMPLETE).order_by(
        (Sample.priority == "Urgent").desc(), Analysis.completed_at)
    return testing._rows(session, stmt)


@dataclass
class SampleForDecision:
    id: int
    sample_id: str
    material: str | None
    lot_number: str | None
    priority: str
    due_date: date | None
    updated_at: datetime


def samples_awaiting_decision(session: Session, actor: SessionUser) -> list[SampleForDecision]:
    require(actor, Permission.REVIEW_RECORDS)
    rows = session.scalars(select(Sample).where(Sample.status == SampleStatus.REVIEWED)
                           .order_by((Sample.priority == "Urgent").desc(), Sample.updated_at))
    return [SampleForDecision(s.id, s.sample_id, s.material_name or s.sampling_point, s.lot_number, s.priority,
                              s.due_date, s.updated_at) for s in rows]


@dataclass
class ResultLine:
    test: str
    parameter: str
    reported: str
    unit: str | None
    spec_text: str | None
    conformance: str
    performed_by: str | None
    reviewed_by: str | None


@dataclass
class DecisionSummary:
    sample_id: str
    status: str
    results: list[ResultLine] = field(default_factory=list)
    open_oos: list[str] = field(default_factory=list)
    unreviewed: list[str] = field(default_factory=list)

    @property
    def conforms(self) -> bool:
        return bool(self.results) and all(r.conformance != "Fail" for r in self.results)

    @property
    def can_decide(self) -> bool:
        return self.status == SampleStatus.REVIEWED and not self.open_oos and not self.unreviewed


def decision_summary(session: Session, actor: SessionUser, sample_pk: int) -> DecisionSummary:
    require(actor, Permission.VIEW_RECORDS)
    sample = session.get(Sample, sample_pk)
    if sample is None:
        raise NotFound("Sample not found.")
    names = {u.id: u.full_name for u in session.scalars(select(User))}
    summary = DecisionSummary(sample.sample_id, sample.status)
    for analysis, method in session.execute(
            select(Analysis, TestMethod).join(TestMethod, Analysis.method_id == TestMethod.id)
            .where(Analysis.sample_id == sample.id, Analysis.status != AnalysisStatus.CANCELLED)
            .order_by(Analysis.id)):
        label = f"{method.code} v{method.version}"
        if analysis.status != AnalysisStatus.REVIEWED:
            summary.unreviewed.append(f"{label} ({analysis.status})")
            continue
        for res in testing.reportable_results(session, analysis.id):
            summary.results.append(ResultLine(
                label, res.parameter_name, res.reported_value, res.unit, res.spec_text, res.conformance,
                names.get(analysis.completed_by_id), names.get(analysis.reviewed_by_id)))
    summary.open_oos = list(session.scalars(select(OOSInvestigation.number).where(
        OOSInvestigation.sample_id == sample.id, OOSInvestigation.status == "Open")))
    return summary


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------


@dataclass
class DecisionResult:
    signature: SignatureResult
    certificate_id: int | None = None
    certificate_number: str | None = None

    @property
    def ok(self) -> bool:
        return self.signature.ok

    @property
    def message(self) -> str:
        return self.signature.message


def decide_sample(session: Session, actor: SessionUser, sample_pk: int, *, approve: bool, password: str,
                  comment: str | None = None) -> DecisionResult:
    """Approve or reject a reviewed sample and issue its certificate. The caller
    must commit even when the signature fails (the attempt is audited)."""
    require(actor, Permission.APPROVE_RECORDS)
    sample = session.get(Sample, sample_pk)
    if sample is None:
        raise NotFound("Sample not found.")
    summary = decision_summary(session, actor, sample_pk)
    if sample.status != SampleStatus.REVIEWED:
        raise ValidationError(f"{sample.sample_id} is {sample.status}. All tests must be reviewed before a decision.")
    if summary.open_oos:
        raise ValidationError("Close the open OOS investigation(s) first: " + ", ".join(summary.open_oos) + ".")
    if summary.unreviewed:
        raise ValidationError("These tests are not reviewed yet: " + ", ".join(summary.unreviewed) + ".")
    comment = (comment or "").strip() or None
    if approve and not summary.conforms:
        raise ValidationError("The sample has results that do not conform to the specification, so it cannot be "
                              "approved. Reject it, or have the failing test re-tested if the OOS was invalidated.")
    if not approve and not comment:
        raise ValidationError("A comment is required to reject a sample.")
    decision_text = APPROVED if approve else REJECTED
    result = esign.sign(session, actor, password,
                        SignatureMeaning.APPROVED if approve else SignatureMeaning.REJECTED,
                        "sample", sample.sample_id, reason=comment or f"Sample {decision_text.lower()}")
    if not result.ok:
        return DecisionResult(result)
    decision = SampleDecision(sample_id=sample.id, decision=decision_text, conforms=summary.conforms,
                              comment=comment, decided_by_id=actor.id, signature_id=result.signature.id)
    session.add(decision)
    session.flush()
    old_status = sample.status
    sample.status = SampleStatus.APPROVED if approve else SampleStatus.REJECTED
    sample.updated_at = utcnow()
    audit.record(session, AuditAction.SAMPLE_APPROVED if approve else AuditAction.SAMPLE_REJECTED, actor=actor,
                 entity_type="sample", entity_id=sample.sample_id, old={"status": old_status},
                 new={"status": sample.status, "conforms": summary.conforms}, reason=comment)
    certificate = certificates.issue(session, actor, sample, decision)
    return DecisionResult(result, certificate.id, certificate.number)


def decision_for(session: Session, sample_pk: int) -> SampleDecision | None:
    return session.scalar(select(SampleDecision).where(SampleDecision.sample_id == sample_pk)
                          .order_by(SampleDecision.id.desc()).limit(1))
