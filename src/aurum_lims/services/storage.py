"""Storage locations: a tree of places samples are kept."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import Sample, StorageLocation
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

PATH_SEPARATOR = " › "


class LocationKind(StrEnum):
    SITE = "Site"
    BUILDING = "Building"
    ROOM = "Room"
    REFRIGERATOR = "Refrigerator"
    FREEZER = "Freezer"
    INCUBATOR = "Incubator / stability chamber"
    CABINET = "Cabinet"
    SHELF = "Shelf"
    RACK = "Rack"
    BOX = "Box"
    OTHER = "Other"


def describe(location: StorageLocation) -> dict:
    return {
        "name": location.name,
        "kind": location.kind,
        "parent_id": location.parent_id,
        "condition": location.condition,
        "is_active": location.is_active,
    }


def list_locations(session: Session, include_inactive: bool = True) -> list[StorageLocation]:
    stmt = select(StorageLocation).order_by(StorageLocation.name)
    if not include_inactive:
        stmt = stmt.where(StorageLocation.is_active.is_(True))
    return list(session.scalars(stmt))


def paths(session: Session) -> dict[int, str]:
    """Full display path of every location, e.g. 'QC Lab › Fridge 1 › Shelf 2'."""
    locations = {loc.id: loc for loc in session.scalars(select(StorageLocation))}
    result: dict[int, str] = {}

    def build(location_id: int) -> str:
        if location_id not in result:
            location = locations[location_id]
            prefix = build(location.parent_id) + PATH_SEPARATOR if location.parent_id else ""
            result[location_id] = prefix + location.name
        return result[location_id]

    for location_id in locations:
        build(location_id)
    return result


def active_choices(session: Session) -> list[tuple[int, str]]:
    """(id, full path) of every active location, sorted by path, for pickers."""
    all_paths = paths(session)
    active = session.scalars(select(StorageLocation.id).where(StorageLocation.is_active.is_(True)))
    return sorted(((i, all_paths[i]) for i in active), key=lambda item: item[1].lower())


def sample_counts(session: Session) -> dict[int, int]:
    """Number of samples currently stored directly in each location."""
    rows = session.execute(
        select(Sample.location_id, func.count()).where(Sample.location_id.is_not(None)).group_by(Sample.location_id)
    )
    return {location_id: count for location_id, count in rows}


def get_location(session: Session, location_id: int) -> StorageLocation:
    location = session.get(StorageLocation, location_id)
    if location is None:
        raise NotFound("Storage location not found.")
    return location


def _check_name(session: Session, name: str, parent_id: int | None, exclude_id: int | None = None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValidationError("Name is required.")
    if len(name) > 80:
        raise ValidationError("Name must be 80 characters or fewer.")
    stmt = select(StorageLocation.id).where(
        func.lower(StorageLocation.name) == name.lower(),
        StorageLocation.parent_id.is_(None) if parent_id is None else StorageLocation.parent_id == parent_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(StorageLocation.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise ValidationError(f"There is already a location called '{name}' in the same place.")
    return name


def _check_parent(session: Session, parent_id: int | None, moving_id: int | None = None) -> None:
    if parent_id is None:
        return
    parent = get_location(session, parent_id)
    if not parent.is_active:
        raise ValidationError("The parent location is inactive.")
    # Walk up from the new parent to make sure we are not moving a location inside itself.
    current = parent
    while current is not None:
        if moving_id is not None and current.id == moving_id:
            raise ValidationError("A location cannot be placed inside itself.")
        current = session.get(StorageLocation, current.parent_id) if current.parent_id else None


def create_location(
    session: Session,
    actor: SessionUser,
    *,
    name: str,
    kind: LocationKind | str,
    parent_id: int | None = None,
    condition: str | None = None,
) -> StorageLocation:
    require(actor, Permission.MANAGE_MASTER_DATA)
    _check_parent(session, parent_id)
    name = _check_name(session, name, parent_id)
    location = StorageLocation(
        name=name, kind=LocationKind(kind).value, parent_id=parent_id,
        condition=(condition or "").strip() or None, is_active=True, created_by_id=actor.id,
    )
    session.add(location)
    session.flush()
    audit.record(
        session, AuditAction.LOCATION_CREATED, actor=actor, entity_type="location", entity_id=location.id,
        new={**describe(location), "path": paths(session)[location.id]},
    )
    return location


def update_location(
    session: Session,
    actor: SessionUser,
    location_id: int,
    *,
    reason: str,
    name: str,
    kind: LocationKind | str,
    parent_id: int | None,
    condition: str | None,
) -> StorageLocation:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    location = get_location(session, location_id)
    old = describe(location)
    _check_parent(session, parent_id, moving_id=location_id)
    location.name = _check_name(session, name, parent_id, exclude_id=location_id)
    location.kind = LocationKind(kind).value
    location.parent_id = parent_id
    location.condition = (condition or "").strip() or None
    new = describe(location)
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    audit.record(
        session, AuditAction.LOCATION_UPDATED, actor=actor, entity_type="location", entity_id=location.id,
        old=changed, new={k: new[k] for k in changed}, reason=reason.strip(),
    )
    session.flush()
    return location


def set_active(session: Session, actor: SessionUser, location_id: int, active: bool, reason: str) -> StorageLocation:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    location = get_location(session, location_id)
    if location.is_active == active:
        raise ValidationError(f"This location is already {'active' if active else 'inactive'}.")
    if not active:
        if sample_counts(session).get(location_id):
            raise ValidationError("Move the samples stored here somewhere else before deactivating this location.")
        children = session.scalar(
            select(func.count()).select_from(StorageLocation)
            .where(StorageLocation.parent_id == location_id, StorageLocation.is_active.is_(True))
        )
        if children:
            raise ValidationError("Deactivate the locations inside this one first.")
    else:
        _check_parent(session, location.parent_id)
    location.is_active = active
    audit.record(
        session, AuditAction.LOCATION_UPDATED, actor=actor, entity_type="location", entity_id=location.id,
        old={"is_active": not active}, new={"is_active": active}, reason=reason.strip(),
    )
    session.flush()
    return location
