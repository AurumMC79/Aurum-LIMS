"""Reopening a decided sample and issuing an amended certificate."""

import pytest
from PySide6.QtWidgets import QPushButton
from sqlalchemy import select

from aurum_lims.db.models import Analysis, AuditEntry, Certificate, Sample
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.services import certificates, esign, review, samples, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.samples import SampleDetails, SampleStatus
from aurum_lims.services.testing import AnalysisStatus
from aurum_lims.ui.sample_dialogs import SampleDetailDialog
from tests.conftest import USER_PASSWORD
from tests.test_review import _review_all, signed  # noqa: F401 - fixture
from tests.test_testing import setup  # noqa: F401 - fixture


def _approve(db, signed):  # noqa: F811
    with db.session() as s:
        result = review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD)
        assert result.ok
        return result.certificate_number


@pytest.fixture
def approved(db, signed):  # noqa: F811
    _review_all(db, signed)
    return {**signed, "coa": _approve(db, signed)}


def _reopen(db, data, reason="Sample weight transcribed wrongly", tests=()):
    with db.session() as s:
        result = review.reopen_sample(s, data["qa"], data["sample"], password=USER_PASSWORD, reason=reason,
                                      return_test_ids=list(tests))
        assert result.ok


def test_reopen_rules(db, approved, make_user):
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            review.reopen_sample(s, approved["analyst"], approved["sample"], password=USER_PASSWORD, reason="x")
        with pytest.raises(ValidationError, match="Explain"):
            review.reopen_sample(s, approved["qa"], approved["sample"], password=USER_PASSWORD, reason=" ")
        with pytest.raises(ValidationError, match="Only reviewed tests"):
            review.reopen_sample(s, approved["qa"], approved["sample"], password=USER_PASSWORD, reason="x",
                                 return_test_ids=[999])
    with db.session() as s:  # a wrong password is audited and changes nothing
        assert not review.reopen_sample(s, approved["qa"], approved["sample"], password="wrong-pass-1",
                                        reason="x").ok
    with db.session() as s:
        assert s.get(Sample, approved["sample"]).status == SampleStatus.APPROVED


def test_only_decided_samples_can_be_reopened(db, signed):  # noqa: F811
    with db.session() as s:
        with pytest.raises(ValidationError, match="Only approved or rejected"):
            review.reopen_sample(s, signed["qa"], signed["sample"], password=USER_PASSWORD, reason="x")


def test_correct_a_result_and_issue_an_amended_coa(db, approved):
    _reopen(db, approved, tests=[approved["assay_test"]])
    with db.session() as s:
        sample = s.get(Sample, approved["sample"])
        assert sample.status == SampleStatus.IN_TESTING
        assert sample.amendment_reason == "Sample weight transcribed wrongly"
        assay = s.get(Analysis, approved["assay_test"])
        assert assay.status == AnalysisStatus.RETURNED
        assert assay.review_comment == "Reopened for amendment: Sample weight transcribed wrongly"
        assert s.get(Analysis, approved["app_test"]).status == AnalysisStatus.REVIEWED
        assert [sig.meaning for sig in esign.signatures_for(s, "sample", sample.sample_id)][-1] == "Reopened by"
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.SAMPLE_REOPENED)).one()
        assert entry.reason == "Sample weight transcribed wrongly" and '"Approved"' in entry.old_values
        testing.save_values(s, approved["analyst"], approved["assay_test"], {("wt_spl", 2): "25.15"},
                            reason="Corrected per amendment")
    with db.session() as s:
        assert testing.complete_analysis(s, approved["analyst"], approved["assay_test"], USER_PASSWORD).ok
    with db.session() as s:
        assert review.accept_test(s, approved["qa"], approved["assay_test"], USER_PASSWORD).ok
    with db.session() as s:
        summary = review.decision_summary(s, approved["qa"], approved["sample"])
        assert summary.can_decide and summary.amends == approved["coa"]
    amended = _approve(db, approved)
    assert amended == f"{approved['coa']}-A1"
    with db.session() as s:
        new = s.scalar(select(Certificate).where(Certificate.number == amended))
        old = s.scalar(select(Certificate).where(Certificate.number == approved["coa"]))
        assert new.supersedes_id == old.id and new.amendment == 1
        assert new.amendment_reason == "Sample weight transcribed wrongly"
        assert s.get(Sample, approved["sample"]).amendment_reason is None
        assert s.get(Sample, approved["sample"]).status == SampleStatus.APPROVED
        rows = {r.number: r for r in certificates.list_certificates(s, approved["analyst"])}
        assert rows[approved["coa"]].superseded_by == amended and rows[amended].superseded_by is None
        assert certificates.current_certificate(s, approved["sample"]).number == amended
        # Both PDFs remain intact and retrievable.
        assert certificates.get_pdf(s, approved["analyst"], old.id)[1].startswith(b"%PDF")
        assert certificates.get_pdf(s, approved["analyst"], new.id)[1].startswith(b"%PDF")
        sample = s.get(Sample, approved["sample"])
        page = certificates.build_html(s, sample, review.decision_for(s, sample.id), amended, certificates.COA,
                                       old, new.amendment_reason)
    assert "AMENDED CERTIFICATE OF ANALYSIS" in page
    assert f"supersedes Certificate of Analysis <b>{approved['coa']}</b>" in page
    assert "Reason for amendment: Sample weight transcribed wrongly" in page


def test_correct_details_without_retesting_then_amend_again(db, approved):
    _reopen(db, approved, reason="Lot number typed wrongly")
    with db.session() as s:
        assert s.get(Sample, approved["sample"]).status == SampleStatus.REVIEWED  # straight back to QA
        samples.update_details(s, approved["analyst"], approved["sample"],
                               SampleDetails(material_name="Aurumex 10 mg tablets", lot_number="B1-REV"),
                               "Lot number corrected per batch record")
    first = _approve(db, approved)
    _reopen(db, approved, reason="Customer name missing")
    second = _approve(db, approved)
    assert (first, second) == (f"{approved['coa']}-A1", f"{approved['coa']}-A2")


def test_amendment_to_rejection_issues_a_test_report(db, approved):
    _reopen(db, approved, reason="Assay result reassessed")
    with db.session() as s:
        result = review.decide_sample(s, approved["qa"], approved["sample"], approve=False, password=USER_PASSWORD,
                                      comment="Rejected after reassessment")
        assert result.ok and result.certificate_number.startswith("TR-")
    with db.session() as s:
        report = s.get(Certificate, result.certificate_id)
        assert report.amendment == 1
        assert s.get(Certificate, report.supersedes_id).number == approved["coa"]


def test_reopen_button_only_for_approvers(qtbot, db, approved):
    def buttons(user):
        dialog = SampleDetailDialog(db, user, approved["sample"])
        qtbot.addWidget(dialog)
        return [b.text() for b in dialog.findChildren(QPushButton)]

    assert "Reopen for amendment…" in buttons(approved["qa"])
    assert "Reopen for amendment…" not in buttons(approved["analyst"])
