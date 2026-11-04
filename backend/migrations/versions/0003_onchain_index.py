"""On-chain NFT index: tracked collections + token metadata on reference_items (Phase 4).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade():
    op.create_table(
        "nft_collections",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chain_id", sa.Integer, nullable=False),
        sa.Column("contract", sa.String(42), nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("token_type", sa.String(16)),
        sa.Column("total_supply", sa.Numeric(78, 0)),
        sa.Column("cap", sa.Integer, nullable=False),
        sa.Column("page_key", sa.Text),  # Alchemy pagination cursor: ingestion resumes here
        sa.Column("ingested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("backfill_done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sync_block", sa.BigInteger),  # live sync: last block scanned for Transfer(from=0)
        sa.Column("tracked", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("chain_id", "contract", name="uq_nft_collections_chain_contract"),
    )
    op.add_column("reference_items", sa.Column("collection_id", sa.BigInteger, sa.ForeignKey("nft_collections.id")))
    op.add_column("reference_items", sa.Column("source_url", sa.Text))
    op.add_column("reference_items", sa.Column("attributes", JSONB))
    op.add_column("reference_items", sa.Column("trait_hash", sa.String(64)))
    # pending = image fetched, fingerprint missing; ready = searchable; failed = image unusable
    op.add_column("reference_items", sa.Column("status", sa.String(12), nullable=False, server_default="ready"))
    op.create_index("ix_reference_items_collection_trait", "reference_items", ["collection_id", "trait_hash"])
    op.create_index("ix_reference_items_status", "reference_items", ["status"])
    op.create_index(
        "uq_reference_items_token",
        "reference_items",
        ["chain_id", "contract", "token_id", "source"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_reference_items_token", table_name="reference_items")
    op.drop_index("ix_reference_items_status", table_name="reference_items")
    op.drop_index("ix_reference_items_collection_trait", table_name="reference_items")
    for col in ("status", "trait_hash", "attributes", "source_url", "collection_id"):
        op.drop_column("reference_items", col)
    op.drop_table("nft_collections")
