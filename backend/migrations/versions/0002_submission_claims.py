"""Signed submissions: the file hash the artist's wallet signed when uploading (Phase 3).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("submissions", sa.Column("file_sha256", sa.String(64)))
    op.add_column("submissions", sa.Column("claim_message", sa.Text))
    op.add_column("submissions", sa.Column("claim_signature", sa.String(132)))
    op.create_index("ix_submissions_file_sha256", "submissions", ["file_sha256"])


def downgrade():
    op.drop_index("ix_submissions_file_sha256", table_name="submissions")
    op.drop_column("submissions", "claim_signature")
    op.drop_column("submissions", "claim_message")
    op.drop_column("submissions", "file_sha256")
