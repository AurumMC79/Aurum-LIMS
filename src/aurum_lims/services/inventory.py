"""Reagents, reference standards, solvents, columns and consumables.

*Items* are catalogue entries; *lots* are what was actually received, each
with an internal ID (LOT-YYYY-NNNN), dates, certificate reference, purity
(for reference standards) and a quantity on hand. Every stock movement is an
immutable transaction. A lot is usable while it is Available and not past its
effective expiry: the earlier of the supplier's expiry date and
"opened on + days allowed after opening".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import IdCounter, InventoryItem, InventoryLot, LotTransaction, StorageLocation, User
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require_any
from aurum_lims.services import audit, formulas
from aurum_lims.services.audit import AuditAction


class ItemKind(StrEnum):
    REAGENT = "Reagent"
    REFERENCE_STANDARD = "Reference standard"
    SOLVENT = "Solvent"
    SOLUTION = "Buffer / solution"
    COLUMN = "Column"
    CONSUMABLE = "Consumable"
    MEDIA = "Culture media"
    OTHER = "Other"


class LotStatus(StrEnum):
    AVAILABLE = "Available"
    DEPLETED = "Depleted"
    DISCARDED = "Discarded"


class TransactionKind(StrEnum):
    RECEIVED = "Received"
    USED = "Used"
    RETURNED = "Returned"
    ADJUSTED = "Adjusted"
    DISCARDED = "Discarded"


UNITS = ["g", "mg", "kg", "mL", "L", "µL", "units", "vials", "bottles", "columns", "plates", "packs"]
ZERO = Decimal(0)


INVENTORY_PERMISSIONS = (Permission.EDIT_RECORDS, Permission.MANAGE_MASTER_DATA, Permission.MANAGE_INVENTORY)


def can_manage(actor: SessionUser) -> bool:
    return any(actor.can(p) for p in INVENTORY_PERMISSIONS)


def _can_manage(actor: SessionUser) -> None:
    require_any(actor, *INVENTORY_PERMISSIONS)


def _clean(value: str | None) -> str | None:
    return " ".join(value.split()) if value and value.strip() else None


def _quantity(text: str | None, label: str, allow_zero: bool = False) -> Decimal:
    value = formulas.parse_decimal(text)
    if value is None:
        raise ValidationError(f"Enter the {label}.")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(f"The {label} must be greater than zero.")
    return value


def _fmt(value: Decimal) -> str:
    return formulas.format_decimal(value) or "0"


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------


@dataclass
class ItemDetails:
    name: str
    kind: str
    unit: str
    grade: str | None = None
    supplier: str | None = None
    catalogue_number: str | None = None
    min_stock: str | None = None
    days_after_opening: int | None = None


def _validate_item(details: ItemDetails) -> ItemDetails:
    name = _clean(details.name)
    if not name:
        raise ValidationError("Item name is required.")
    kind = ItemKind(details.kind).value
    unit = _clean(details.unit)
    if not unit:
        raise ValidationError("Choose the unit stock is counted in (e.g. g, mL, units).")
    min_stock = _clean(details.min_stock)
    if min_stock is not None:
        _quantity(min_stock, "minimum stock", allow_zero=True)
    days = details.days_after_opening or None
    if days is not None and not 1 <= int(days) <= 3650:
        raise ValidationError("Days allowed after opening must be between 1 and 3650 (or empty).")
    return ItemDetails(name, kind, unit, _clean(details.grade), _clean(details.supplier),
                       _clean(details.catalogue_number), min_stock, days)


def get_item(session: Session, item_id: int) -> InventoryItem:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise NotFound("Inventory item not found.")
    return item


def list_items(session: Session, include_inactive: bool = False, kind: str | None = None) -> list[InventoryItem]:
    stmt = select(InventoryItem).order_by(InventoryItem.name)
    if not include_inactive:
        stmt = stmt.where(InventoryItem.is_active.is_(True))
    if kind:
        stmt = stmt.where(InventoryItem.kind == kind)
    return list(session.scalars(stmt))


def create_item(session: Session, actor: SessionUser, details: ItemDetails) -> InventoryItem:
    _can_manage(actor)
    details = _validate_item(details)
    item = InventoryItem(created_by_id=actor.id, is_active=True, **vars(details))
    session.add(item)
    session.flush()
    audit.record(session, AuditAction.ITEM_CREATED, actor=actor, entity_type="inventory_item", entity_id=item.name,
                 new=vars(details))
    return item


def update_item(session: Session, actor: SessionUser, item_id: int, details: ItemDetails, is_active: bool,
                reason: str) -> InventoryItem:
    _can_manage(actor)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    item = get_item(session, item_id)
    new = {**vars(_validate_item(details)), "is_active": bool(is_active)}
    old = {k: getattr(item, k) for k in new}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key, value in new.items():
        setattr(item, key, value)
    audit.record(session, AuditAction.ITEM_UPDATED, actor=actor, entity_type="inventory_item", entity_id=item.name,
                 old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    return item


# ---------------------------------------------------------------------------
# lots
# ---------------------------------------------------------------------------


def effective_expiry(lot: InventoryLot, item: InventoryItem) -> date | None:
    dates = [d for d in (lot.expiry_date,) if d]
    if lot.opened_on and item.days_after_opening:
        dates.append(lot.opened_on + timedelta(days=item.days_after_opening))
    return min(dates) if dates else None


def lot_problems(session: Session, lot: InventoryLot, today: date | None = None) -> list[str]:
    """Why a lot cannot be used today (empty list = usable)."""
    today = today or date.today()
    item = session.get(InventoryItem, lot.item_id)
    out = []
    if lot.status == LotStatus.DISCARDED:
        out.append(f"{lot.internal_id} has been discarded")
    expiry = effective_expiry(lot, item)
    if expiry and expiry < today:
        out.append(f"{lot.internal_id} ({item.name}) expired on {expiry.isoformat()}")
    return out


def get_lot(session: Session, lot_id: int) -> InventoryLot:
    lot = session.get(InventoryLot, lot_id)
    if lot is None:
        raise NotFound("Lot not found.")
    return lot


def _next_internal_id(session: Session) -> str:
    year = datetime.now().astimezone().year
    counter = session.get(IdCounter, ("lot", str(year)))
    if counter is None:
        counter = IdCounter(scope="lot", period=str(year), last_value=0)
        session.add(counter)
    counter.last_value += 1
    session.flush()
    return f"LOT-{year}-{counter.last_value:04d}"


def _transaction(session: Session, actor: SessionUser, lot: InventoryLot, kind: TransactionKind, quantity: Decimal,
                 *, analysis_id: int | None = None, reason: str | None = None) -> LotTransaction:
    balance = formulas.parse_decimal(lot.quantity_on_hand) + quantity
    if balance < 0:
        raise ValidationError(f"Not enough stock in {lot.internal_id}: {lot.quantity_on_hand} on hand.")
    lot.quantity_on_hand = _fmt(balance)
    if lot.status != LotStatus.DISCARDED:
        lot.status = LotStatus.DEPLETED if balance == 0 else LotStatus.AVAILABLE
    record = LotTransaction(lot_id=lot.id, kind=kind.value, quantity=_fmt(quantity), balance_after=lot.quantity_on_hand,
                            analysis_id=analysis_id, reason=reason, by_id=actor.id)
    session.add(record)
    session.flush()
    return record


def receive_lot(
    session: Session,
    actor: SessionUser,
    item_id: int,
    *,
    lot_number: str,
    quantity: str,
    received_on: date | None = None,
    expiry_date: date | None = None,
    purity: str | None = None,
    certificate_ref: str | None = None,
    location_id: int | None = None,
    notes: str | None = None,
) -> InventoryLot:
    _can_manage(actor)
    item = get_item(session, item_id)
    if not item.is_active:
        raise ValidationError(f"{item.name} is inactive.")
    lot_number = _clean(lot_number)
    if not lot_number:
        raise ValidationError("Enter the supplier's lot number.")
    amount = _quantity(quantity, "quantity received")
    received_on = received_on or date.today()
    if received_on > date.today():
        raise ValidationError("The received date cannot be in the future.")
    if expiry_date and expiry_date < received_on:
        raise ValidationError("The expiry date is before the received date.")
    purity = _clean(purity)
    if item.kind == ItemKind.REFERENCE_STANDARD:
        value = formulas.parse_decimal(purity)
        if value is None or not ZERO < value <= 100:
            raise ValidationError("Enter the certified purity of the reference standard (0–100 %).")
    elif purity is not None:
        formulas.parse_decimal(purity)
    if location_id is not None:
        location = session.get(StorageLocation, location_id)
        if location is None or not location.is_active:
            raise ValidationError("Choose an active storage location.")
    lot = InventoryLot(
        item_id=item.id, lot_number=lot_number, internal_id=_next_internal_id(session), status=LotStatus.AVAILABLE,
        received_on=received_on, expiry_date=expiry_date, quantity_received=_fmt(amount), quantity_on_hand="0",
        purity=purity, certificate_ref=_clean(certificate_ref), location_id=location_id,
        notes=(notes or "").strip() or None, created_by_id=actor.id,
    )
    session.add(lot)
    session.flush()
    _transaction(session, actor, lot, TransactionKind.RECEIVED, amount)
    audit.record(session, AuditAction.LOT_RECEIVED, actor=actor, entity_type="lot", entity_id=lot.internal_id,
                 new={"item": item.name, "lot_number": lot_number, "quantity": f"{lot.quantity_received} {item.unit}",
                      "expiry_date": expiry_date, "purity": purity, "certificate_ref": lot.certificate_ref})
    return lot


def open_lot(session: Session, actor: SessionUser, lot_id: int, opened_on: date | None = None) -> InventoryLot:
    _can_manage(actor)
    lot = get_lot(session, lot_id)
    if lot.opened_on is not None:
        raise ValidationError(f"{lot.internal_id} was already opened on {lot.opened_on.isoformat()}.")
    opened_on = opened_on or date.today()
    if opened_on > date.today() or opened_on < lot.received_on:
        raise ValidationError("The opened date must be between the received date and today.")
    lot.opened_on = opened_on
    item = session.get(InventoryItem, lot.item_id)
    audit.record(session, AuditAction.LOT_UPDATED, actor=actor, entity_type="lot", entity_id=lot.internal_id,
                 new={"opened_on": opened_on, "effective_expiry": effective_expiry(lot, item)})
    return lot


def adjust_stock(session: Session, actor: SessionUser, lot_id: int, counted_quantity: str, reason: str) -> InventoryLot:
    """Set the quantity on hand to a counted value (e.g. after a stock check)."""
    _can_manage(actor)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to adjust stock.")
    lot = get_lot(session, lot_id)
    if lot.status == LotStatus.DISCARDED:
        raise ValidationError(f"{lot.internal_id} has been discarded.")
    counted = _quantity(counted_quantity, "counted quantity", allow_zero=True)
    before = lot.quantity_on_hand
    delta = counted - formulas.parse_decimal(before)
    if delta == 0:
        raise ValidationError("The counted quantity equals the quantity on hand.")
    _transaction(session, actor, lot, TransactionKind.ADJUSTED, delta, reason=reason.strip())
    audit.record(session, AuditAction.LOT_UPDATED, actor=actor, entity_type="lot", entity_id=lot.internal_id,
                 old={"quantity_on_hand": before}, new={"quantity_on_hand": lot.quantity_on_hand}, reason=reason.strip())
    return lot


def discard_lot(session: Session, actor: SessionUser, lot_id: int, reason: str) -> InventoryLot:
    _can_manage(actor)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to discard a lot.")
    lot = get_lot(session, lot_id)
    if lot.status == LotStatus.DISCARDED:
        raise ValidationError(f"{lot.internal_id} is already discarded.")
    remaining = formulas.parse_decimal(lot.quantity_on_hand)
    lot.status = LotStatus.DISCARDED
    _transaction(session, actor, lot, TransactionKind.DISCARDED, -remaining, reason=reason.strip())
    audit.record(session, AuditAction.LOT_UPDATED, actor=actor, entity_type="lot", entity_id=lot.internal_id,
                 new={"status": LotStatus.DISCARDED.value, "discarded_quantity": _fmt(remaining)}, reason=reason.strip())
    return lot


def consume(session: Session, actor: SessionUser, lot: InventoryLot, quantity: Decimal, analysis_id: int,
            returned: bool = False) -> None:
    """Take stock out for a test (or put it back when a usage is removed)."""
    kind = TransactionKind.RETURNED if returned else TransactionKind.USED
    _transaction(session, actor, lot, kind, quantity if returned else -quantity, analysis_id=analysis_id,
                 reason="Usage removed from test" if returned else None)


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


@dataclass
class LotRow:
    id: int
    internal_id: str
    item_id: int
    item_name: str
    kind: str
    lot_number: str
    status: str
    quantity_on_hand: str
    unit: str
    received_on: date
    opened_on: date | None
    expiry: date | None
    purity: str | None
    certificate_ref: str | None
    location_id: int | None
    problems: list[str]


def list_lots(session: Session, *, item_id: int | None = None, include_closed: bool = False,
              kind: str | None = None) -> list[LotRow]:
    stmt = (select(InventoryLot, InventoryItem).join(InventoryItem, InventoryLot.item_id == InventoryItem.id)
            .order_by(InventoryItem.name, InventoryLot.id.desc()))
    if item_id is not None:
        stmt = stmt.where(InventoryLot.item_id == item_id)
    if not include_closed:
        stmt = stmt.where(InventoryLot.status == LotStatus.AVAILABLE)
    if kind:
        stmt = stmt.where(InventoryItem.kind == kind)
    rows = []
    for lot, item in session.execute(stmt):
        rows.append(LotRow(lot.id, lot.internal_id, item.id, item.name, item.kind, lot.lot_number, lot.status,
                           lot.quantity_on_hand, item.unit, lot.received_on, lot.opened_on,
                           effective_expiry(lot, item), lot.purity, lot.certificate_ref, lot.location_id,
                           lot_problems(session, lot)))
    return rows


@dataclass
class TransactionRow:
    at: datetime
    kind: str
    quantity: str
    balance_after: str
    reason: str | None
    analysis_id: int | None
    by: str


def transactions(session: Session, lot_id: int) -> list[TransactionRow]:
    rows = session.execute(select(LotTransaction, User.full_name).join(User, LotTransaction.by_id == User.id)
                           .where(LotTransaction.lot_id == lot_id).order_by(LotTransaction.id))
    return [TransactionRow(t.at, t.kind, t.quantity, t.balance_after, t.reason, t.analysis_id, who)
            for t, who in rows]


def stock_on_hand(session: Session, item: InventoryItem) -> Decimal:
    total = ZERO
    for lot in session.scalars(select(InventoryLot).where(InventoryLot.item_id == item.id,
                                                          InventoryLot.status == LotStatus.AVAILABLE)):
        if not lot_problems(session, lot):
            total += formulas.parse_decimal(lot.quantity_on_hand)
    return total


def low_stock(session: Session) -> list[tuple[InventoryItem, Decimal]]:
    """Active items whose usable stock is below their minimum."""
    out = []
    for item in list_items(session):
        if item.min_stock is None:
            continue
        on_hand = stock_on_hand(session, item)
        if on_hand < formulas.parse_decimal(item.min_stock):
            out.append((item, on_hand))
    return out


def expiring(session: Session, within_days: int = 30) -> tuple[list[LotRow], list[LotRow]]:
    """(expired, expiring within ``within_days``) among available lots."""
    today = date.today()
    expired, soon = [], []
    for row in list_lots(session):
        if row.expiry is None:
            continue
        if row.expiry < today:
            expired.append(row)
        elif row.expiry <= today + timedelta(days=within_days):
            soon.append(row)
    return expired, soon
