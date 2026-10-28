"""Second-person review, sample decisions and certificates."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import Analysis, Certificate, Sample
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import certificates, lab_info, oos, review, testing
from aurum_lims.services.samples import SampleStatus
from aurum_lims.services.testing import AnalysisStatus
from tests.conftest import USER_PASSWORD
from tests.test_testing import ASSAY_VALUES, setup  # noqa: F401 - fixture

LOW_ASSAY = {**ASSAY_VALUES, ("area_spl", 1): "9000", ("area_spl", 2): "9050"}


@pytest.fixture
def signed(db, setup, qapp):  # noqa: F811 - qapp is needed to render PDFs
    """A sample with assay + appearance signed by the analyst."""
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        assay, app = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"],
                                                          [setup["assay"], setup["appearance"]])]
        testing.save_values(s, setup["analyst"], assay, ASSAY_VALUES)
        testing.save_values(s, setup["analyst"], app, {("appearance", 1): "White round tablet"})
    for analysis_id in (assay, app):
        with db.session() as s:
            assert testing.complete_analysis(s, setup["analyst"], analysis_id, USER_PASSWORD).ok
    return {**setup, "assay_test": assay, "app_test": app}


def _review_all(db, signed):
    for analysis_id in (signed["assay_test"], signed["app_test"]):
        with db.session() as s:
            assert review.accept_test(s, signed["qa"], analysis_id, USER_PASSWORD).ok


def test_review_segregation_and_permissions(db, signed, make_user):
    other_analyst = make_user("analyst2", Role.ANALYST)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            review.accept_test(s, other_analyst, signed["assay_test"], USER_PASSWORD)
    # A QA reviewer who entered a value on the test may not review it.
    with db.session() as s:
        s.get(Analysis, signed["assay_test"]).completed_by_id = signed["qa"].id
    with db.session() as s:
        with pytest.raises(ValidationError, match="second person"):
            review.accept_test(s, signed["qa"], signed["assay_test"], USER_PASSWORD)


def test_review_then_approve_issues_coa(db, signed, admin):
    with db.session() as s:
        assert [r.id for r in review.tests_awaiting_review(s, signed["qa"])] == [signed["assay_test"], signed["app_test"]]
    _review_all(db, signed)
    with db.session() as s:
        assert s.get(Sample, signed["sample"]).status == SampleStatus.REVIEWED
        assert [x.id for x in review.samples_awaiting_decision(s, signed["qa"])] == [signed["sample"]]
        summary = review.decision_summary(s, signed["qa"], signed["sample"])
        assert summary.conforms and summary.can_decide and len(summary.results) == 2
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            review.decide_sample(s, admin, signed["sample"], approve=True, password="Spectroscopy2026")
    with db.session() as s:
        assert not review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password="nope-nope-1").ok
    with db.session() as s:
        result = review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD)
        assert result.ok and result.certificate_number.startswith("CoA-")
    with db.session() as s:
        assert s.get(Sample, signed["sample"]).status == SampleStatus.APPROVED
        number, pdf = certificates.get_pdf(s, signed["analyst"], result.certificate_id)
        assert pdf.startswith(b"%PDF") and number == result.certificate_number
        [row] = certificates.list_certificates(s, signed["analyst"])
        assert row.kind == certificates.COA and row.decision == "Approved"
        # Approved samples are locked.
        with pytest.raises(ValidationError):
            testing.assign_tests(s, signed["analyst"], signed["sample"], [signed["assay"]])


def test_certificate_content(db, signed, admin):
    with db.session() as s:
        lab_info.set_lab_info(s, admin, lab_info.LabInfo(
            lab_name="Aurum QC Laboratory", lab_address="1 Lab Way\nGoldtown", lab_contact="qc@example.com"),
            "Initial set-up")
    _review_all(db, signed)
    with db.session() as s:
        assert review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD).ok
    with db.session() as s:
        sample = s.get(Sample, signed["sample"])
        page = certificates.build_html(s, sample, review.decision_for(s, sample.id), "CoA-TEST-0001",
                                       certificates.COA)
    assert "Aurum QC Laboratory" in page and "Goldtown" in page
    assert "100.0" in page and "95.0 – 105.0 %" in page and "Conforms" in page
    assert "Performed by Jsmith" in page and "Reviewed by Qa.Lee" in page
    assert "Approved by Qa.Lee" in page and "<b>conforms</b>" in page
    assert "SPEC-FP-001 v1 – Aurumex 10 mg release" in page and "â€" not in page


def test_return_for_correction_creates_new_revision(db, signed):
    with db.session() as s:
        with pytest.raises(ValidationError, match="corrected"):
            review.return_test(s, signed["qa"], signed["assay_test"], "")
        review.return_test(s, signed["qa"], signed["assay_test"], "Sample weight 2 transcribed wrongly")
    with db.session() as s:
        assert s.get(Analysis, signed["assay_test"]).status == AnalysisStatus.RETURNED
        assert s.get(Sample, signed["sample"]).status == SampleStatus.IN_TESTING
        assert signed["assay_test"] in {r.id for r in testing.worklist(s, signed["analyst"])}
        testing.save_values(s, signed["analyst"], signed["assay_test"], {("wt_spl", 2): "25.15"},
                            reason="Corrected per review")
    with db.session() as s:
        assert testing.complete_analysis(s, signed["analyst"], signed["assay_test"], USER_PASSWORD).ok
    with db.session() as s:
        analysis = s.get(Analysis, signed["assay_test"])
        assert analysis.revision == 2 and analysis.status == AnalysisStatus.COMPLETE
        assert len(testing.reportable_results(s, analysis.id, revision=1)) == 1  # history kept
        assert testing.reportable_results(s, analysis.id)[0].reported_value != \
            testing.reportable_results(s, analysis.id, revision=1)[0].reported_value


def test_oos_blocks_decision_and_reject_issues_report(db, setup, qapp):  # noqa: F811
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        [assay] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        testing.save_values(s, setup["analyst"], assay, LOW_ASSAY)
    with db.session() as s:
        testing.complete_analysis(s, setup["analyst"], assay, USER_PASSWORD, {"assay": "Low"})
    with db.session() as s:
        review.accept_test(s, setup["qa"], assay, USER_PASSWORD)
    with db.session() as s:
        summary = review.decision_summary(s, setup["qa"], setup["sample"])
        assert summary.open_oos and not summary.can_decide and not summary.conforms
        with pytest.raises(ValidationError, match="OOS"):
            review.decide_sample(s, setup["qa"], setup["sample"], approve=False, password=USER_PASSWORD, comment="x")
        [inv] = oos.list_investigations(s, setup["qa"])
    with db.session() as s:
        oos.close(s, setup["qa"], inv.id, conclusion=oos.CONCLUSIONS[0], root_cause="Batch sub-potent",
                  password=USER_PASSWORD)
    with db.session() as s:
        with pytest.raises(ValidationError, match="cannot be"):
            review.decide_sample(s, setup["qa"], setup["sample"], approve=True, password=USER_PASSWORD)
        with pytest.raises(ValidationError, match="comment"):
            review.decide_sample(s, setup["qa"], setup["sample"], approve=False, password=USER_PASSWORD)
        result = review.decide_sample(s, setup["qa"], setup["sample"], approve=False, password=USER_PASSWORD,
                                      comment="Assay below specification (OOS confirmed)")
        assert result.ok and result.certificate_number.startswith("TR-")
    with db.session() as s:
        assert s.get(Sample, setup["sample"]).status == SampleStatus.REJECTED


def test_certificates_are_immutable_and_verified(db, signed):
    _review_all(db, signed)
    with db.session() as s:
        cert_id = review.decide_sample(s, signed["qa"], signed["sample"], approve=True,
                                       password=USER_PASSWORD).certificate_id
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE certificates SET number = 'X'"))
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM sample_decisions"))
    # Simulate tampering outside the app: the fingerprint check catches it.
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER certificates_no_update")
        conn.execute(text("UPDATE certificates SET pdf = X'255044462D2D'"))
    with db.session() as s:
        with pytest.raises(certificates.CertificateIntegrityError):
            certificates.get_pdf(s, signed["qa"], cert_id)


def test_lab_info_rules(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            lab_info.set_lab_info(s, analyst, lab_info.LabInfo(lab_name="X"), "r")
        with pytest.raises(ValidationError, match="name is required"):
            lab_info.set_lab_info(s, admin, lab_info.LabInfo(lab_name=""), "r")
        lab_info.set_lab_info(s, admin, lab_info.LabInfo(lab_name="Aurum QC", paper_size="A4"), "Set-up")
    with db.session() as s:
        assert lab_info.get_lab_info(s).paper_size == "A4"
