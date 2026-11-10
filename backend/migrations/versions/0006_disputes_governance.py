"""Phase 6: dispute evidence, reasoned resolutions, Safe proposals, event timestamps.

* `dispute_evidence`: what a reporter submitted (pinned to IPFS) before sending `openDispute`;
  linked to the on-chain dispute by its evidence URI.
* `disputes`: + the reviewer's pinned reason (DisputeResolver.DisputeReasoned) and the backend's
  own evidence (copy search, earliest signed submission, reverse search).
* `safe_proposals`: governance Safe transactions waiting for owner signatures (EIP-712 SafeTx).
* `chain_events.block_time`: block timestamp, for timelines and the explorer.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UINT256 = sa.Numeric(78, 0)


def upgrade():
    op.add_column("chain_events", sa.Column("block_time", sa.BigInteger))
    op.create_table(
        "dispute_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("registry", sa.String(42), nullable=False),
        sa.Column("contract", sa.String(42), nullable=False),
        sa.Column("token_id", UINT256, nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("disputant", sa.String(42), nullable=False, index=True),
        sa.Column("evidence_uri", sa.Text, nullable=False, unique=True),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("doc", JSONB, nullable=False),
        sa.Column("original_file", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("disputes", sa.Column("evidence_id", sa.BigInteger, sa.ForeignKey("dispute_evidence.id")))
    op.add_column("disputes", sa.Column("reason_uri", sa.Text))
    op.add_column("disputes", sa.Column("reason_hash", sa.String(64)))
    op.add_column("disputes", sa.Column("backend_evidence", JSONB))
    op.add_column("disputes", sa.Column("backend_evidence_uri", sa.Text))
    op.create_table(
        "safe_proposals",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("safe", sa.String(42), nullable=False),
        sa.Column("safe_tx_hash", sa.String(66), nullable=False, unique=True),
        sa.Column("to", sa.String(42), nullable=False),
        sa.Column("value", UINT256, nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("operation", sa.SmallInteger, nullable=False),
        sa.Column("nonce", UINT256, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("signatures", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("executed_tx", sa.String(66)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'executed', 'replaced')", name="ck_safe_proposal_status"),
    )
    op.create_index("ix_safe_proposals_chain_status", "safe_proposals", ["chain_id", "status"])


def downgrade():
    op.drop_index("ix_safe_proposals_chain_status", table_name="safe_proposals")
    op.drop_table("safe_proposals")
    for col in ("backend_evidence_uri", "backend_evidence", "reason_hash", "reason_uri", "evidence_id"):
        op.drop_column("disputes", col)
    op.drop_table("dispute_evidence")
    op.drop_column("chain_events", "block_time")
