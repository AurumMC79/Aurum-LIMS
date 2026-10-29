"""Several screenshots per bug report (BUG-0004).

Existing single screenshots are moved into the new table, then the old
column is removed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bug_report_screenshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["bug_reports.id"], name="fk_bug_report_screenshots_report_id_bug_reports"),
        sa.PrimaryKeyConstraint("id", name="pk_bug_report_screenshots"),
    )
    op.create_index("ix_bug_report_screenshots_report_id", "bug_report_screenshots", ["report_id"])
    op.execute(
        "INSERT INTO bug_report_screenshots (report_id, position, image) "
        "SELECT id, 1, screenshot FROM bug_reports WHERE screenshot IS NOT NULL"
    )
    if op.get_bind().dialect.name == "sqlite":
        # Drop the column in place. Rebuilding the table (Alembic batch mode) would fail
        # here, because the screenshots just copied now reference bug_reports rows.
        op.execute("ALTER TABLE bug_reports DROP COLUMN screenshot")
    else:
        op.drop_column("bug_reports", "screenshot")


def downgrade() -> None:
    op.add_column("bug_reports", sa.Column("screenshot", sa.LargeBinary(), nullable=True))
    op.execute(
        "UPDATE bug_reports SET screenshot = (SELECT image FROM bug_report_screenshots s "
        "WHERE s.report_id = bug_reports.id ORDER BY position LIMIT 1)"
    )
    op.drop_index("ix_bug_report_screenshots_report_id", table_name="bug_report_screenshots")
    op.drop_table("bug_report_screenshots")
