"""Partial HNSW indexes for one-off works (copy_semantic search).

With ~280k clustered collection items in one HNSW graph, the 2.7k paintings form a sparse region the
greedy search rarely reaches: a painting copy's source was in the HNSW top-50 only 51 % of the time
(exact search: 100 %). One-off works get their own small graphs; collection members only count as
exact copies (PDQ + pixel check), so their global graphs are only used for display and PDQ lookups.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_reference_items_copy_vec_oneoff ON reference_items "
        "USING hnsw (copy_vec halfvec_ip_ops) WHERE NOT collection_member"
    )
    op.execute(
        "CREATE INDEX ix_reference_items_sscd_oneoff ON reference_items "
        "USING hnsw (sscd halfvec_ip_ops) WHERE NOT collection_member"
    )


def downgrade():
    op.drop_index("ix_reference_items_sscd_oneoff", table_name="reference_items")
    op.drop_index("ix_reference_items_copy_vec_oneoff", table_name="reference_items")
