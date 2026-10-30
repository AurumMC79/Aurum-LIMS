"""Suppliers: approval status, receiving lots, history, and converting typed supplier names."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from aurum_lims.db.models import AuditEntry, InventoryItem, InventoryLot
from aurum_lims.db.session import Database
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import inventory, suppliers
from aurum_lims.services.inventory import ItemDetails, ItemKind
from aurum_lims.services.suppliers import SupplierDetails, SupplierStatus
from tests.test_migrations import _upgrade_to

TODAY = date.today()
APPROVED = SupplierDetails("Merck KGaA", SupplierStatus.APPROVED, scope="Reagents, solvents",
                           qualification_ref="QA-SUP-014 questionnaire", review_due=TODAY + timedelta(days=730))


def _item(db, actor, supplier_id=None, name="Acetonitrile HPLC grade"):
    with db.session() as s:
        return inventory.create_item(s, actor, ItemDetails(name=name, kind=ItemKind.SOLVENT, unit="mL",
                                                           supplier_id=supplier_id)).id


def test_who_can_set_the_approval_status(db, admin, make_user):
    cho = make_user("cho1", Role.CHEMICAL_HYGIENE_OFFICER)
    qa = make_user("qa1", Role.QA_REVIEWER)
    with db.session() as s:
        with pytest.raises(PermissionDenied, match="Only QA"):
            suppliers.create_supplier(s, cho, APPROVED)
        new = suppliers.create_supplier(s, cho, SupplierDetails("Sigma-Aldrich", email="orders@sigma.example"))
        assert new.status == SupplierStatus.EVALUATION
        supplier_id = new.id
    with db.session() as s:
        with pytest.raises(PermissionDenied, match="Only QA"):
            suppliers.update_supplier(s, cho, supplier_id, SupplierDetails("Sigma-Aldrich", SupplierStatus.APPROVED,
                                                                           review_due=TODAY + timedelta(days=365)), "x")
        suppliers.update_supplier(s, cho, supplier_id, SupplierDetails("Sigma-Aldrich", phone="+1 555 0101",
                                                                       email="orders@sigma.example"),
                                  "Phone number added")
        with pytest.raises(ValidationError, match="review"):
            suppliers.update_supplier(s, qa, supplier_id, SupplierDetails("Sigma-Aldrich", SupplierStatus.APPROVED),
                                      "Questionnaire accepted")
        suppliers.update_supplier(s, qa, supplier_id, SupplierDetails(
            "Sigma-Aldrich", SupplierStatus.APPROVED, phone="+1 555 0101", email="orders@sigma.example",
            review_due=TODAY + timedelta(days=365)), "Questionnaire accepted")
    with db.session() as s:
        supplier = suppliers.get(s, supplier_id)
        assert supplier.status == SupplierStatus.APPROVED and supplier.approved_on == TODAY
        assert suppliers.concerns(supplier) == []
        actions = [e.action for e in s.scalars(select(AuditEntry).where(AuditEntry.entity_type == "supplier"))]
        assert actions == ["SUPPLIER_CREATED", "SUPPLIER_UPDATED", "SUPPLIER_UPDATED"]


def test_validation(db, admin, make_user):
    admin = make_user("qa1", Role.QA_REVIEWER)  # approving is QA's decision
    with db.session() as s:
        for details, message in [(SupplierDetails(" "), "name"),
                                 (SupplierDetails("X", email="not-an-email"), "email"),
                                 (SupplierDetails("X", SupplierStatus.APPROVED, review_due=TODAY - timedelta(days=1)),
                                  "after the approval date")]:
            with pytest.raises(ValidationError, match=message):
                suppliers.create_supplier(s, admin, details)
        suppliers.create_supplier(s, admin, APPROVED)
        with pytest.raises(ValidationError, match="already exists"):
            suppliers.create_supplier(s, admin, SupplierDetails("merck kgaa"))


def test_items_lots_and_disqualification(db, admin, make_user):
    admin = make_user("qa1", Role.QA_REVIEWER)
    with db.session() as s:
        supplier_id = suppliers.create_supplier(s, admin, APPROVED).id
    item_id = _item(db, admin, supplier_id)
    with db.session() as s:
        assert s.get(InventoryItem, item_id).supplier == "Merck KGaA"
        lot = inventory.receive_lot(s, admin, item_id, lot_number="I1234", quantity="4000")
        assert lot.supplier_id == supplier_id
    with db.session() as s:
        suppliers.update_supplier(s, admin, supplier_id, SupplierDetails("Merck Life Science", SupplierStatus.APPROVED,
                                                                         scope=APPROVED.scope,
                                                                         qualification_ref=APPROVED.qualification_ref,
                                                                         approved_on=TODAY,
                                                                         review_due=APPROVED.review_due),
                                  "Company renamed")
    with db.session() as s:
        assert s.get(InventoryItem, item_id).supplier == "Merck Life Science"  # items follow the name
        [row] = suppliers.overview(s)
        assert (row.items, row.lots_received, row.last_received, row.lots_discarded) == (1, 1, TODAY, 0)
        [delivery] = suppliers.deliveries(s, supplier_id)
        assert delivery.lot_number == "I1234" and delivery.quantity == "4000 mL"
        suppliers.update_supplier(s, admin, supplier_id, SupplierDetails(
            "Merck Life Science", SupplierStatus.DISQUALIFIED, scope=APPROVED.scope,
            qualification_ref=APPROVED.qualification_ref, approved_on=TODAY, review_due=APPROVED.review_due),
            "Repeated out-of-spec deliveries")
    with db.session() as s:
        with pytest.raises(ValidationError, match="disqualified"):
            inventory.receive_lot(s, admin, item_id, lot_number="I1300", quantity="4000")


def test_concerns_and_reviews_due(db, admin, make_user):
    admin = make_user("qa1", Role.QA_REVIEWER)
    with db.session() as s:
        evaluation = suppliers.create_supplier(s, admin, SupplierDetails("New Supplier Ltd"))
        conditional = suppliers.create_supplier(s, admin, SupplierDetails(
            "Columns R Us", SupplierStatus.CONDITIONAL, approved_on=TODAY - timedelta(days=400),
            review_due=TODAY - timedelta(days=35)))
        assert suppliers.concerns(evaluation) == ["New Supplier Ltd is not approved yet (under evaluation)"]
        assert suppliers.concerns(conditional) == [
            "Columns R Us is only conditionally approved",
            f"Columns R Us approval review was due {(TODAY - timedelta(days=35)).isoformat()}"]
        assert [x.name for x in suppliers.reviews_due(s)] == ["Columns R Us"]
        suppliers.set_active(s, admin, evaluation.id, False, "Never used")
    item_id = _item(db, admin)
    with db.session() as s:
        with pytest.raises(ValidationError, match="inactive"):
            inventory.receive_lot(s, admin, item_id, lot_number="1", quantity="1", supplier_id=evaluation.id)
        with pytest.raises(ValidationError, match="active supplier"):
            inventory.create_item(s, admin, ItemDetails(name="Y", kind=ItemKind.SOLVENT, unit="mL",
                                                        supplier_id=evaluation.id))


def test_migration_turns_typed_names_into_suppliers(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    try:
        _upgrade_to(db, "0010")
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, username, full_name, role, password_hash, is_active, is_locked, "
                "failed_login_count, must_change_password, password_changed_at, created_at) "
                "VALUES (1, 'old', 'Old User', 'administrator', 'x', 1, 0, 0, 0, '2026-10-01', '2026-10-01')"))
            for i, supplier in enumerate(["Merck", " merck ", "VWR", None], start=1):
                conn.execute(text(
                    "INSERT INTO inventory_items (id, name, kind, supplier, unit, is_active, created_at, "
                    "created_by_id) VALUES (:i, :n, 'Reagent', :s, 'g', 1, '2026-10-01', 1)"),
                    {"i": i, "n": f"Item {i}", "s": supplier})
            conn.execute(text(
                "INSERT INTO inventory_lots (item_id, lot_number, internal_id, status, received_on, "
                "quantity_received, quantity_on_hand, created_at, created_by_id) "
                "VALUES (2, 'L1', 'LOT-2026-0001', 'Available', '2026-10-01', '5', '5', '2026-10-01', 1)"))
        db.upgrade()
        with db.session() as s:
            rows = {x.name: x for x in suppliers.list_suppliers(s)}
            assert set(rows) == {"Merck", "VWR"} and rows["Merck"].status == SupplierStatus.EVALUATION
            items = {i.name: i.supplier_id for i in s.scalars(select(InventoryItem))}
            assert items == {"Item 1": rows["Merck"].id, "Item 2": rows["Merck"].id, "Item 3": rows["VWR"].id,
                             "Item 4": None}
            assert s.scalar(select(InventoryLot.supplier_id)) == rows["Merck"].id
    finally:
        db.dispose()
