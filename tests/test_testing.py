"""Methods, specifications, result entry, calculations, completion and OOS."""

from decimal import Decimal as D

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import Analysis, OOSInvestigation, Sample
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import audit, methods, oos, sample_types, samples, specifications, storage, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.formulas import Conformance
from aurum_lims.services.methods import ParameterSpec as P
from aurum_lims.services.samples import Receipt, SampleDetails, SampleStatus
from aurum_lims.services.specifications import LimitSpec as L
from aurum_lims.services.testing import AnalysisStatus
from aurum_lims.services.versioning import VersionStatus
from tests.conftest import USER_PASSWORD

ASSAY_PARAMS = [
    P("wt_std", "Standard weight", "input", unit="mg"),
    P("purity_std", "Standard purity", "input", unit="%"),
    P("area_std", "Standard peak area (mean)", "input"),
    P("wt_spl", "Sample weight", "input", unit="mg", replicates=2),
    P("area_spl", "Sample peak area", "input", replicates=2),
    P("assay", "Assay", "result", unit="%", decimals=1,
      formula="area_spl / area_std * wt_std / wt_spl * purity_std"),
]
APPEARANCE_PARAMS = [P("appearance", "Appearance", "result", result_type="text")]


@pytest.fixture
def setup(db, admin, make_user):
    qa = make_user("qa.lee", Role.QA_REVIEWER)
    analyst = make_user("jsmith", Role.ANALYST)
    with db.session() as s:
        sample_types.seed_defaults(s)
        fridge = storage.create_location(s, admin, name="Fridge 1", kind="Refrigerator").id
        assay = methods.create_method(s, admin, code="hplc-assay", name="Assay by HPLC", parameters=ASSAY_PARAMS,
                                      sop_reference="SOP-QC-101").id
        appearance = methods.create_method(s, admin, code="APP", name="Appearance", parameters=APPEARANCE_PARAMS).id
    for method_id in (assay, appearance):
        with db.session() as s:
            assert methods.activate(s, qa, method_id, USER_PASSWORD).ok
    with db.session() as s:
        spec = specifications.create_spec(s, admin, code="SPEC-FP-001", name="Aurumex 10 mg release",
                                          material_name="Aurumex 10 mg tablets", limits=[
                                              L("HPLC-ASSAY", "assay", "range", "95.0", "105.0"),
                                              L("APP", "appearance", "text", expected_text="White round tablet"),
                                          ]).id
    with db.session() as s:
        assert specifications.activate(s, qa, spec, USER_PASSWORD).ok
    with db.session() as s:
        fp = next(t.id for t in sample_types.list_types(s) if t.code == "FP")
        sample = samples.register_samples(s, analyst, fp, SampleDetails(material_name="Aurumex 10 mg tablets",
                                                                        lot_number="B1"),
                                          receipt=Receipt(location_id=fridge))[0].id
    return {"qa": qa, "analyst": analyst, "assay": assay, "appearance": appearance, "spec": spec, "sample": sample}


def _assign(db, setup, *method_keys):
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        created = testing.assign_tests(s, setup["analyst"], setup["sample"], [setup[k] for k in method_keys])
        return [a.id for a in created]


ASSAY_VALUES = {
    ("wt_std", 1): "25.12", ("purity_std", 1): "99.8", ("area_std", 1): "10120",
    ("wt_spl", 1): "25.30", ("area_spl", 1): "10234",
    ("wt_spl", 2): "25.05", ("area_spl", 2): "10101",
}


# ----- methods -----------------------------------------------------------------

def test_parameter_validation():
    with pytest.raises(ValidationError, match="defined above"):
        methods.validate_parameters([P("x", "X", "result", formula="y * 2"), P("y", "Y", "input")])
    with pytest.raises(ValidationError, match="used twice"):
        methods.validate_parameters([P("a", "A", "input"), P("a", "A2", "result")])
    with pytest.raises(ValidationError, match="different numbers of replicates"):
        methods.validate_parameters([P("a", "A", "input", replicates=2), P("b", "B", "input", replicates=3),
                                     P("c", "C", "result", formula="a+b")])
    with pytest.raises(ValidationError, match="key must start"):
        methods.validate_parameters([P("1abc", "A", "result")])
    with pytest.raises(ValidationError, match="at least one result"):
        methods.validate_parameters([P("a", "A", "input")])
    cleaned = methods.validate_parameters(ASSAY_PARAMS)
    assert cleaned[-1].replicates == 2  # calculated per sample replicate


def test_method_versioning(db, admin, setup, make_user):
    with db.session() as s:
        with pytest.raises(ValidationError, match="Only draft"):
            methods.update_draft(s, admin, setup["assay"], name="X", parameters=ASSAY_PARAMS)
        v2 = methods.new_version(s, admin, setup["assay"]).id
    with db.session() as s:
        methods.update_draft(s, admin, v2, name="Assay by HPLC (rev. column)", parameters=ASSAY_PARAMS)
    with db.session() as s:
        with pytest.raises(PermissionDenied):  # administrators cannot approve (segregation of duties)
            methods.activate(s, make_user("plain.admin", Role.ADMINISTRATOR), v2, USER_PASSWORD)
    with db.session() as s:
        assert methods.activate(s, setup["qa"], v2, USER_PASSWORD).ok
    with db.session() as s:
        assert methods.get_method(s, setup["assay"]).status == VersionStatus.RETIRED
        assert methods.active_version(s, "HPLC-ASSAY").version == 2


def test_spec_limit_validation(db, admin, setup):
    with db.session() as s:
        with pytest.raises(ValidationError, match="no result parameter"):
            specifications.create_spec(s, admin, code="S2", name="x", limits=[L("HPLC-ASSAY", "wt_spl", "nmt", high="1")])
        with pytest.raises(ValidationError, match="numeric limit"):
            specifications.create_spec(s, admin, code="S2", name="x", limits=[L("HPLC-ASSAY", "assay", "text", expected_text="x")])
        with pytest.raises(ValidationError, match="no current method"):
            specifications.create_spec(s, admin, code="S2", name="x", limits=[L("NOPE", "assay", "report")])
        found = specifications.suggestions_for(s, "aurumex 10 mg tablets", None)
        assert [x.code for x in found] == ["SPEC-FP-001"]


# ----- calculations ------------------------------------------------------------

def test_calculation_with_replicates_and_spec(db, setup):
    [analysis_id] = _assign(db, setup, "assay")
    with db.session() as s:
        calc = testing.save_values(s, setup["analyst"], analysis_id, ASSAY_VALUES)
    assay = calc.results[0]
    # rep 1: 10234/10120*25.12/25.30*99.8 = 100.2085…; rep 2: 10101/10120*25.12/25.05*99.8 = 99.8908…
    assert len(assay.replicate_values) == 2
    assert assay.reported == "100.0"
    assert assay.rsd is not None and assay.rsd < D("0.3")
    assert assay.spec_text == "95.0 – 105.0 %"
    assert assay.conformance is Conformance.PASS
    assert calc.complete
    with db.session() as s:
        assert s.get(Analysis, analysis_id).status == AnalysisStatus.IN_PROGRESS
        assert s.get(Sample, setup["sample"]).status == SampleStatus.IN_TESTING


def test_incomplete_and_invalid_values(db, setup):
    [analysis_id] = _assign(db, setup, "assay")
    with db.session() as s:
        calc = testing.save_values(s, setup["analyst"], analysis_id, {("wt_std", 1): "25.1"})
        assert not calc.complete and "values missing" in "".join(calc.problems())
        with pytest.raises(ValidationError, match="not a number"):
            testing.save_values(s, setup["analyst"], analysis_id, {("wt_spl", 1): "abc"})
        with pytest.raises(ValidationError, match="not an entry field"):
            testing.save_values(s, setup["analyst"], analysis_id, {("assay", 1): "100"})


def test_changing_saved_value_needs_reason(db, setup, admin):
    [analysis_id] = _assign(db, setup, "assay")
    with db.session() as s:
        testing.save_values(s, setup["analyst"], analysis_id, ASSAY_VALUES)
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            testing.save_values(s, setup["analyst"], analysis_id, {("wt_spl", 1): "25.31"})
        testing.save_values(s, setup["analyst"], analysis_id, {("wt_spl", 1): "25.31"}, reason="Transcription error")
    with db.session() as s:
        entry = audit.query_entries(s, admin, action=AuditAction.RESULTS_CHANGED)[0]
        assert "25.30" in entry.old_values and "25.31" in entry.new_values


# ----- completion & OOS --------------------------------------------------------

def test_complete_freezes_results(db, setup):
    assay_id, app_id = _assign(db, setup, "assay", "appearance")
    with db.session() as s:
        testing.save_values(s, setup["analyst"], assay_id, ASSAY_VALUES)
        testing.save_values(s, setup["analyst"], app_id, {("appearance", 1): "White round tablet"})
    with db.session() as s:
        assert not testing.complete_analysis(s, setup["analyst"], assay_id, "wrong-pass-1").ok
    with db.session() as s:
        assert testing.complete_analysis(s, setup["analyst"], assay_id, USER_PASSWORD).ok
        assert s.get(Sample, setup["sample"]).status == SampleStatus.IN_TESTING
        assert testing.complete_analysis(s, setup["analyst"], app_id, USER_PASSWORD).ok
    with db.session() as s:
        assert s.get(Sample, setup["sample"]).status == SampleStatus.TESTING_COMPLETE
        results = testing.reportable_results(s, assay_id)
        assert results[0].reported_value == "100.0" and results[0].conformance == "Pass"
        with pytest.raises(ValidationError, match="no longer be changed"):
            testing.save_values(s, setup["analyst"], assay_id, {("wt_spl", 1): "1"}, reason="x")
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE reportable_results SET reported_value = '99.0'"))


def test_oos_requires_comment_and_opens_investigation(db, setup, admin):
    [analysis_id] = _assign(db, setup, "assay")
    low = {**ASSAY_VALUES, ("area_spl", 1): "9000", ("area_spl", 2): "9050"}
    with db.session() as s:
        calc = testing.save_values(s, setup["analyst"], analysis_id, low)
        assert calc.failures and calc.failures[0].key == "assay"
    with db.session() as s:
        with pytest.raises(ValidationError, match="comment"):
            testing.complete_analysis(s, setup["analyst"], analysis_id, USER_PASSWORD)
    with db.session() as s:
        result = testing.complete_analysis(s, setup["analyst"], analysis_id, USER_PASSWORD,
                                           comments={"assay": "Low assay; standard prep checked"})
        assert result.ok
    with db.session() as s:
        [row] = oos.list_investigations(s, setup["analyst"])
        assert row.number.startswith("OOS-") and row.status == "Open"
        oos.update(s, setup["analyst"], row.id, phase=oos.PHASES[0], investigation_notes="Re-injected standard",
                   root_cause=None)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            oos.close(s, setup["analyst"], row.id, conclusion=oos.CONCLUSIONS[0], root_cause="x",
                      password=USER_PASSWORD)
        with pytest.raises(ValidationError, match="root cause"):
            oos.close(s, setup["qa"], row.id, conclusion=oos.CONCLUSIONS[0], root_cause="", password=USER_PASSWORD)
    with db.session() as s:
        assert oos.close(s, setup["qa"], row.id, conclusion=oos.CONCLUSIONS[0],
                         root_cause="Sample sub-potent; batch rejected", password=USER_PASSWORD).ok
    with db.session() as s:
        assert s.get(OOSInvestigation, row.id).status == "Closed"
        assert oos.open_count(s) == 0


def test_cancel_and_status_rules(db, setup):
    [analysis_id] = _assign(db, setup, "assay")
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            testing.cancel_analysis(s, setup["analyst"], analysis_id, "")
        testing.cancel_analysis(s, setup["analyst"], analysis_id, "Assigned by mistake")
    with db.session() as s:
        assert s.get(Sample, setup["sample"]).status == SampleStatus.RECEIVED
        assert testing.worklist(s, setup["analyst"]) == []


def test_viewer_cannot_enter_results(db, setup, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    [analysis_id] = _assign(db, setup, "assay")
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            testing.save_values(s, viewer, analysis_id, ASSAY_VALUES)
        with pytest.raises(PermissionDenied):
            testing.assign_tests(s, viewer, setup["sample"], [setup["assay"]])


def test_spec_change_needs_reason(db, setup):
    _assign(db, setup, "assay")
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            testing.set_specification(s, setup["analyst"], setup["sample"], None)
        testing.set_specification(s, setup["analyst"], setup["sample"], None, reason="Wrong spec chosen")
