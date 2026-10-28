"""Test review, result revisions, sample decisions and certificates.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

IMMUTABLE_TABLES = ("sample_decisions", "certificates")

# (table, column SQL) added with plain ALTER TABLE so existing triggers are kept.
NEW_COLUMNS = [
    ("analyses", "revision INTEGER NOT NULL DEFAULT 1"),
    ("analyses", "reviewed_at DATETIME"),
    ("analyses", "reviewed_by_id INTEGER CONSTRAINT fk_analyses_reviewed_by_id_users REFERENCES users (id)"),
    ("analyses", "review_signature_id INTEGER CONSTRAINT fk_analyses_review_signature_id_electronic_signatures "
                 "REFERENCES electronic_signatures (id)"),
    ("analyses", "review_comment TEXT"),
    ("reportable_results", "revision INTEGER NOT NULL DEFAULT 1"),
]


def upgrade() -> None:
    op.create_table(
        "sample_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("conforms", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), nullable=False),
        sa.Column("signature_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], name="fk_sample_decisions_sample_id_samples"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], name="fk_sample_decisions_decided_by_id_users"),
        sa.ForeignKeyConstraint(["signature_id"], ["electronic_signatures.id"],
                                name="fk_sample_decisions_signature_id_electronic_signatures"),
        sa.PrimaryKeyConstraint("id", name="pk_sample_decisions"),
    )
    op.create_index("ix_sample_decisions_sample_id", "sample_decisions", ["sample_id"])

    op.create_table(
        "certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("issued_by_id", sa.Integer(), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], name="fk_certificates_sample_id_samples"),
        sa.ForeignKeyConstraint(["decision_id"], ["sample_decisions.id"],
                                name="fk_certificates_decision_id_sample_decisions"),
        sa.ForeignKeyConstraint(["issued_by_id"], ["users.id"], name="fk_certificates_issued_by_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_certificates"),
        sa.UniqueConstraint("number", name="uq_certificates_number"),
    )
    op.create_index("ix_certificates_sample_id", "certificates", ["sample_id"])

    if op.get_bind().dialect.name == "sqlite":
        for table, column_sql in NEW_COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END"
                )
    else:
        op.add_column("analyses", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        op.add_column("analyses", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        op.add_column("analyses", sa.Column("reviewed_by_id", sa.Integer(), nullable=True))
        op.add_column("analyses", sa.Column("review_signature_id", sa.Integer(), nullable=True))
        op.add_column("analyses", sa.Column("review_comment", sa.Text(), nullable=True))
        op.add_column("reportable_results", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        op.create_foreign_key("fk_analyses_reviewed_by_id_users", "analyses", "users", ["reviewed_by_id"], ["id"])
        op.create_foreign_key("fk_analyses_review_signature_id_electronic_signatures", "analyses",
                              "electronic_signatures", ["review_signature_id"], ["id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{operation}")
    op.drop_table("certificates")
    op.drop_table("sample_decisions")
    with op.batch_alter_table("analyses") as batch:
        for column in ("review_comment", "review_signature_id", "reviewed_by_id", "reviewed_at", "revision"):
            batch.drop_column(column)
    with op.batch_alter_table("reportable_results") as batch:
        batch.drop_column("revision")
    if op.get_bind().dialect.name == "sqlite":
        # Batch mode rebuilds reportable_results, which drops its triggers; put them back.
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS reportable_results_no_{operation.lower()} "
                f"BEFORE {operation} ON reportable_results "
                "BEGIN SELECT RAISE(ABORT, 'reportable_results records are immutable'); END"
            )
