"""Results below the limit of detection (ND) or quantitation (< LOQ)."""

from decimal import Decimal as D

import pytest

from aurum_lims.db.models import Sample
from aurum_lims.errors import ValidationError
from aurum_lims.services import certificates, formulas, methods, review, samples, sample_types, specifications, storage, testing
from aurum_lims.services.formulas import Conformance
from aurum_lims.services.methods import ParameterSpec as P
from aurum_lims.services.samples import SampleDetails
from aurum_lims.services.specifications import LimitSpec as L
from aurum_lims.services.testing import LimitInfo
from tests.conftest import USER_PASSWORD
from tests.test_review import signed  # noqa: F401 - fixture
from tests.test_testing import setup  # noqa: F401 - fixture

IMPURITY = [P("imp_a", "Impurity A", "result", unit="%", decimals=2, lod="0.02", loq="0.05")]
NMT_010 = {"imp_a": LimitInfo("nmt", None, "0.10", None)}


def _result(values, limits=NMT_010, params=IMPURITY):
    [r] = testing.calculate(methods.validate_parameters(params), values, limits).results
    return r


@pytest.mark.parametrize("value, reported, conformance", [
    ("0.012", "ND", Conformance.PASS),        # below the LOD
    ("0.0249", "0.02", Conformance.PASS),     # rounds to the LOD itself: detected, but below the LOQ
    ("0.03", "< 0.05", Conformance.PASS),     # between LOD and LOQ
    ("0.05", "0.05", Conformance.PASS),       # at the LOQ: reported as a number
    ("0.104", "0.10", Conformance.PASS),
    ("0.106", "0.11", Conformance.FAIL),
])
def test_reporting_against_an_impurity_limit(value, reported, conformance):
    r = _result({("imp_a", 1): value})
    expected = "< 0.05" if reported == "0.02" else reported
    assert r.reported == expected and r.conformance is conformance


def test_rounded_value_is_compared_with_the_limits():
    # 0.0496 rounds to 0.05 (= LOQ), so it is quantified.
    assert _result({("imp_a", 1): "0.0496"}).reported == "0.05"
    assert _result({("imp_a", 1): "0.0149"}).reported == "ND"


def test_below_loq_cannot_show_conformance_to_a_lower_limit():
    params = [P("assay", "Assay", "result", unit="%", decimals=1, loq="5.0")]
    r = _result({("assay", 1): "2.0"}, {"assay": LimitInfo("range", "95.0", "105.0", None)}, params)
    assert r.reported == "< 5.0" and r.conformance is Conformance.FAIL
    r = _result({("assay", 1): "2.0"}, {"assay": LimitInfo("report", None, None, None)}, params)
    assert r.conformance is Conformance.REPORT


def test_method_loq_above_the_limit_does_not_conform():
    """A method whose LOQ is above the limit cannot show the sample passes."""
    params = [P("imp_a", "Impurity A", "result", unit="%", decimals=2, loq="0.20")]
    assert _result({("imp_a", 1): "0.15"}, params=params).conformance is Conformance.FAIL


def test_check_below_rules():
    check = formulas.check_below
    assert check("nmt", None, "0.10", D("0.05")) is Conformance.PASS
    assert check("lt", None, "0.10", D("0.10")) is Conformance.PASS  # value < 0.10
    assert check("range", "0", "0.5", D("0.05")) is Conformance.PASS
    assert check("nlt", "0", None, D("0.05")) is Conformance.PASS
    assert check("gt", "0", None, D("0.05")) is Conformance.FAIL
    assert check("text", None, None, D("1")) is Conformance.FAIL


def test_with_unit():
    assert formulas.with_unit("ND", "%") == "ND"
    assert formulas.with_unit("< 0.05", "%") == "< 0.05 %"
    assert formulas.with_unit("0.08", None) == "0.08"


def test_validation():
    with pytest.raises(ValidationError, match="only number results"):
        methods.validate_parameters([P("wt", "Weight", "input", lod="1"), P("r", "R", "result")])
    with pytest.raises(ValidationError, match="only number results"):
        methods.validate_parameters([P("app", "Appearance", "result", result_type="text", loq="1")])
    with pytest.raises(ValidationError, match="above the LOQ"):
        methods.validate_parameters([P("imp", "Imp", "result", lod="0.1", loq="0.05")])
    with pytest.raises(ValidationError, match="greater than zero"):
        methods.validate_parameters([P("imp", "Imp", "result", loq="0")])
    with pytest.raises(ValidationError, match="not a number"):
        methods.validate_parameters([P("imp", "Imp", "result", lod="abc")])
    [p] = methods.validate_parameters([P("imp", "Imp", "result", lod=" 0.02 ", loq="")])
    assert p.lod == "0.02" and p.loq is None


def test_nd_result_end_to_end(db, signed, admin):  # noqa: F811
    """Saved on the method, carried into new versions, frozen with the result and explained on the CoA."""
    with db.session() as s:
        method_id = methods.create_method(s, admin, code="IMP", name="Related substances", parameters=IMPURITY).id
    with db.session() as s:
        assert methods.activate(s, signed["qa"], method_id, USER_PASSWORD).ok
    with db.session() as s:
        draft = methods.new_version(s, admin, method_id)
        [copied] = [methods.to_spec(p) for p in methods.parameters_of(s, draft.id)]
        assert (copied.lod, copied.loq) == ("0.02", "0.05")
        spec = specifications.create_spec(s, admin, code="SPEC-IMP", name="Impurities", material_name="Aurumex",
                                          limits=[L("IMP", "imp_a", "nmt", high="0.10")]).id
    with db.session() as s:
        assert specifications.activate(s, signed["qa"], spec, USER_PASSWORD).ok
    with db.session() as s:
        fp = next(t.id for t in sample_types.list_types(s) if t.code == "FP")
        shelf = storage.create_location(s, admin, name="Shelf 2", kind="Shelf").id
        sample_pk = samples.register_samples(s, signed["analyst"], fp,
                                             SampleDetails(material_name="Aurumex", lot_number="B2"),
                                             receipt=samples.Receipt(location_id=shelf))[0].id
    with db.session() as s:
        testing.set_specification(s, signed["analyst"], sample_pk, spec)
        [test] = [a.id for a in testing.assign_tests(s, signed["analyst"], sample_pk, [method_id])]
        testing.save_values(s, signed["analyst"], test, {("imp_a", 1): "0.004"})
    with db.session() as s:
        assert testing.complete_analysis(s, signed["analyst"], test, USER_PASSWORD).ok
    with db.session() as s:
        [res] = testing.reportable_results(s, test)
        assert (res.reported_value, res.conformance, res.lod, res.loq) == ("ND", "Pass", "0.02", "0.05")
    with db.session() as s:
        assert review.accept_test(s, signed["qa"], test, USER_PASSWORD).ok
    with db.session() as s:
        assert review.decide_sample(s, signed["qa"], sample_pk, approve=True, password=USER_PASSWORD).ok
    with db.session() as s:
        sample = s.get(Sample, sample_pk)
        page = certificates.build_html(s, sample, review.decision_for(s, sample.id), "CoA-TEST-0002",
                                       certificates.COA)
    assert "<b>ND</b>" in page and "ND %" not in page
    assert "below the limit of detection (0.02 %)" in page
