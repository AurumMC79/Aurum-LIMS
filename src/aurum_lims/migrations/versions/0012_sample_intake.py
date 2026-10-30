"""Sample intaking: versions of the laboratory's intake form, and what was entered for each sample.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

IMMUTABLE = ("intake_form_versions", "intake_submissions")


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], name=f"fk_{table}_{column}_{target.split('.')[0]}")


def upgrade() -> None:
    op.create_table(
        "intake_form_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fields", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        _fk("intake_form_versions", "created_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_form_versions"),
        sa.UniqueConstraint("version", name="uq_intake_form_versions_version"),
    )
    op.create_table(
        "intake_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("form_version_id", sa.Integer(), nullable=True),
        sa.Column("form_version", sa.Integer(), nullable=False),
        sa.Column("entries", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_by_id", sa.Integer(), nullable=False),
        _fk("intake_submissions", "sample_id", "samples.id"),
        _fk("intake_submissions", "form_version_id", "intake_form_versions.id"),
        _fk("intake_submissions", "submitted_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_submissions"),
    )
    op.create_index("ix_intake_submissions_sample_id", "intake_submissions", ["sample_id"])
    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
                )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    op.drop_table("intake_submissions")
    op.drop_table("intake_form_versions")
