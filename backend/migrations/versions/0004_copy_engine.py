"""Copy engine fingerprints (Phase 4): PDQ + SSCD (+ DINOv2) replace pHash + CLIP on reference_items.

Vectors are halfvec (fp16: half the size, same ranking); search is HNSW by inner product (the
vectors are L2-normalised). PDQ is bit(256) with an HNSW Hamming index. `nn_*` = similarity of a
reference to its own nearest neighbour (density-relative scoring). Existing rows become pending,
so workers / `python -m ingest embed` fingerprint them with the new engine.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import BIT, HALFVEC, Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SSCD_DIM = 512
DINO_DIM = 384  # DINOv2 ViT-S/14
COPY_DIM = SSCD_DIM + DINO_DIM


def upgrade():
    op.drop_index("ix_reference_items_embedding", table_name="reference_items")
    op.drop_column("reference_items", "embedding")
    op.drop_column("reference_items", "phash")
    op.add_column("reference_items", sa.Column("pdq", BIT(256)))
    op.add_column("reference_items", sa.Column("pdq_quality", sa.SmallInteger))
    op.add_column("reference_items", sa.Column("sscd", HALFVEC(SSCD_DIM)))
    op.add_column("reference_items", sa.Column("dino", HALFVEC(DINO_DIM)))
    op.add_column("reference_items", sa.Column("copy_vec", HALFVEC(COPY_DIM)))
    op.add_column("reference_items", sa.Column("nn_sscd", sa.REAL))
    op.add_column("reference_items", sa.Column("nn_dino", sa.REAL))
    op.add_column(
        "reference_items", sa.Column("collection_member", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.execute("UPDATE reference_items SET collection_member = true WHERE collection_id IS NOT NULL")
    op.execute("UPDATE reference_items SET status = 'pending' WHERE status = 'ready'")
    op.execute("CREATE INDEX ix_reference_items_pdq ON reference_items USING hnsw (pdq bit_hamming_ops)")
    op.execute("CREATE INDEX ix_reference_items_sscd ON reference_items USING hnsw (sscd halfvec_ip_ops)")
    op.execute("CREATE INDEX ix_reference_items_copy_vec ON reference_items USING hnsw (copy_vec halfvec_ip_ops)")


def downgrade():
    for ix in ("ix_reference_items_copy_vec", "ix_reference_items_sscd", "ix_reference_items_pdq"):
        op.drop_index(ix, table_name="reference_items")
    for col in ("collection_member", "nn_dino", "nn_sscd", "copy_vec", "dino", "sscd", "pdq_quality", "pdq"):
        op.drop_column("reference_items", col)
    op.add_column("reference_items", sa.Column("phash", sa.BigInteger))
    op.add_column("reference_items", sa.Column("embedding", Vector(512)))
    op.execute("CREATE INDEX ix_reference_items_embedding ON reference_items USING hnsw (embedding vector_cosine_ops)")
    op.execute("UPDATE reference_items SET status = 'pending'")
