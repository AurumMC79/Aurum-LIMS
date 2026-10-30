"""Amended certificates: a re-issued certificate supersedes the earlier one.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-29
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: certificates is protected by triggers, which a
    # batch-mode table rebuild would drop.
    op.execute("ALTER TABLE certificates ADD COLUMN supersedes_id INTEGER REFERENCES certificates (id)")
    op.execute("ALTER TABLE certificates ADD COLUMN amendment INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE certificates ADD COLUMN amendment_reason TEXT")
    # Why a decided sample was reopened; printed on the amended certificate.
    op.execute("ALTER TABLE samples ADD COLUMN amendment_reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE samples DROP COLUMN amendment_reason")
    op.execute("ALTER TABLE certificates DROP COLUMN amendment_reason")
    op.execute("ALTER TABLE certificates DROP COLUMN amendment")
    op.execute("ALTER TABLE certificates DROP COLUMN supersedes_id")
