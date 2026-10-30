"""Analytical test methods and their parameters (inputs, results, formulas)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import MethodParameter, MethodRequirement, TestMethod, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, esign, formulas
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult
from aurum_lims.services.versioning import VersionStatus

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_.]{0,39}$")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED_KEYS = set(formulas.FUNCTIONS) | set(formulas.VARIADIC)
MAX_REPLICATES = 20


class ParameterKind(StrEnum):
    INPUT = "input"
    RESULT = "result"

    @property
    def label(self) -> str:
        return {"input": "Input (not reported)", "result": "Result (reported)"}[self.value]


class ResultType(StrEnum):
    NUMERIC = "numeric"
    TEXT = "text"
    PASS_FAIL = "pass_fail"

    @property
    def label(self) -> str:
        return {"numeric": "Number", "text": "Text", "pass_fail": "Pass / Fail"}[self.value]


@dataclass
class ParameterSpec:
    """A parameter as edited in the method editor."""

    key: str
    name: str
    kind: str = ParameterKind.RESULT
    result_type: str = ResultType.NUMERIC
    unit: str | None = None
    decimals: int | None = None
    replicates: int = 1
    formula: str | None = None
    lod: str | None = None
    loq: str | None = None

    @property
    def calculated(self) -> bool:
        return bool(self.formula)


class RequirementKind(StrEnum):
    INSTRUMENT = "instrument"
    MATERIAL = "material"


@dataclass
class RequirementSpec:
    """Equipment or material a method needs. ``category`` is an instrument
    category (e.g. HPLC) or an inventory item kind (e.g. Reference standard).
    A reference-standard requirement may fill a method input with the lot's purity."""

    kind: str
    category: str
    label: str = ""
    fills_parameter: str | None = None


def to_requirement(requirement: MethodRequirement) -> RequirementSpec:
    return RequirementSpec(requirement.kind, requirement.category, requirement.label, requirement.fills_parameter)


def validate_requirements(requirements: list[RequirementSpec], parameters: list[ParameterSpec]) -> list[RequirementSpec]:
    from aurum_lims.services.instruments import CATEGORIES
    from aurum_lims.services.inventory import ItemKind

    params = {p.key: p for p in parameters}
    cleaned, filled = [], set()
    for index, r in enumerate(requirements, start=1):
        kind = RequirementKind(r.kind)
        where = f"Requirement {index}"
        if kind is RequirementKind.INSTRUMENT and r.category not in CATEGORIES:
            raise ValidationError(f"{where}: choose an instrument category.")
        if kind is RequirementKind.MATERIAL and r.category not in {k.value for k in ItemKind}:
            raise ValidationError(f"{where}: choose a material type.")
        label = " ".join((r.label or "").split()) or r.category
        fills = (r.fills_parameter or "").strip() or None
        if fills:
            if kind is not RequirementKind.MATERIAL or r.category != ItemKind.REFERENCE_STANDARD:
                raise ValidationError(f"{where}: only a reference standard can fill a parameter with its purity.")
            p = params.get(fills)
            if p is None or p.kind != ParameterKind.INPUT or p.result_type != ResultType.NUMERIC or p.calculated \
                    or p.replicates != 1:
                raise ValidationError(f"{where}: '{fills}' must be a number input with 1 replicate.")
            if fills in filled:
                raise ValidationError(f"{where}: '{fills}' is already filled by another requirement.")
            filled.add(fills)
        cleaned.append(RequirementSpec(kind.value, r.category, label[:100], fills))
    return cleaned


def requirements_of(session: Session, method_id: int) -> list[MethodRequirement]:
    return list(session.scalars(select(MethodRequirement).where(MethodRequirement.method_id == method_id)
                                .order_by(MethodRequirement.position)))


def _save_requirements(session: Session, method: TestMethod, requirements: list[RequirementSpec]) -> None:
    for existing in requirements_of(session, method.id):
        session.delete(existing)
    session.flush()
    for position, r in enumerate(requirements):
        session.add(MethodRequirement(method_id=method.id, position=position, **asdict(r)))
    session.flush()


def to_spec(parameter: MethodParameter) -> ParameterSpec:
    return ParameterSpec(
        key=parameter.key, name=parameter.name, kind=parameter.kind, result_type=parameter.result_type,
        unit=parameter.unit, decimals=parameter.decimals, replicates=parameter.replicates, formula=parameter.formula,
        lod=parameter.lod, loq=parameter.loq,
    )


def calculated_replicates(spec: ParameterSpec, earlier: dict[str, ParameterSpec]) -> int:
    """How many replicate values a calculated parameter produces."""
    counts = {calculated_replicates(earlier[n], earlier) if earlier[n].calculated else earlier[n].replicates
              for n in formulas.names_in(spec.formula) if n in earlier}
    multi = {c for c in counts if c > 1}
    return multi.pop() if len(multi) == 1 else 1


def validate_parameters(parameters: list[ParameterSpec]) -> list[ParameterSpec]:
    """Clean and check a method's parameter list; raise ``ValidationError`` with
    a message that names the offending parameter."""
    if not parameters:
        raise ValidationError("A method needs at least one parameter.")
    cleaned: list[ParameterSpec] = []
    earlier: dict[str, ParameterSpec] = {}
    for index, p in enumerate(parameters, start=1):
        key = (p.key or "").strip().lower()
        name = " ".join((p.name or "").split())
        where = f"Parameter {index} ({name or key or 'unnamed'})"
        if not KEY_PATTERN.match(key):
            raise ValidationError(f"{where}: the key must start with a letter and use only lower-case letters, "
                                  "numbers and _ (e.g. wt_sample).")
        if key in RESERVED_KEYS:
            raise ValidationError(f"{where}: '{key}' is a function name and cannot be used as a key.")
        if key in earlier:
            raise ValidationError(f"{where}: the key '{key}' is used twice.")
        if not name:
            raise ValidationError(f"{where}: a name is required.")
        kind = ParameterKind(p.kind)
        result_type = ResultType(p.result_type)
        formula = (p.formula or "").strip() or None
        unit = (p.unit or "").strip() or None
        decimals = p.decimals
        replicates = int(p.replicates or 1)
        if kind is ParameterKind.INPUT and result_type is not ResultType.NUMERIC:
            raise ValidationError(f"{where}: inputs must be numbers.")
        if result_type is not ResultType.NUMERIC:
            if formula:
                raise ValidationError(f"{where}: only number results can have a formula.")
            decimals, unit, replicates = None, None, 1
        if decimals is not None and not 0 <= int(decimals) <= 10:
            raise ValidationError(f"{where}: decimals must be between 0 and 10.")
        if not 1 <= replicates <= MAX_REPLICATES:
            raise ValidationError(f"{where}: replicates must be between 1 and {MAX_REPLICATES}.")
        if formula:
            try:
                used = formulas.names_in(formula)
            except formulas.FormulaError as exc:
                raise ValidationError(f"{where}: {exc}") from None
            unknown = used - set(earlier)
            if unknown:
                raise ValidationError(
                    f"{where}: the formula uses {', '.join(sorted(unknown))}, which must be defined above it.")
            not_numeric = [n for n in used if earlier[n].result_type != ResultType.NUMERIC]
            if not_numeric:
                raise ValidationError(f"{where}: the formula can only use number parameters.")
            counts = {calculated_replicates(earlier[n], earlier) if earlier[n].calculated else earlier[n].replicates
                      for n in used}
            if len({c for c in counts if c > 1}) > 1:
                raise ValidationError(f"{where}: the formula mixes parameters with different numbers of "
                                      "replicates. Shared values (1 replicate) can be mixed with one replicate count.")
        lod, loq = (p.lod or "").strip() or None, (p.loq or "").strip() or None
        if lod or loq:
            if kind is not ParameterKind.RESULT or result_type is not ResultType.NUMERIC:
                raise ValidationError(f"{where}: only number results can have an LOD or LOQ.")
            try:
                lod_value, loq_value = formulas.parse_decimal(lod), formulas.parse_decimal(loq)
            except ValidationError as exc:
                raise ValidationError(f"{where}: {exc}") from None
            if any(v is not None and v <= 0 for v in (lod_value, loq_value)):
                raise ValidationError(f"{where}: the LOD and LOQ must be greater than zero.")
            if lod_value is not None and loq_value is not None and lod_value > loq_value:
                raise ValidationError(f"{where}: the LOD cannot be above the LOQ.")
        spec = ParameterSpec(key, name, kind.value, result_type.value, unit,
                             None if decimals is None else int(decimals), replicates, formula, lod, loq)
        if formula:
            spec.replicates = calculated_replicates(spec, earlier)
        cleaned.append(spec)
        earlier[key] = spec
    if not any(p.kind == ParameterKind.RESULT for p in cleaned):
        raise ValidationError("A method needs at least one result parameter.")
    return cleaned


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


def get_method(session: Session, method_id: int) -> TestMethod:
    method = session.get(TestMethod, method_id)
    if method is None:
        raise NotFound("Method not found.")
    return method


def parameters_of(session: Session, method_id: int) -> list[MethodParameter]:
    return list(session.scalars(
        select(MethodParameter).where(MethodParameter.method_id == method_id).order_by(MethodParameter.position)
    ))


def list_methods(session: Session, include_retired: bool = False) -> list[TestMethod]:
    stmt = select(TestMethod).order_by(TestMethod.code, TestMethod.version.desc())
    if not include_retired:
        stmt = stmt.where(TestMethod.status != VersionStatus.RETIRED)
    return list(session.scalars(stmt))


def active_methods(session: Session) -> list[TestMethod]:
    return list(session.scalars(
        select(TestMethod).where(TestMethod.status == VersionStatus.ACTIVE).order_by(TestMethod.code)
    ))


def active_version(session: Session, code: str) -> TestMethod | None:
    return session.scalar(select(TestMethod).where(TestMethod.code == code, TestMethod.status == VersionStatus.ACTIVE))


def describe(method: TestMethod, parameters: list[ParameterSpec],
             requirements: list[RequirementSpec] | None = None) -> dict:
    return {
        "code": method.code, "name": method.name, "version": method.version, "status": method.status,
        "sop_reference": method.sop_reference, "description": method.description,
        "parameters": [{k: v for k, v in asdict(p).items() if v not in (None, "")} for p in parameters],
        "requirements": [{k: v for k, v in asdict(r).items() if v not in (None, "")} for r in requirements or []],
    }


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------


def _clean_header(code: str, name: str, sop_reference: str | None, description: str | None):
    code = (code or "").strip().upper()
    name = " ".join((name or "").split())
    if not CODE_PATTERN.match(code):
        raise ValidationError("Method code must be 1-40 capital letters, numbers, - _ or . (e.g. HPLC-ASSAY-01).")
    if not name:
        raise ValidationError("Method name is required.")
    return code, name, (sop_reference or "").strip() or None, (description or "").strip() or None


def _save_parameters(session: Session, method: TestMethod, parameters: list[ParameterSpec]) -> None:
    for existing in parameters_of(session, method.id):
        session.delete(existing)
    session.flush()
    for position, p in enumerate(parameters):
        session.add(MethodParameter(method_id=method.id, position=position, **asdict(p)))
    session.flush()


def create_method(
    session: Session, actor: SessionUser, *, code: str, name: str, parameters: list[ParameterSpec],
    sop_reference: str | None = None, description: str | None = None,
    requirements: list[RequirementSpec] | None = None,
) -> TestMethod:
    require(actor, Permission.MANAGE_MASTER_DATA)
    code, name, sop_reference, description = _clean_header(code, name, sop_reference, description)
    if session.scalar(select(TestMethod.id).where(TestMethod.code == code)) is not None:
        raise ValidationError(f"A method with code {code} already exists. Create a new version of it instead.")
    parameters = validate_parameters(parameters)
    requirements = validate_requirements(requirements or [], parameters)
    method = TestMethod(code=code, name=name, version=1, status=VersionStatus.DRAFT, sop_reference=sop_reference,
                        description=description, created_by_id=actor.id)
    session.add(method)
    session.flush()
    _save_parameters(session, method, parameters)
    _save_requirements(session, method, requirements)
    audit.record(session, AuditAction.METHOD_CREATED, actor=actor, entity_type="method",
                 entity_id=f"{code} v{method.version}", new=describe(method, parameters, requirements))
    return method


def update_draft(
    session: Session, actor: SessionUser, method_id: int, *, name: str, parameters: list[ParameterSpec],
    sop_reference: str | None = None, description: str | None = None,
    requirements: list[RequirementSpec] | None = None,
) -> TestMethod:
    require(actor, Permission.MANAGE_MASTER_DATA)
    method = get_method(session, method_id)
    if method.status != VersionStatus.DRAFT:
        raise ValidationError("Only draft methods can be edited. Create a new version to change an active method.")
    _, name, sop_reference, description = _clean_header(method.code, name, sop_reference, description)
    parameters = validate_parameters(parameters)
    requirements = validate_requirements(requirements or [], parameters)
    old = describe(method, [to_spec(p) for p in parameters_of(session, method.id)],
                   [to_requirement(r) for r in requirements_of(session, method.id)])
    method.name, method.sop_reference, method.description = name, sop_reference, description
    _save_parameters(session, method, parameters)
    _save_requirements(session, method, requirements)
    new = describe(method, parameters, requirements)
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    audit.record(session, AuditAction.METHOD_UPDATED, actor=actor, entity_type="method",
                 entity_id=f"{method.code} v{method.version}", old=changed, new={k: new[k] for k in changed})
    return method


def new_version(session: Session, actor: SessionUser, method_id: int) -> TestMethod:
    """Create a draft copy of a method as the next version number."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    source = get_method(session, method_id)
    if session.scalar(select(TestMethod.id).where(TestMethod.code == source.code,
                                                  TestMethod.status == VersionStatus.DRAFT)) is not None:
        raise ValidationError(f"{source.code} already has a draft version. Edit that draft instead.")
    latest = session.scalar(select(func.max(TestMethod.version)).where(TestMethod.code == source.code))
    method = TestMethod(code=source.code, name=source.name, version=latest + 1, status=VersionStatus.DRAFT,
                        sop_reference=source.sop_reference, description=source.description, created_by_id=actor.id)
    session.add(method)
    session.flush()
    parameters = [to_spec(p) for p in parameters_of(session, source.id)]
    _save_parameters(session, method, parameters)
    _save_requirements(session, method, [to_requirement(r) for r in requirements_of(session, source.id)])
    audit.record(session, AuditAction.METHOD_CREATED, actor=actor, entity_type="method",
                 entity_id=f"{method.code} v{method.version}", new={"copied_from": f"v{source.version}"})
    return method


def activate(session: Session, actor: SessionUser, method_id: int, password: str) -> SignatureResult:
    """Approve a draft for use (QA electronic signature). The previously active
    version of the same method is retired."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    method = get_method(session, method_id)
    if method.status != VersionStatus.DRAFT:
        raise ValidationError("Only a draft can be activated.")
    params = validate_parameters([to_spec(p) for p in parameters_of(session, method.id)])
    validate_requirements([to_requirement(r) for r in requirements_of(session, method.id)], params)
    label = f"{method.code} v{method.version}"
    result = esign.sign(session, actor, password, SignatureMeaning.APPROVED, "method", label,
                        reason="Method version approved for use")
    if not result.ok:
        return result
    previous = active_version(session, method.code)
    now = utcnow()
    if previous is not None:
        previous.status = VersionStatus.RETIRED
        previous.retired_at = now
        audit.record(session, AuditAction.METHOD_RETIRED, actor=actor, entity_type="method",
                     entity_id=f"{previous.code} v{previous.version}", reason=f"Superseded by v{method.version}")
    method.status = VersionStatus.ACTIVE
    method.activated_at = now
    method.activated_by_id = actor.id
    audit.record(session, AuditAction.METHOD_ACTIVATED, actor=actor, entity_type="method", entity_id=label)
    session.flush()
    return result


def retire(session: Session, actor: SessionUser, method_id: int, reason: str) -> TestMethod:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    method = get_method(session, method_id)
    if method.status == VersionStatus.RETIRED:
        raise ValidationError("This version is already retired.")
    method.status = VersionStatus.RETIRED
    method.retired_at = utcnow()
    audit.record(session, AuditAction.METHOD_RETIRED, actor=actor, entity_type="method",
                 entity_id=f"{method.code} v{method.version}", reason=reason.strip())
    return method
