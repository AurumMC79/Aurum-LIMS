"""Instrument data import: import templates per method, and the raw files imported.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], name=f"fk_{table}_{column}_{target.split('.')[0]}")


def upgrade() -> None:
    op.create_table(
        "import_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("method_code", sa.String(40), nullable=False),
        sa.Column("settings", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        _fk("import_templates", "created_by_id", "users.id"),
        _fk("import_templates", "updated_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_import_templates"),
        sa.UniqueConstraint("method_code", "name", name="uq_import_templates_method_code"),
    )
    op.create_index("ix_import_templates_method_code", "import_templates", ["method_code"])

    op.create_table(
        "imported_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("template_name", sa.String(100), nullable=False),
        sa.Column("settings", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("values_found", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("imported_by_id", sa.Integer(), nullable=False),
        _fk("imported_files", "analysis_id", "analyses.id"),
        _fk("imported_files", "template_id", "import_templates.id"),
        _fk("imported_files", "imported_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_imported_files"),
    )
    op.create_index("ix_imported_files_analysis_id", "imported_files", ["analysis_id"])

    if op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER imported_files_no_{operation.lower()} BEFORE {operation} ON imported_files "
                "BEGIN SELECT RAISE(ABORT, 'imported_files records are immutable'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS imported_files_no_update")
        op.execute("DROP TRIGGER IF EXISTS imported_files_no_delete")
    op.drop_table("imported_files")
    op.drop_table("import_templates")
