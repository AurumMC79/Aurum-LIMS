from decimal import Decimal as D

import pytest

from aurum_lims.errors import ValidationError
from aurum_lims.services import formulas
from aurum_lims.services.formulas import Conformance, FormulaError, LimitType


def test_assay_formula():
    value = formulas.evaluate(
        "area_spl / area_std * wt_std / wt_spl * purity_std",
        {"area_spl": D("10234"), "area_std": D("10120"), "wt_std": D("25.12"), "wt_spl": D("25.30"),
         "purity_std": D("99.8")},
    )
    assert formulas.usp_round(value, 1) == D("100.2")


def test_operators_and_functions():
    assert formulas.evaluate("2^3 + sqrt(16) - abs(-1)", {}) == D(11)
    assert formulas.evaluate("max(a, b, 3) * -1", {"a": D(1), "b": D(5)}) == D(-5)
    assert formulas.evaluate("log10(1000)", {}) == D(3)


@pytest.mark.parametrize("formula", [
    "__import__('os').system('x')", "a.b", "a if b else c", "[1, 2]", "open('f')", "a == b", "'text'",
])
def test_unsafe_or_invalid_formulas_rejected(formula):
    with pytest.raises(FormulaError):
        formulas.evaluate(formula, {"a": D(1), "b": D(2), "c": D(3)})


def test_runtime_errors():
    with pytest.raises(FormulaError, match="zero"):
        formulas.evaluate("a / b", {"a": D(1), "b": D(0)})
    with pytest.raises(FormulaError, match="Unknown parameter"):
        formulas.evaluate("a + missing", {"a": D(1)})
    with pytest.raises(FormulaError, match="not possible"):
        formulas.evaluate("sqrt(a)", {"a": D(-4)})


def test_names_in():
    assert formulas.names_in("sqrt(a) * b_2 / 100") == {"a", "b_2"}


def test_usp_rounding_half_up():
    # Binary floats would round 2.675 down to 2.67; USP rounding gives 2.68.
    assert formulas.usp_round(D("2.675"), 2) == D("2.68")
    assert formulas.usp_round(D("99.45"), 1) == D("99.5")
    assert formulas.usp_round(D("-0.05"), 1) == D("-0.1")
    assert formulas.format_decimal(D("100"), 1) == "100.0"


def test_mean_and_rsd():
    values = [D("99.8"), D("100.2"), D("100.0")]
    assert formulas.mean(values) == D("100")
    assert formulas.usp_round(formulas.rsd_percent(values), 2) == D("0.20")
    assert formulas.rsd_percent([D(1)]) is None


def test_parse_decimal():
    assert formulas.parse_decimal(" 12.50 ") == D("12.50")
    assert formulas.parse_decimal("") is None
    with pytest.raises(ValidationError):
        formulas.parse_decimal("1,5")


def test_limit_checks_use_rounded_value():
    # 94.95 rounds to 95.0 → passes a 95.0–105.0 limit (USP 7.20).
    reported = formulas.usp_round(D("94.95"), 1)
    assert formulas.check(LimitType.RANGE, "95.0", "105.0", None, reported) is Conformance.PASS
    assert formulas.check(LimitType.RANGE, "95.0", "105.0", None, formulas.usp_round(D("94.94"), 1)) is Conformance.FAIL
    assert formulas.check(LimitType.NMT, None, "0.5", None, D("0.5")) is Conformance.PASS
    assert formulas.check(LimitType.LT, None, "0.5", None, D("0.5")) is Conformance.FAIL
    assert formulas.check(LimitType.TEXT, None, None, "Conforms", "conforms") is Conformance.PASS
    assert formulas.check(LimitType.PASS, None, None, None, "Fail") is Conformance.FAIL
    assert formulas.check(LimitType.REPORT, None, None, None, "anything") is Conformance.REPORT


def test_limit_problems_and_text():
    assert formulas.limit_problems(LimitType.RANGE, "105", "95", None)
    assert formulas.limit_problems(LimitType.NMT, None, None, None)
    assert formulas.limit_problems(LimitType.RANGE, "95.0", "105.0", None) == []
    assert formulas.limit_text(LimitType.RANGE, "95.0", "105.0", None, "%") == "95.0 – 105.0 %"
    assert formulas.limit_text(LimitType.NMT, None, "0.5", None, "%") == "NMT 0.5 %"
