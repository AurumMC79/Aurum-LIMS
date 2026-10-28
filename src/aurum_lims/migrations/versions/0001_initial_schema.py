"""Initial schema: users, password history, audit trail, e-signatures, settings.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables whose rows may never be changed or removed once written.
IMMUTABLE_TABLES = ("audit_entries", "electronic_signatures")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], name="fk_users_created_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "password_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_password_history_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_password_history"),
    )
    op.create_index("ix_password_history_user_id", "password_history", ["user_id"])

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp_utc", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("old_values", sa.Text(), nullable=True),
        sa.Column("new_values", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_audit_entries_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_entries"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_audit_entries_timestamp_utc", "audit_entries", ["timestamp_utc"])
    op.create_index("ix_audit_entries_action", "audit_entries", ["action"])

    op.create_table(
        "electronic_signatures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("meaning", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=False),
        sa.Column("audit_entry_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_electronic_signatures_user_id_users"),
        sa.ForeignKeyConstraint(
            ["audit_entry_id"], ["audit_entries.id"], name="fk_electronic_signatures_audit_entry_id_audit_entries"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_electronic_signatures"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], name="fk_app_settings_updated_by_id_users"),
        sa.PrimaryKeyConstraint("key", name="pk_app_settings"),
    )

    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{operation.lower()} "
                    f"BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
                )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    op.drop_table("app_settings")
    op.drop_table("electronic_signatures")
    op.drop_index("ix_audit_entries_action", table_name="audit_entries")
    op.drop_index("ix_audit_entries_timestamp_utc", table_name="audit_entries")
    op.drop_table("audit_entries")
    op.drop_index("ix_password_history_user_id", table_name="password_history")
    op.drop_table("password_history")
    op.drop_table("users")
