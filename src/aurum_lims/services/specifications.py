"""Specifications: the limits a material or product must meet, by test."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import MethodParameter, SampleType, SpecLimit, Specification, TestMethod, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, esign, formulas
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.esign import SignatureMeaning, SignatureResult
from aurum_lims.services.formulas import LimitType
from aurum_lims.services.methods import ParameterKind
from aurum_lims.services.versioning import VersionStatus

CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_./]{0,39}$")


@dataclass
class LimitSpec:
    method_code: str
    parameter_key: str
    limit_type: str
    low: str | None = None
    high: str | None = None
    expected_text: str | None = None


def to_limit(limit: SpecLimit) -> LimitSpec:
    return LimitSpec(limit.method_code, limit.parameter_key, limit.limit_type, limit.low, limit.high,
                     limit.expected_text)


def result_parameters(session: Session, method_code: str) -> list[MethodParameter]:
    """Result parameters of the newest non-retired version of a method (what a limit can point at)."""
    method = session.scalar(
        select(TestMethod).where(TestMethod.code == method_code, TestMethod.status != VersionStatus.RETIRED)
        .order_by(TestMethod.version.desc()).limit(1)
    )
    if method is None:
        return []
    return list(session.scalars(
        select(MethodParameter)
        .where(MethodParameter.method_id == method.id, MethodParameter.kind == ParameterKind.RESULT)
        .order_by(MethodParameter.position)
    ))


def validate_limits(session: Session, limits: list[LimitSpec]) -> list[LimitSpec]:
    if not limits:
        raise ValidationError("A specification needs at least one limit.")
    cleaned, seen = [], set()
    for index, limit in enumerate(limits, start=1):
        code = (limit.method_code or "").strip().upper()
        key = (limit.parameter_key or "").strip().lower()
        params = {p.key: p for p in result_parameters(session, code)}
        where = f"Limit {index} ({code} / {key or '?'})"
        if not params:
            raise ValidationError(f"{where}: there is no current method with code {code}.")
        if key not in params:
            raise ValidationError(f"{where}: {code} has no result parameter '{key}'.")
        if (code, key) in seen:
            raise ValidationError(f"{where}: this result already has a limit.")
        seen.add((code, key))
        limit_type = LimitType(limit.limit_type)
        result_type = params[key].result_type
        if result_type == "numeric" and limit_type in (LimitType.TEXT, LimitType.PASS):
            raise ValidationError(f"{where}: a number result needs a numeric limit (or Report only).")
        if result_type == "text" and limit_type not in (LimitType.TEXT, LimitType.REPORT):
            raise ValidationError(f"{where}: a text result needs 'Text must match' or 'Report only'.")
        if result_type == "pass_fail" and limit_type not in (LimitType.PASS, LimitType.REPORT):
            raise ValidationError(f"{where}: a Pass/Fail result needs 'Must be Pass' or 'Report only'.")
        low = (limit.low or "").strip() or None
        high = (limit.high or "").strip() or None
        expected = (limit.expected_text or "").strip() or None
        if limit_type not in (LimitType.RANGE, LimitType.NLT, LimitType.GT):
            low = None
        if limit_type not in (LimitType.RANGE, LimitType.NMT, LimitType.LT):
            high = None
        if limit_type is not LimitType.TEXT:
            expected = None
        problems = formulas.limit_problems(limit_type, low, high, expected)
        if problems:
            raise ValidationError(f"{where}: " + " ".join(problems))
        cleaned.append(LimitSpec(code, key, limit_type.value, low, high, expected))
    return cleaned


def get_spec(session: Session, spec_id: int) -> Specification:
    spec = session.get(Specification, spec_id)
    if spec is None:
        raise NotFound("Specification not found.")
    return spec


def limits_of(session: Session, spec_id: int) -> list[SpecLimit]:
    return list(session.scalars(select(SpecLimit).where(SpecLimit.spec_id == spec_id).order_by(SpecLimit.position)))


def list_specs(session: Session, include_retired: bool = False) -> list[Specification]:
    stmt = select(Specification).order_by(Specification.code, Specification.version.desc())
    if not include_retired:
        stmt = stmt.where(Specification.status != VersionStatus.RETIRED)
    return list(session.scalars(stmt))


def active_specs(session: Session) -> list[Specification]:
    return list(session.scalars(
        select(Specification).where(Specification.status == VersionStatus.ACTIVE).order_by(Specification.code)
    ))


def suggestions_for(session: Session, material_name: str | None, sample_type_id: int | None) -> list[Specification]:
    """Active specs whose material (and type, if set) match a sample — best first."""
    specs = active_specs(session)

    def score(spec: Specification) -> int:
        s = 0
        if material_name and spec.material_name and spec.material_name.strip().lower() == material_name.strip().lower():
            s += 2
        if spec.sample_type_id and spec.sample_type_id == sample_type_id:
            s += 1
        return s

    return sorted((s for s in specs if score(s) > 0), key=score, reverse=True)


def describe(spec: Specification, limits: list[LimitSpec]) -> dict:
    return {
        "code": spec.code, "name": spec.name, "version": spec.version, "status": spec.status,
        "material_name": spec.material_name, "sample_type_id": spec.sample_type_id,
        "description": spec.description,
        "limits": [{k: v for k, v in asdict(limit).items() if v not in (None, "")} for limit in limits],
    }


def _clean_header(session, code, name, material_name, sample_type_id, description):
    code = (code or "").strip().upper()
    name = " ".join((name or "").split())
    if not CODE_PATTERN.match(code):
        raise ValidationError("Specification code must be 1-40 capital letters, numbers, - _ . or / "
                              "(e.g. SPEC-FP-001).")
    if not name:
        raise ValidationError("Specification name is required.")
    if sample_type_id is not None and session.get(SampleType, sample_type_id) is None:
        raise ValidationError("Unknown sample type.")
    return code, name, (" ".join((material_name or "").split()) or None), sample_type_id, \
        ((description or "").strip() or None)


def _save_limits(session: Session, spec: Specification, limits: list[LimitSpec]) -> None:
    for existing in limits_of(session, spec.id):
        session.delete(existing)
    session.flush()
    for position, limit in enumerate(limits):
        session.add(SpecLimit(spec_id=spec.id, position=position, **asdict(limit)))
    session.flush()


def create_spec(
    session: Session, actor: SessionUser, *, code: str, name: str, limits: list[LimitSpec],
    material_name: str | None = None, sample_type_id: int | None = None, description: str | None = None,
) -> Specification:
    require(actor, Permission.MANAGE_MASTER_DATA)
    code, name, material_name, sample_type_id, description = _clean_header(
        session, code, name, material_name, sample_type_id, description)
    if session.scalar(select(Specification.id).where(Specification.code == code)) is not None:
        raise ValidationError(f"A specification with code {code} already exists. Create a new version instead.")
    limits = validate_limits(session, limits)
    spec = Specification(code=code, name=name, version=1, status=VersionStatus.DRAFT, material_name=material_name,
                         sample_type_id=sample_type_id, description=description, created_by_id=actor.id)
    session.add(spec)
    session.flush()
    _save_limits(session, spec, limits)
    audit.record(session, AuditAction.SPEC_CREATED, actor=actor, entity_type="specification",
                 entity_id=f"{code} v1", new=describe(spec, limits))
    return spec


def update_draft(
    session: Session, actor: SessionUser, spec_id: int, *, name: str, limits: list[LimitSpec],
    material_name: str | None = None, sample_type_id: int | None = None, description: str | None = None,
) -> Specification:
    require(actor, Permission.MANAGE_MASTER_DATA)
    spec = get_spec(session, spec_id)
    if spec.status != VersionStatus.DRAFT:
        raise ValidationError("Only draft specifications can be edited. Create a new version instead.")
    _, name, material_name, sample_type_id, description = _clean_header(
        session, spec.code, name, material_name, sample_type_id, description)
    limits = validate_limits(session, limits)
    old = describe(spec, [to_limit(x) for x in limits_of(session, spec.id)])
    spec.name, spec.material_name, spec.sample_type_id, spec.description = name, material_name, sample_type_id, description
    _save_limits(session, spec, limits)
    new = describe(spec, limits)
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    audit.record(session, AuditAction.SPEC_UPDATED, actor=actor, entity_type="specification",
                 entity_id=f"{spec.code} v{spec.version}", old=changed, new={k: new[k] for k in changed})
    return spec


def new_version(session: Session, actor: SessionUser, spec_id: int) -> Specification:
    require(actor, Permission.MANAGE_MASTER_DATA)
    source = get_spec(session, spec_id)
    if session.scalar(select(Specification.id).where(Specification.code == source.code,
                                                     Specification.status == VersionStatus.DRAFT)) is not None:
        raise ValidationError(f"{source.code} already has a draft version. Edit that draft instead.")
    latest = session.scalar(select(func.max(Specification.version)).where(Specification.code == source.code))
    spec = Specification(code=source.code, name=source.name, version=latest + 1, status=VersionStatus.DRAFT,
                         material_name=source.material_name, sample_type_id=source.sample_type_id,
                         description=source.description, created_by_id=actor.id)
    session.add(spec)
    session.flush()
    _save_limits(session, spec, [to_limit(x) for x in limits_of(session, source.id)])
    audit.record(session, AuditAction.SPEC_CREATED, actor=actor, entity_type="specification",
                 entity_id=f"{spec.code} v{spec.version}", new={"copied_from": f"v{source.version}"})
    return spec


def activate(session: Session, actor: SessionUser, spec_id: int, password: str) -> SignatureResult:
    require(actor, Permission.MANAGE_MASTER_DATA)
    spec = get_spec(session, spec_id)
    if spec.status != VersionStatus.DRAFT:
        raise ValidationError("Only a draft can be activated.")
    validate_limits(session, [to_limit(x) for x in limits_of(session, spec.id)])
    label = f"{spec.code} v{spec.version}"
    result = esign.sign(session, actor, password, SignatureMeaning.APPROVED, "specification", label,
                        reason="Specification version approved for use")
    if not result.ok:
        return result
    previous = session.scalar(select(Specification).where(Specification.code == spec.code,
                                                          Specification.status == VersionStatus.ACTIVE))
    now = utcnow()
    if previous is not None:
        previous.status = VersionStatus.RETIRED
        previous.retired_at = now
        audit.record(session, AuditAction.SPEC_RETIRED, actor=actor, entity_type="specification",
                     entity_id=f"{previous.code} v{previous.version}", reason=f"Superseded by v{spec.version}")
    spec.status = VersionStatus.ACTIVE
    spec.activated_at = now
    spec.activated_by_id = actor.id
    audit.record(session, AuditAction.SPEC_ACTIVATED, actor=actor, entity_type="specification", entity_id=label)
    session.flush()
    return result


def retire(session: Session, actor: SessionUser, spec_id: int, reason: str) -> Specification:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    spec = get_spec(session, spec_id)
    if spec.status == VersionStatus.RETIRED:
        raise ValidationError("This version is already retired.")
    spec.status = VersionStatus.RETIRED
    spec.retired_at = utcnow()
    audit.record(session, AuditAction.SPEC_RETIRED, actor=actor, entity_type="specification",
                 entity_id=f"{spec.code} v{spec.version}", reason=reason.strip())
    return spec
