"""Instruments, calibration events, inventory items/lots, stock transactions,
method requirements and what each test used.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

IMMUTABLE_TABLES = ("instrument_events", "lot_transactions")


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    target_table = target.split(".")[0]
    return sa.ForeignKeyConstraint([column], [target], name=f"fk_{table}_{column}_{target_table}")


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("serial_number", sa.String(100), nullable=True),
        sa.Column("location", sa.String(150), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("calibration_interval_days", sa.Integer(), nullable=True),
        sa.Column("maintenance_interval_days", sa.Integer(), nullable=True),
        sa.Column("calibration_due", sa.Date(), nullable=True),
        sa.Column("maintenance_due", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        _fk("instruments", "created_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_instruments"),
        sa.UniqueConstraint("code", name="uq_instruments_code"),
    )
    op.create_index("ix_instruments_category", "instruments", ["category"])

    op.create_table(
        "instrument_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("performed_by", sa.String(150), nullable=True),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("certificate_ref", sa.String(100), nullable=True),
        sa.Column("next_due", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), nullable=False),
        _fk("instrument_events", "instrument_id", "instruments.id"),
        _fk("instrument_events", "recorded_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_instrument_events"),
    )
    op.create_index("ix_instrument_events_instrument_id", "instrument_events", ["instrument_id"])

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("grade", sa.String(80), nullable=True),
        sa.Column("supplier", sa.String(150), nullable=True),
        sa.Column("catalogue_number", sa.String(80), nullable=True),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("min_stock", sa.String(32), nullable=True),
        sa.Column("days_after_opening", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        _fk("inventory_items", "created_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_items"),
    )
    op.create_index("ix_inventory_items_kind", "inventory_items", ["kind"])

    op.create_table(
        "inventory_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("lot_number", sa.String(80), nullable=False),
        sa.Column("internal_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("quantity_received", sa.String(32), nullable=False),
        sa.Column("quantity_on_hand", sa.String(32), nullable=False),
        sa.Column("purity", sa.String(32), nullable=True),
        sa.Column("certificate_ref", sa.String(100), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        _fk("inventory_lots", "item_id", "inventory_items.id"),
        _fk("inventory_lots", "location_id", "storage_locations.id"),
        _fk("inventory_lots", "created_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_lots"),
        sa.UniqueConstraint("internal_id", name="uq_inventory_lots_internal_id"),
    )
    op.create_index("ix_inventory_lots_item_id", "inventory_lots", ["item_id"])

    op.create_table(
        "lot_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity", sa.String(32), nullable=False),
        sa.Column("balance_after", sa.String(32), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("by_id", sa.Integer(), nullable=False),
        _fk("lot_transactions", "lot_id", "inventory_lots.id"),
        _fk("lot_transactions", "analysis_id", "analyses.id"),
        _fk("lot_transactions", "by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_lot_transactions"),
    )
    op.create_index("ix_lot_transactions_lot_id", "lot_transactions", ["lot_id"])

    op.create_table(
        "method_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("method_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("fills_parameter", sa.String(40), nullable=True),
        _fk("method_requirements", "method_id", "test_methods.id"),
        sa.PrimaryKeyConstraint("id", name="pk_method_requirements"),
    )
    op.create_index("ix_method_requirements_method_id", "method_requirements", ["method_id"])

    op.create_table(
        "analysis_usages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=True),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.Column("lot_id", sa.Integer(), nullable=True),
        sa.Column("quantity_used", sa.String(32), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), nullable=False),
        _fk("analysis_usages", "analysis_id", "analyses.id"),
        _fk("analysis_usages", "requirement_id", "method_requirements.id"),
        _fk("analysis_usages", "instrument_id", "instruments.id"),
        _fk("analysis_usages", "lot_id", "inventory_lots.id"),
        _fk("analysis_usages", "recorded_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_usages"),
    )
    op.create_index("ix_analysis_usages_analysis_id", "analysis_usages", ["analysis_id"])

    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
                )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    for table in ("analysis_usages", "method_requirements", "lot_transactions", "inventory_lots",
                  "inventory_items", "instrument_events", "instruments"):
        op.drop_table(table)
