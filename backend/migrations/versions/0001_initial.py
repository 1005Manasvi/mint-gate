"""Initial schema: artists, submissions, reports, attestations, chain index, audit log, references.

Revision ID: 0001
Revises:
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UINT256 = sa.Numeric(78, 0)
NOW = sa.text("now()")


def _ts(name, nullable=False, default=True):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW if default else None, nullable=nullable)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "artists",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("wallet", sa.String(42), nullable=False, unique=True),
        _ts("created_at"),
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("artist_id", sa.BigInteger, sa.ForeignKey("artists.id"), index=True),
        sa.Column("image_sha256", sa.String(64), index=True),
        sa.Column("filename", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("error", sa.Text),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("submission_id", sa.String(32), sa.ForeignKey("submissions.id"), nullable=False, unique=True),
        sa.Column("report_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("score", sa.SmallInteger, nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("checks", JSONB, nullable=False),
        sa.Column("body", JSONB, nullable=False),
        sa.Column("evidence_uri", sa.Text),
        sa.Column("review", JSONB),
        sa.Column("publication", JSONB),
        _ts("created_at"),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_report_score"),
    )
    op.create_table(
        "attestations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("report_id", sa.String(32), sa.ForeignKey("reports.id"), nullable=False, index=True),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("registry", sa.String(42), nullable=False),
        sa.Column("artist", sa.String(42), nullable=False),
        sa.Column("verdict", JSONB, nullable=False),
        sa.Column("signature", sa.String(132), nullable=False),
        sa.Column("nonce", UINT256, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_tx", sa.String(66)),
        _ts("created_at"),
        sa.UniqueConstraint("chain_id", "registry", "nonce", name="uq_attestation_nonce"),
    )
    op.create_table(
        "indexer_cursors",
        sa.Column("chain_id", sa.Integer, primary_key=True),
        sa.Column("network", sa.String(32), nullable=False),
        sa.Column("block", sa.BigInteger, nullable=False),
        sa.Column("block_hash", sa.String(66)),
        _ts("updated_at"),
    )
    op.create_table(
        "chain_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("block", sa.BigInteger, nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("tx", sa.String(66), nullable=False),
        sa.Column("log_index", sa.Integer, nullable=False),
        sa.Column("contract", sa.String(42), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("args", JSONB, nullable=False),
        sa.UniqueConstraint("chain_id", "tx", "log_index", name="uq_chain_event"),
    )
    op.create_index("ix_chain_events_block", "chain_events", ["chain_id", "block", "log_index"])
    op.create_index("ix_chain_events_event", "chain_events", ["chain_id", "event"])
    op.create_table(
        "tokens",
        sa.Column("chain_id", sa.Integer, primary_key=True),
        sa.Column("contract", sa.String(42), primary_key=True),
        sa.Column("token_id", UINT256, primary_key=True),
        sa.Column("owner", sa.String(42), nullable=False, index=True),
        sa.Column("artist", sa.String(42), nullable=False, index=True),
        sa.Column("image_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("minted_block", sa.BigInteger, nullable=False),
        sa.Column("minted_tx", sa.String(66), nullable=False),
        sa.CheckConstraint("status IN ('active', 'flagged', 'revoked')", name="ck_token_status"),
    )
    op.create_table(
        "disputes",
        sa.Column("chain_id", sa.Integer, primary_key=True),
        sa.Column("registry", sa.String(42), primary_key=True),
        sa.Column("dispute_id", UINT256, primary_key=True),
        sa.Column("image_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("disputant", sa.String(42), nullable=False),
        sa.Column("bond", UINT256, nullable=False),
        sa.Column("evidence_uri", sa.Text, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("opened_block", sa.BigInteger, nullable=False),
        sa.Column("opened_tx", sa.String(66), nullable=False),
        sa.Column("resolved_block", sa.BigInteger),
        sa.Column("resolved_tx", sa.String(66)),
        sa.Column("reviewer", sa.String(42)),
        sa.CheckConstraint("status IN ('open', 'upheld', 'rejected')", name="ck_dispute_status"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False, index=True),
        sa.Column("target", sa.String(128), index=True),
        _ts("at"),
        sa.Column("meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    # Append-only: rows can be inserted, never changed or removed.
    op.execute(
        """
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP;
        END $$;
        CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        """
    )
    op.create_table(
        "reference_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("chain_id", sa.Integer),
        sa.Column("contract", sa.String(42)),
        sa.Column("token_id", UINT256),
        sa.Column("image_uri", sa.Text, nullable=False, unique=True),
        sa.Column("phash", sa.BigInteger),
        sa.Column("embedding", Vector(512)),
        _ts("created_at"),
    )
    # Cosine ANN search (used from Phase 4 on, when the table holds ~200k on-chain NFTs).
    op.execute("CREATE INDEX ix_reference_items_embedding ON reference_items USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    for table in (
        "reference_items",
        "audit_log",
        "disputes",
        "tokens",
        "chain_events",
        "indexer_cursors",
        "attestations",
        "reports",
        "submissions",
        "artists",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
