"""Preventive-maintenance plans for instruments, and suppliers for inventory.

Supplier names already typed on inventory items become supplier records with
the status "Under evaluation" (their approval has not been assessed yet), and
existing items and lots are linked to them.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-29
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], name=f"fk_{table}_{column}_{target.split('.')[0]}")


def upgrade() -> None:
    op.create_table(
        "maintenance_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("tasks", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("last_done", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        _fk("maintenance_plans", "instrument_id", "instruments.id"),
        _fk("maintenance_plans", "created_by_id", "users.id"),
        _fk("maintenance_plans", "updated_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_maintenance_plans"),
        sa.UniqueConstraint("instrument_id", "name", name="uq_maintenance_plans_instrument_id"),
    )
    op.create_index("ix_maintenance_plans_instrument_id", "maintenance_plans", ["instrument_id"])

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("contact_name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("qualification_ref", sa.String(150), nullable=True),
        sa.Column("approved_on", sa.Date(), nullable=True),
        sa.Column("review_due", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        _fk("suppliers", "created_by_id", "users.id"),
        _fk("suppliers", "updated_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.UniqueConstraint("name", name="uq_suppliers_name"),
    )

    # Plain ADD COLUMN: instrument_events is protected by triggers, which a
    # batch-mode table rebuild would drop.
    op.execute("ALTER TABLE instruments ADD COLUMN plan_due DATE")
    op.execute("ALTER TABLE instruments ADD COLUMN plan_due_name VARCHAR(100)")
    op.execute("ALTER TABLE instrument_events ADD COLUMN plan_id INTEGER REFERENCES maintenance_plans (id)")
    op.execute("ALTER TABLE instrument_events ADD COLUMN checklist TEXT")
    op.execute("ALTER TABLE inventory_items ADD COLUMN supplier_id INTEGER REFERENCES suppliers (id)")
    op.execute("ALTER TABLE inventory_lots ADD COLUMN supplier_id INTEGER REFERENCES suppliers (id)")

    bind = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    names = {}
    for (raw,) in bind.execute(sa.text("SELECT DISTINCT supplier FROM inventory_items WHERE supplier IS NOT NULL")):
        name = " ".join(raw.split())
        if name and name.lower() not in names:
            names[name.lower()] = name
    for name in sorted(names.values()):
        bind.execute(sa.text(
            "INSERT INTO suppliers (name, status, notes, is_active, created_at) VALUES (:n, 'Under evaluation', "
            ":notes, 1, :now)"), {"n": name, "now": now,
                                   "notes": "Created automatically from existing inventory items; approval not yet "
                                            "assessed."})
    bind.execute(sa.text(
        "UPDATE inventory_items SET supplier_id = (SELECT s.id FROM suppliers s WHERE lower(s.name) = "
        "lower(trim(inventory_items.supplier))) WHERE supplier IS NOT NULL"))
    bind.execute(sa.text(
        "UPDATE inventory_lots SET supplier_id = (SELECT i.supplier_id FROM inventory_items i "
        "WHERE i.id = inventory_lots.item_id)"))


def downgrade() -> None:
    op.execute("ALTER TABLE inventory_lots DROP COLUMN supplier_id")
    op.execute("ALTER TABLE inventory_items DROP COLUMN supplier_id")
    op.execute("ALTER TABLE instrument_events DROP COLUMN checklist")
    op.execute("ALTER TABLE instrument_events DROP COLUMN plan_id")
    op.execute("ALTER TABLE instruments DROP COLUMN plan_due_name")
    op.execute("ALTER TABLE instruments DROP COLUMN plan_due")
    op.drop_table("suppliers")
    op.drop_table("maintenance_plans")
