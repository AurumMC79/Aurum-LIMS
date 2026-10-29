"""SOP index (controlled documents and their PDF revisions) and bug reports.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], name=f"fk_{table}_{column}_{target.split('.')[0]}")


def upgrade() -> None:
    op.create_table(
        "controlled_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        # Points at document_revisions, which is created below; SQLite only checks it at write time.
        sa.Column("current_revision_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        _fk("controlled_documents", "parent_id", "controlled_documents.id"),
        sa.ForeignKeyConstraint(["current_revision_id"], ["document_revisions.id"],
                                name="fk_controlled_documents_current_revision_id"),
        _fk("controlled_documents", "created_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_controlled_documents"),
        sa.UniqueConstraint("number", name="uq_controlled_documents_number"),
    )
    op.create_index("ix_controlled_documents_parent_id", "controlled_documents", ["parent_id"])

    op.create_table(
        "document_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.String(16), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        _fk("document_revisions", "document_id", "controlled_documents.id"),
        _fk("document_revisions", "uploaded_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_document_revisions"),
    )
    op.create_index("ix_document_revisions_document_id", "document_revisions", ["document_id"])

    op.create_table(
        "bug_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("area", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("steps", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("screenshot", sa.LargeBinary(), nullable=True),
        sa.Column("app_version", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("reported_by_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        _fk("bug_reports", "reported_by_id", "users.id"),
        _fk("bug_reports", "updated_by_id", "users.id"),
        sa.PrimaryKeyConstraint("id", name="pk_bug_reports"),
        sa.UniqueConstraint("number", name="uq_bug_reports_number"),
    )
    op.create_index("ix_bug_reports_status", "bug_reports", ["status"])

    if op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER document_revisions_no_{operation.lower()} BEFORE {operation} ON document_revisions "
                "BEGIN SELECT RAISE(ABORT, 'document_revisions records are immutable'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS document_revisions_no_update")
        op.execute("DROP TRIGGER IF EXISTS document_revisions_no_delete")
    op.drop_table("bug_reports")
    op.drop_table("document_revisions")
    op.drop_table("controlled_documents")
