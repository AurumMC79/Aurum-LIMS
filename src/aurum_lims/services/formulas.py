"""Exact decimal arithmetic for method formulas, USP-style rounding, and
comparison of results with specification limits.

All numbers are ``Decimal`` so results such as 2.675 round the way a chemist
expects (to 2.68), rather than suffering binary floating-point error.

Formulas use parameter keys as variables, e.g.::

    area_spl / area_std * wt_std / wt_spl * purity_std

Allowed: numbers, + - * / ** ( ), and the functions sqrt, ln, log10, exp,
abs, min, max. Nothing else is evaluated, so a formula can never run code.
"""

from __future__ import annotations

import ast
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation, localcontext
from enum import StrEnum

from aurum_lims.errors import ValidationError

PRECISION = 28

FUNCTIONS = {
    "sqrt": lambda x: x.sqrt(),
    "ln": lambda x: x.ln(),
    "log10": lambda x: x.log10(),
    "exp": lambda x: x.exp(),
    "abs": lambda x: abs(x),
}
VARIADIC = {"min": min, "max": max}


class FormulaError(ValidationError):
    pass


def parse_decimal(text: str | None) -> Decimal | None:
    """Parse a number as typed by a user; ``None`` for blank. Commas are not
    accepted as decimal separators to avoid 1,234 / 1.234 ambiguity."""
    if text is None or not str(text).strip():
        return None
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise ValidationError(f"'{text}' is not a number.") from None
    if not value.is_finite():
        raise ValidationError(f"'{text}' is not a number.")
    return value


def names_in(formula: str) -> set[str]:
    """Variable names used by a formula (raises ``FormulaError`` if it is invalid)."""
    tree = _parse(formula)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id not in FUNCTIONS | VARIADIC}


def _parse(formula: str) -> ast.Expression:
    if not formula or not formula.strip():
        raise FormulaError("The formula is empty.")
    try:
        tree = ast.parse(formula.strip().replace("^", "**"), mode="eval")
    except SyntaxError:
        raise FormulaError("The formula is not valid. Check brackets and operators.") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load,
                             ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS | VARIADIC or node.keywords:
                raise FormulaError("Only these functions can be used: " + ", ".join(sorted(FUNCTIONS | VARIADIC)) + ".")
            if node.func.id in FUNCTIONS and len(node.args) != 1:
                raise FormulaError(f"{node.func.id}() takes exactly one value.")
            if node.func.id in VARIADIC and len(node.args) < 2:
                raise FormulaError(f"{node.func.id}() needs at least two values.")
            continue
        raise FormulaError("The formula contains something that is not allowed. Use numbers, parameter keys, "
                           "+ - * / ^ and brackets.")
    return tree


def evaluate(formula: str, variables: dict[str, Decimal]) -> Decimal:
    """Evaluate ``formula`` with ``variables``. Raises ``FormulaError`` for
    unknown names, division by zero or impossible maths (e.g. sqrt of -1)."""
    tree = _parse(formula)

    def ev(node) -> Decimal:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise FormulaError(f"Unknown parameter '{node.id}' in formula.")
            return variables[node.id]
        if isinstance(node, ast.UnaryOp):
            value = ev(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise FormulaError("Division by zero.")
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
        if isinstance(node, ast.Call):
            args = [ev(arg) for arg in node.args]
            if node.func.id in VARIADIC:
                return VARIADIC[node.func.id](args)
            return FUNCTIONS[node.func.id](args[0])
        raise FormulaError("Unsupported formula.")  # pragma: no cover - _parse already rejects this

    with localcontext() as ctx:
        ctx.prec = PRECISION
        try:
            result = ev(tree)
        except (InvalidOperation, DivisionByZero, OverflowError):
            raise FormulaError("The calculation is not possible with these values (e.g. square root or "
                               "logarithm of a negative number).") from None
    if not result.is_finite():
        raise FormulaError("The calculation result is not a finite number.")
    return result


def usp_round(value: Decimal, decimals: int | None) -> Decimal:
    """Round half up to ``decimals`` places (USP General Notices 7.20)."""
    if decimals is None:
        return value
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def mean(values: list[Decimal]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return sum(values, Decimal(0)) / len(values)


def rsd_percent(values: list[Decimal]) -> Decimal | None:
    """Relative standard deviation (sample, n-1) in %; ``None`` if not defined."""
    if len(values) < 2:
        return None
    with localcontext() as ctx:
        ctx.prec = PRECISION
        m = mean(values)
        if m == 0:
            return None
        variance = sum(((v - m) ** 2 for v in values), Decimal(0)) / (len(values) - 1)
        return abs(variance.sqrt() / m * 100)


def format_decimal(value: Decimal | None, decimals: int | None = None) -> str:
    """Display a Decimal without exponent notation."""
    if value is None:
        return ""
    if decimals is not None:
        value = usp_round(value, decimals)
        return f"{value:.{decimals}f}"
    text = format(value.normalize(), "f")
    return text


# ---------------------------------------------------------------------------
# specification limits
# ---------------------------------------------------------------------------


class LimitType(StrEnum):
    RANGE = "range"
    NLT = "nlt"
    NMT = "nmt"
    LT = "lt"
    GT = "gt"
    TEXT = "text"
    PASS = "pass"
    REPORT = "report"

    @property
    def label(self) -> str:
        return LIMIT_LABELS[self]


LIMIT_LABELS = {
    LimitType.RANGE: "Between (inclusive)",
    LimitType.NLT: "Not less than (≥)",
    LimitType.NMT: "Not more than (≤)",
    LimitType.LT: "Less than (<)",
    LimitType.GT: "Greater than (>)",
    LimitType.TEXT: "Text must match",
    LimitType.PASS: "Must be Pass",
    LimitType.REPORT: "Report only",
}


class Conformance(StrEnum):
    PASS = "Pass"
    FAIL = "Fail"
    REPORT = "Report"


def limit_text(limit_type: LimitType | str, low: str | None, high: str | None, expected: str | None,
               unit: str | None = None) -> str:
    limit_type = LimitType(limit_type)
    u = f" {unit}" if unit else ""
    return {
        LimitType.RANGE: f"{low} – {high}{u}",
        LimitType.NLT: f"NLT {low}{u}",
        LimitType.NMT: f"NMT {high}{u}",
        LimitType.LT: f"< {high}{u}",
        LimitType.GT: f"> {low}{u}",
        LimitType.TEXT: expected or "",
        LimitType.PASS: "Pass",
        LimitType.REPORT: "Report result",
    }[limit_type]


NOT_DETECTED = "ND"


def detection_text(reported: Decimal, lod: str | None, loq: str | None) -> tuple[str, Decimal] | None:
    """For a rounded result below the method's LOD or LOQ: the text to report
    ("ND" or "< LOQ value") and the bound the true value lies under."""
    lod_value, loq_value = parse_decimal(lod), parse_decimal(loq)
    if lod_value is not None and reported < lod_value:
        return NOT_DETECTED, lod_value
    if loq_value is not None and reported < loq_value:
        return f"< {loq}", loq_value
    return None


def with_unit(reported: str | None, unit: str | None) -> str:
    """A reported result with its unit ("ND" stands alone)."""
    reported = reported or ""
    if not unit or reported == NOT_DETECTED or not reported:
        return reported
    return f"{reported} {unit}"


def limit_problems(limit_type: LimitType | str, low: str | None, high: str | None, expected: str | None) -> list[str]:
    limit_type = LimitType(limit_type)
    problems = []
    try:
        lo, hi = parse_decimal(low), parse_decimal(high)
    except ValidationError as exc:
        return [str(exc)]
    if limit_type in (LimitType.RANGE, LimitType.NLT, LimitType.GT) and lo is None:
        problems.append("A lower limit is required.")
    if limit_type in (LimitType.RANGE, LimitType.NMT, LimitType.LT) and hi is None:
        problems.append("An upper limit is required.")
    if limit_type is LimitType.RANGE and lo is not None and hi is not None and lo > hi:
        problems.append("The lower limit is above the upper limit.")
    if limit_type is LimitType.TEXT and not (expected or "").strip():
        problems.append("Enter the expected text, e.g. Conforms.")
    return problems


def check(limit_type: LimitType | str, low: str | None, high: str | None, expected: str | None,
          reported: Decimal | str | None) -> Conformance:
    """Compare a reported (already rounded) result with a limit."""
    limit_type = LimitType(limit_type)
    if limit_type is LimitType.REPORT:
        return Conformance.REPORT
    if limit_type is LimitType.TEXT:
        return Conformance.PASS if str(reported or "").strip().lower() == (expected or "").strip().lower() else Conformance.FAIL
    if limit_type is LimitType.PASS:
        return Conformance.PASS if str(reported or "").strip().lower() == "pass" else Conformance.FAIL
    value = reported if isinstance(reported, Decimal) else parse_decimal(reported)
    if value is None:
        return Conformance.FAIL
    lo, hi = parse_decimal(low), parse_decimal(high)
    ok = {
        LimitType.RANGE: lo is not None and hi is not None and lo <= value <= hi,
        LimitType.NLT: lo is not None and value >= lo,
        LimitType.NMT: hi is not None and value <= hi,
        LimitType.LT: hi is not None and value < hi,
        LimitType.GT: lo is not None and value > lo,
    }[limit_type]
    return Conformance.PASS if ok else Conformance.FAIL


def check_below(limit_type: LimitType | str, low: str | None, high: str | None, bound: Decimal) -> Conformance:
    """Conformance of a result reported as ND or "< LOQ": the true value lies
    somewhere from 0 up to (not including) ``bound``, so it conforms only if every
    value in that range would. Otherwise conformance cannot be shown and the
    result is treated as not conforming (for example an assay below its LOQ, or
    a method whose LOQ is above the impurity limit)."""
    limit_type = LimitType(limit_type)
    if limit_type is LimitType.REPORT:
        return Conformance.REPORT
    lo, hi = parse_decimal(low), parse_decimal(high)
    ok = {
        LimitType.RANGE: lo is not None and hi is not None and lo <= 0 and bound <= hi,
        LimitType.NLT: lo is not None and lo <= 0,
        LimitType.NMT: hi is not None and bound <= hi,
        LimitType.LT: hi is not None and bound <= hi,
        LimitType.GT: False,
    }.get(limit_type, False)
    return Conformance.PASS if ok else Conformance.FAIL
