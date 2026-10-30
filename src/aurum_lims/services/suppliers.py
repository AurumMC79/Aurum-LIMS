"""Suppliers of reagents, reference standards, consumables and services.

Each supplier has an approval status:

* **Approved** / **Conditionally approved** – qualified (e.g. questionnaire,
  certificate review or audit); a review date says when to requalify;
* **Under evaluation** – not yet assessed (new suppliers, and the ones created
  from supplier names typed on inventory items before this existed);
* **Disqualified** – lots can no longer be received from them.

Anyone who manages inventory can add suppliers and keep their details up to
date; setting or changing the approval status is a QA decision (the approve
permission). Receiving a lot from a supplier that is not approved, or whose
review is overdue, shows a warning; a disqualified supplier is refused.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import InventoryItem, InventoryLot, Supplier, utcnow
from aurum_lims.errors import NotFound, PermissionDenied, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import audit, inventory
from aurum_lims.services.audit import AuditAction

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SupplierStatus(StrEnum):
    APPROVED = "Approved"
    CONDITIONAL = "Conditionally approved"
    EVALUATION = "Under evaluation"
    DISQUALIFIED = "Disqualified"


APPROVED_STATUSES = {SupplierStatus.APPROVED, SupplierStatus.CONDITIONAL}


@dataclass
class SupplierDetails:
    name: str
    status: str = SupplierStatus.EVALUATION
    scope: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    qualification_ref: str | None = None
    approved_on: date | None = None
    review_due: date | None = None
    notes: str | None = None


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _validate(details: SupplierDetails) -> SupplierDetails:
    name = _clean(details.name)
    if not name:
        raise ValidationError("Supplier name is required.")
    status = SupplierStatus(details.status)
    email = _clean(details.email)
    if email and not EMAIL.match(email):
        raise ValidationError(f"'{email}' is not an email address.")
    approved_on, review_due = details.approved_on, details.review_due
    if status in APPROVED_STATUSES:
        approved_on = approved_on or date.today()
        if approved_on > date.today():
            raise ValidationError("The approval date cannot be in the future.")
        if review_due is None:
            raise ValidationError("Enter when the supplier's approval is due for review.")
        if review_due <= approved_on:
            raise ValidationError("The review date must be after the approval date.")
    return SupplierDetails(name, status.value, _clean(details.scope), _clean(details.contact_name), email,
                           _clean(details.phone), _clean(details.address), _clean(details.qualification_ref),
                           approved_on, review_due, (details.notes or "").strip() or None)


def _check_manage(actor: SessionUser) -> None:
    if not inventory.can_manage(actor):
        raise PermissionDenied("Your role cannot manage suppliers.")


def _check_status_change(actor: SessionUser) -> None:
    if not actor.can(Permission.APPROVE_RECORDS):
        raise PermissionDenied("Only QA can approve, conditionally approve or disqualify a supplier.")


def get(session: Session, supplier_id: int) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise NotFound("Supplier not found.")
    return supplier


def _check_name(session: Session, name: str, supplier_id: int | None = None) -> None:
    clash = session.scalar(select(Supplier.id).where(func.lower(Supplier.name) == name.lower()))
    if clash is not None and clash != supplier_id:
        raise ValidationError(f"A supplier called '{name}' already exists.")


def create_supplier(session: Session, actor: SessionUser, details: SupplierDetails) -> Supplier:
    _check_manage(actor)
    details = _validate(details)
    if details.status != SupplierStatus.EVALUATION:
        _check_status_change(actor)
    _check_name(session, details.name)
    supplier = Supplier(**asdict(details), is_active=True, created_by_id=actor.id)
    session.add(supplier)
    session.flush()
    audit.record(session, AuditAction.SUPPLIER_CREATED, actor=actor, entity_type="supplier", entity_id=supplier.name,
                 new=asdict(details))
    return supplier


def update_supplier(session: Session, actor: SessionUser, supplier_id: int, details: SupplierDetails,
                    reason: str) -> Supplier:
    _check_manage(actor)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    supplier = get(session, supplier_id)
    details = _validate(details)
    if details.status != supplier.status or (details.status in APPROVED_STATUSES and (
            details.approved_on != supplier.approved_on or details.review_due != supplier.review_due)):
        _check_status_change(actor)
    _check_name(session, details.name, supplier.id)
    new = asdict(details)
    old = {k: getattr(supplier, k) for k in new}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key, value in new.items():
        setattr(supplier, key, value)
    supplier.updated_at, supplier.updated_by_id = utcnow(), actor.id
    if "name" in changed:  # items show the supplier's name
        for item in session.scalars(select(InventoryItem).where(InventoryItem.supplier_id == supplier.id)):
            item.supplier = supplier.name
    audit.record(session, AuditAction.SUPPLIER_UPDATED, actor=actor, entity_type="supplier", entity_id=supplier.name,
                 old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    return supplier


def set_active(session: Session, actor: SessionUser, supplier_id: int, active: bool, reason: str) -> Supplier:
    _check_manage(actor)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    supplier = get(session, supplier_id)
    if supplier.is_active == active:
        raise ValidationError("The supplier is already " + ("active." if active else "inactive."))
    supplier.is_active, supplier.updated_at, supplier.updated_by_id = active, utcnow(), actor.id
    audit.record(session, AuditAction.SUPPLIER_UPDATED, actor=actor, entity_type="supplier", entity_id=supplier.name,
                 old={"active": not active}, new={"active": active}, reason=reason.strip())
    return supplier


def list_suppliers(session: Session, include_inactive: bool = False) -> list[Supplier]:
    stmt = select(Supplier).order_by(Supplier.name)
    if not include_inactive:
        stmt = stmt.where(Supplier.is_active.is_(True))
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# receiving and history
# ---------------------------------------------------------------------------


def concerns(supplier: Supplier, today: date | None = None) -> list[str]:
    """Why receiving from this supplier needs attention (empty = approved and in date)."""
    today = today or date.today()
    out = []
    if not supplier.is_active:
        out.append(f"{supplier.name} is inactive")
    if supplier.status == SupplierStatus.DISQUALIFIED:
        out.append(f"{supplier.name} is disqualified")
    elif supplier.status == SupplierStatus.EVALUATION:
        out.append(f"{supplier.name} is not approved yet (under evaluation)")
    elif supplier.status == SupplierStatus.CONDITIONAL:
        out.append(f"{supplier.name} is only conditionally approved")
    if supplier.status in APPROVED_STATUSES and supplier.review_due and supplier.review_due < today:
        out.append(f"{supplier.name} approval review was due {supplier.review_due.isoformat()}")
    return out


def check_receipt(session: Session, supplier_id: int | None) -> Supplier | None:
    """Refuse lots from disqualified or inactive suppliers (other concerns are warnings)."""
    if supplier_id is None:
        return None
    supplier = get(session, supplier_id)
    if supplier.status == SupplierStatus.DISQUALIFIED or not supplier.is_active:
        raise ValidationError(f"Lots cannot be received from {supplier.name}: "
                              + ("it is disqualified." if supplier.is_active else "it is inactive."))
    return supplier


@dataclass
class SupplierRow:
    supplier: Supplier
    items: int
    lots_received: int
    last_received: date | None
    lots_discarded: int
    concerns: list[str]


def overview(session: Session, include_inactive: bool = False) -> list[SupplierRow]:
    items = dict(session.execute(select(InventoryItem.supplier_id, func.count())
                                 .group_by(InventoryItem.supplier_id)).all())
    lots = {sid: (n, last) for sid, n, last in session.execute(
        select(InventoryLot.supplier_id, func.count(), func.max(InventoryLot.received_on))
        .group_by(InventoryLot.supplier_id))}
    discarded = dict(session.execute(select(InventoryLot.supplier_id, func.count())
                                     .where(InventoryLot.status == inventory.LotStatus.DISCARDED)
                                     .group_by(InventoryLot.supplier_id)).all())
    today = date.today()
    return [SupplierRow(s, items.get(s.id, 0), lots.get(s.id, (0, None))[0], lots.get(s.id, (0, None))[1],
                        discarded.get(s.id, 0), concerns(s, today))
            for s in list_suppliers(session, include_inactive)]


@dataclass
class DeliveryRow:
    internal_id: str
    item_name: str
    lot_number: str
    received_on: date
    quantity: str
    status: str


def deliveries(session: Session, supplier_id: int) -> list[DeliveryRow]:
    stmt = (select(InventoryLot, InventoryItem).join(InventoryItem, InventoryItem.id == InventoryLot.item_id)
            .where(InventoryLot.supplier_id == supplier_id).order_by(InventoryLot.received_on.desc(),
                                                                     InventoryLot.id.desc()))
    return [DeliveryRow(lot.internal_id, item.name, lot.lot_number, lot.received_on,
                        f"{lot.quantity_received} {item.unit}", lot.status) for lot, item in session.execute(stmt)]


def reviews_due(session: Session, today: date | None = None) -> list[Supplier]:
    """Approved suppliers whose review date has passed."""
    today = today or date.today()
    return [s for s in list_suppliers(session) if s.status in APPROVED_STATUSES and s.review_due
            and s.review_due < today]
