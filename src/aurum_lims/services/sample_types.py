"""Sample types and the descriptive fields each category of sample needs."""

from __future__ import annotations

import re
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import SampleType
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, sample_ids
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.sample_ids import ResetPolicy


class Category(StrEnum):
    RAW_MATERIAL = "raw_material"
    PRODUCT = "product"
    STABILITY = "stability"
    ENVIRONMENTAL = "environmental"
    OTHER = "other"

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self]


CATEGORY_LABELS = {
    Category.RAW_MATERIAL: "Raw material / incoming",
    Category.PRODUCT: "In-process / finished product",
    Category.STABILITY: "Stability",
    Category.ENVIRONMENTAL: "Environmental / water / utilities",
    Category.OTHER: "Other",
}

FIELD_LABELS = {
    "material_name": "Material / product",
    "lot_number": "Lot / batch number",
    "supplier": "Supplier",
    "sampling_point": "Sampling point",
    "stability_study": "Stability study",
    "stability_timepoint": "Time point",
    "stability_condition": "Storage condition",
}

# Descriptive fields shown for each category, in order; (field, required).
CATEGORY_FIELDS: dict[Category, list[tuple[str, bool]]] = {
    Category.RAW_MATERIAL: [("material_name", True), ("lot_number", True), ("supplier", False)],
    Category.PRODUCT: [("material_name", True), ("lot_number", True), ("sampling_point", False)],
    Category.STABILITY: [
        ("material_name", True), ("lot_number", True), ("stability_study", True),
        ("stability_timepoint", True), ("stability_condition", False),
    ],
    Category.ENVIRONMENTAL: [("sampling_point", True), ("material_name", False)],
    Category.OTHER: [("material_name", False), ("lot_number", False), ("supplier", False), ("sampling_point", False)],
}

DEFAULT_TYPES = [
    ("Raw Material", "RM", Category.RAW_MATERIAL, "Incoming raw materials and components tested before release."),
    ("In-Process", "IP", Category.PRODUCT, "Samples taken during manufacture."),
    ("Finished Product", "FP", Category.PRODUCT, "Finished product batches tested for release."),
    ("Stability", "STB", Category.STABILITY, "Samples pulled from stability studies at set time points."),
    ("Environmental Monitoring", "EM", Category.ENVIRONMENTAL, "Swabs, settle plates and air samples."),
    ("Water", "WTR", Category.ENVIRONMENTAL, "Purified water and other utility samples."),
    ("Other", "OTH", Category.OTHER, "Anything that does not fit another type."),
]

CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,16}$")
SEEDED_MARKER = "sample_types_seeded"


def describe(sample_type: SampleType) -> dict:
    return {
        "name": sample_type.name,
        "code": sample_type.code,
        "category": sample_type.category,
        "description": sample_type.description,
        "id_format": sample_type.id_format,
        "id_reset": sample_type.id_reset,
        "is_active": sample_type.is_active,
    }


def list_types(session: Session, include_inactive: bool = False) -> list[SampleType]:
    stmt = select(SampleType).order_by(SampleType.name)
    if not include_inactive:
        stmt = stmt.where(SampleType.is_active.is_(True))
    return list(session.scalars(stmt))


def get_type(session: Session, type_id: int) -> SampleType:
    sample_type = session.get(SampleType, type_id)
    if sample_type is None:
        raise NotFound("Sample type not found.")
    return sample_type


def seed_defaults(session: Session) -> None:
    """Create the starter sample types once, on a fresh database."""
    from aurum_lims.db.models import AppSetting

    if session.get(AppSetting, SEEDED_MARKER) is not None:
        return
    if session.scalar(select(func.count()).select_from(SampleType)) == 0:
        for name, code, category, description in DEFAULT_TYPES:
            sample_type = SampleType(name=name, code=code, category=category, description=description, is_active=True)
            session.add(sample_type)
            session.flush()
            audit.record(
                session, AuditAction.SAMPLE_TYPE_CREATED, entity_type="sample_type", entity_id=code,
                new=describe(sample_type), reason="Default sample types created at first start-up",
            )
    session.add(AppSetting(key=SEEDED_MARKER, value="1"))
    session.flush()


def _clean(session: Session, name: str, code: str, category, id_format: str | None, id_reset, exclude_id=None):
    name = " ".join((name or "").split())
    code = (code or "").strip().upper()
    if not name:
        raise ValidationError("Name is required.")
    if len(name) > 80:
        raise ValidationError("Name must be 80 characters or fewer.")
    if not CODE_PATTERN.match(code):
        raise ValidationError("Code must be 1-16 capital letters or numbers, e.g. RM.")
    for column, value, label in ((SampleType.name, name, "name"), (SampleType.code, code, "code")):
        stmt = select(SampleType.id).where(func.lower(column) == value.lower())
        if exclude_id is not None:
            stmt = stmt.where(SampleType.id != exclude_id)
        if session.scalar(stmt) is not None:
            raise ValidationError(f"Another sample type already uses the {label} '{value}'.")
    category = Category(category)
    id_format = (id_format or "").strip() or None
    id_reset = ResetPolicy(id_reset).value if id_format and id_reset else (ResetPolicy.YEARLY.value if id_format else None)
    if id_format:
        sample_ids.validate(id_format, id_reset)
    return name, code, category, id_format, id_reset


def create_type(
    session: Session,
    actor: SessionUser,
    *,
    name: str,
    code: str,
    category: Category | str,
    description: str | None = None,
    id_format: str | None = None,
    id_reset: ResetPolicy | str | None = None,
) -> SampleType:
    require(actor, Permission.MANAGE_MASTER_DATA)
    name, code, category, id_format, id_reset = _clean(session, name, code, category, id_format, id_reset)
    sample_type = SampleType(
        name=name, code=code, category=category, description=(description or "").strip() or None,
        id_format=id_format, id_reset=id_reset, is_active=True, created_by_id=actor.id,
    )
    session.add(sample_type)
    session.flush()
    audit.record(
        session, AuditAction.SAMPLE_TYPE_CREATED, actor=actor, entity_type="sample_type", entity_id=code,
        new=describe(sample_type),
    )
    return sample_type


def update_type(
    session: Session,
    actor: SessionUser,
    type_id: int,
    *,
    reason: str,
    name: str,
    code: str,
    category: Category | str,
    description: str | None,
    id_format: str | None,
    id_reset: ResetPolicy | str | None,
    is_active: bool,
) -> SampleType:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    sample_type = get_type(session, type_id)
    old = describe(sample_type)
    name, code, category, id_format, id_reset = _clean(
        session, name, code, category, id_format, id_reset, exclude_id=type_id
    )
    sample_type.name = name
    sample_type.code = code
    sample_type.category = category
    sample_type.description = (description or "").strip() or None
    sample_type.id_format = id_format
    sample_type.id_reset = id_reset
    sample_type.is_active = bool(is_active)
    new = describe(sample_type)
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    audit.record(
        session, AuditAction.SAMPLE_TYPE_UPDATED, actor=actor, entity_type="sample_type", entity_id=code,
        old=changed, new={k: new[k] for k in changed}, reason=reason.strip(),
    )
    session.flush()
    return sample_type
