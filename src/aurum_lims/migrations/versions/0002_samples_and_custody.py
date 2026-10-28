"""Samples, sample types, storage locations, chain of custody, ID counters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sample_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id_format", sa.String(80), nullable=True),
        sa.Column("id_reset", sa.String(16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_sample_types_created_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_sample_types"),
        sa.UniqueConstraint("name", name="uq_sample_types_name"),
        sa.UniqueConstraint("code", name="uq_sample_types_code"),
    )

    op.create_table(
        "storage_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["storage_locations.id"], name="fk_storage_locations_parent_id_storage_locations"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_storage_locations_created_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_storage_locations"),
    )

    op.create_table(
        "samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.String(64), nullable=False),
        sa.Column("sample_type_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("material_name", sa.String(200), nullable=True),
        sa.Column("lot_number", sa.String(100), nullable=True),
        sa.Column("supplier", sa.String(200), nullable=True),
        sa.Column("sampling_point", sa.String(200), nullable=True),
        sa.Column("stability_study", sa.String(100), nullable=True),
        sa.Column("stability_timepoint", sa.String(50), nullable=True),
        sa.Column("stability_condition", sa.String(100), nullable=True),
        sa.Column("quantity", sa.String(32), nullable=True),
        sa.Column("quantity_unit", sa.String(16), nullable=True),
        sa.Column("container", sa.String(100), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("sampled_at", sa.DateTime(), nullable=True),
        sa.Column("sampled_by", sa.String(128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("received_by_id", sa.Integer(), nullable=True),
        sa.Column("receipt_condition", sa.String(64), nullable=True),
        sa.Column("receipt_temperature", sa.String(32), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("holder_id", sa.Integer(), nullable=True),
        sa.Column("disposed_at", sa.DateTime(), nullable=True),
        sa.Column("disposed_by_id", sa.Integer(), nullable=True),
        sa.Column("disposal_method", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sample_type_id"], ["sample_types.id"], name="fk_samples_sample_type_id_sample_types"),
        sa.ForeignKeyConstraint(["parent_id"], ["samples.id"], name="fk_samples_parent_id_samples"),
        sa.ForeignKeyConstraint(["received_by_id"], ["users.id"], name="fk_samples_received_by_id_users"),
        sa.ForeignKeyConstraint(["location_id"], ["storage_locations.id"], name="fk_samples_location_id_storage_locations"),
        sa.ForeignKeyConstraint(["holder_id"], ["users.id"], name="fk_samples_holder_id_users"),
        sa.ForeignKeyConstraint(["disposed_by_id"], ["users.id"], name="fk_samples_disposed_by_id_users"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_samples_created_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_samples"),
        sa.UniqueConstraint("sample_id", name="uq_samples_sample_id"),
    )
    op.create_index("ix_samples_parent_id", "samples", ["parent_id"])
    op.create_index("ix_samples_status", "samples", ["status"])
    op.create_index("ix_samples_location_id", "samples", ["location_id"])

    op.create_table(
        "custody_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("from_holder_id", sa.Integer(), nullable=True),
        sa.Column("to_holder_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], name="fk_custody_events_sample_id_samples"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_custody_events_user_id_users"),
        sa.ForeignKeyConstraint(
            ["from_location_id"], ["storage_locations.id"], name="fk_custody_events_from_location_id_storage_locations"
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"], ["storage_locations.id"], name="fk_custody_events_to_location_id_storage_locations"
        ),
        sa.ForeignKeyConstraint(["from_holder_id"], ["users.id"], name="fk_custody_events_from_holder_id_users"),
        sa.ForeignKeyConstraint(["to_holder_id"], ["users.id"], name="fk_custody_events_to_holder_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_custody_events"),
    )
    op.create_index("ix_custody_events_sample_id", "custody_events", ["sample_id"])

    op.create_table(
        "id_counters",
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "period", name="pk_id_counters"),
    )

    if op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER custody_events_no_{operation.lower()} "
                f"BEFORE {operation} ON custody_events "
                "BEGIN SELECT RAISE(ABORT, 'custody_events records are immutable'); END"
            )
        # Samples are never deleted; they are cancelled or disposed instead.
        op.execute(
            "CREATE TRIGGER samples_no_delete BEFORE DELETE ON samples "
            "BEGIN SELECT RAISE(ABORT, 'samples cannot be deleted'); END"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS samples_no_delete")
        op.execute("DROP TRIGGER IF EXISTS custody_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS custody_events_no_delete")
    op.drop_table("id_counters")
    op.drop_index("ix_custody_events_sample_id", table_name="custody_events")
    op.drop_table("custody_events")
    op.drop_index("ix_samples_location_id", table_name="samples")
    op.drop_index("ix_samples_status", table_name="samples")
    op.drop_index("ix_samples_parent_id", table_name="samples")
    op.drop_table("samples")
    op.drop_table("storage_locations")
    op.drop_table("sample_types")
