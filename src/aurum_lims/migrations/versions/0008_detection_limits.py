"""Limits of detection and quantitation (LOD / LOQ) for number results.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: reportable_results is protected by triggers, which a
    # batch-mode table rebuild would drop.
    for table in ("method_parameters", "reportable_results"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN lod VARCHAR(32)")
        op.execute(f"ALTER TABLE {table} ADD COLUMN loq VARCHAR(32)")


def downgrade() -> None:
    for table in ("method_parameters", "reportable_results"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN loq")
        op.execute(f"ALTER TABLE {table} DROP COLUMN lod")
