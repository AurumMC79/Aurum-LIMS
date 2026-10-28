"""Test methods, specifications, analyses, results and OOS investigations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _who(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], ["users.id"], name=f"fk_{table}_{column}_users")


def upgrade() -> None:
    op.create_table(
        "test_methods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sop_reference", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by_id", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        _who("test_methods", "created_by_id"),
        _who("test_methods", "activated_by_id"),
        sa.PrimaryKeyConstraint("id", name="pk_test_methods"),
        sa.UniqueConstraint("code", "version", name="uq_test_methods_code"),
    )
    op.create_index("ix_test_methods_code", "test_methods", ["code"])

    op.create_table(
        "method_parameters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("method_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("result_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=True),
        sa.Column("replicates", sa.Integer(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["method_id"], ["test_methods.id"], name="fk_method_parameters_method_id_test_methods"),
        sa.PrimaryKeyConstraint("id", name="pk_method_parameters"),
        sa.UniqueConstraint("method_id", "key", name="uq_method_parameters_method_id"),
    )
    op.create_index("ix_method_parameters_method_id", "method_parameters", ["method_id"])

    op.create_table(
        "specifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("material_name", sa.String(200), nullable=True),
        sa.Column("sample_type_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by_id", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sample_type_id"], ["sample_types.id"], name="fk_specifications_sample_type_id_sample_types"
        ),
        _who("specifications", "created_by_id"),
        _who("specifications", "activated_by_id"),
        sa.PrimaryKeyConstraint("id", name="pk_specifications"),
        sa.UniqueConstraint("code", "version", name="uq_specifications_code"),
    )
    op.create_index("ix_specifications_code", "specifications", ["code"])

    op.create_table(
        "spec_limits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("spec_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("method_code", sa.String(40), nullable=False),
        sa.Column("parameter_key", sa.String(40), nullable=False),
        sa.Column("limit_type", sa.String(16), nullable=False),
        sa.Column("low", sa.String(32), nullable=True),
        sa.Column("high", sa.String(32), nullable=True),
        sa.Column("expected_text", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["spec_id"], ["specifications.id"], name="fk_spec_limits_spec_id_specifications"),
        sa.PrimaryKeyConstraint("id", name="pk_spec_limits"),
        sa.UniqueConstraint("spec_id", "method_code", "parameter_key", name="uq_spec_limits_spec_id"),
    )
    op.create_index("ix_spec_limits_spec_id", "spec_limits", ["spec_id"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("method_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by_id", sa.Integer(), nullable=True),
        sa.Column("signature_id", sa.Integer(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], name="fk_analyses_sample_id_samples"),
        sa.ForeignKeyConstraint(["method_id"], ["test_methods.id"], name="fk_analyses_method_id_test_methods"),
        _who("analyses", "assigned_to_id"),
        _who("analyses", "created_by_id"),
        _who("analyses", "completed_by_id"),
        sa.ForeignKeyConstraint(
            ["signature_id"], ["electronic_signatures.id"], name="fk_analyses_signature_id_electronic_signatures"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analyses"),
    )
    op.create_index("ix_analyses_sample_id", "analyses", ["sample_id"])
    op.create_index("ix_analyses_status", "analyses", ["status"])

    op.create_table(
        "result_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("parameter_key", sa.String(40), nullable=False),
        sa.Column("replicate", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("entered_at", sa.DateTime(), nullable=False),
        sa.Column("entered_by_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], name="fk_result_values_analysis_id_analyses"),
        _who("result_values", "entered_by_id"),
        _who("result_values", "updated_by_id"),
        sa.PrimaryKeyConstraint("id", name="pk_result_values"),
        sa.UniqueConstraint("analysis_id", "parameter_key", "replicate", name="uq_result_values_analysis_id"),
    )
    op.create_index("ix_result_values_analysis_id", "result_values", ["analysis_id"])

    op.create_table(
        "reportable_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("parameter_key", sa.String(40), nullable=False),
        sa.Column("parameter_name", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("replicate_values", sa.Text(), nullable=True),
        sa.Column("mean", sa.String(64), nullable=True),
        sa.Column("rsd", sa.String(32), nullable=True),
        sa.Column("reported_value", sa.String(64), nullable=False),
        sa.Column("spec_text", sa.String(120), nullable=True),
        sa.Column("conformance", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], name="fk_reportable_results_analysis_id_analyses"),
        sa.PrimaryKeyConstraint("id", name="pk_reportable_results"),
    )
    op.create_index("ix_reportable_results_analysis_id", "reportable_results", ["analysis_id"])

    op.create_table(
        "oos_investigations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("parameter_key", sa.String(40), nullable=False),
        sa.Column("parameter_name", sa.String(100), nullable=False),
        sa.Column("result_value", sa.String(64), nullable=False),
        sa.Column("spec_text", sa.String(120), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("investigation_notes", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.String(64), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.Column("opened_by_id", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_by_id", sa.Integer(), nullable=True),
        sa.Column("signature_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], name="fk_oos_investigations_sample_id_samples"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], name="fk_oos_investigations_analysis_id_analyses"),
        _who("oos_investigations", "opened_by_id"),
        _who("oos_investigations", "closed_by_id"),
        sa.ForeignKeyConstraint(
            ["signature_id"], ["electronic_signatures.id"],
            name="fk_oos_investigations_signature_id_electronic_signatures",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oos_investigations"),
        sa.UniqueConstraint("number", name="uq_oos_investigations_number"),
    )
    op.create_index("ix_oos_investigations_sample_id", "oos_investigations", ["sample_id"])
    op.create_index("ix_oos_investigations_status", "oos_investigations", ["status"])

    if op.get_bind().dialect.name == "sqlite":
        # Plain ALTER TABLE keeps the samples table (and its no-delete trigger) intact;
        # SQLite allows a REFERENCES clause on an added column.
        op.execute(
            "ALTER TABLE samples ADD COLUMN specification_id INTEGER "
            "CONSTRAINT fk_samples_specification_id_specifications REFERENCES specifications (id)"
        )
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER reportable_results_no_{operation.lower()} "
                f"BEFORE {operation} ON reportable_results "
                "BEGIN SELECT RAISE(ABORT, 'reportable_results records are immutable'); END"
            )
    else:
        op.add_column("samples", sa.Column("specification_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_samples_specification_id_specifications", "samples", "specifications", ["specification_id"], ["id"]
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS reportable_results_no_update")
        op.execute("DROP TRIGGER IF EXISTS reportable_results_no_delete")
    with op.batch_alter_table("samples") as batch:
        batch.drop_column("specification_id")
    if op.get_bind().dialect.name == "sqlite":
        # Batch mode rebuilds the table, which drops its trigger; put it back.
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS samples_no_delete BEFORE DELETE ON samples "
            "BEGIN SELECT RAISE(ABORT, 'samples cannot be deleted'); END"
        )
    for table in ("oos_investigations", "reportable_results", "result_values", "analyses", "spec_limits",
                  "specifications", "method_parameters", "test_methods"):
        op.drop_table(table)
