"""SQLAlchemy 2 models. The schema itself is owned by Alembic (`backend/migrations/`).

Hashes are lower-case hex without 0x (as in reports); addresses are EIP-55 checksummed;
uint256 values (token ids, nonces, bonds) are NUMERIC(78, 0).
"""

from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import BIT, HALFVEC
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    false as sa_false,
)
from sqlalchemy import (
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UINT256 = Numeric(78, 0)
SSCD_DIM = 512  # SSCD sscd_disc_mixup
DINO_DIM = 384  # DINOv2 ViT-S/14 (migration 0004)


class Base(DeclarativeBase):
    pass


def _created():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Artist(Base):
    __tablename__ = "artists"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet: Mapped[str] = mapped_column(String(42), unique=True)
    created_at: Mapped[datetime] = _created()


class Submission(Base):
    """One upload. Its id is also the RQ job id."""

    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("artists.id"), index=True)  # NULL: legacy v1
    image_sha256: Mapped[str | None] = mapped_column(String(64), index=True)  # set by the worker
    filename: Mapped[str] = mapped_column(String(200), default="")
    # Signed claim (Phase 3): SHA-256 of the uploaded bytes + the personal_sign message and signature
    # of the session wallet. NULL for submissions made before wallet sign-in existed.
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    claim_message: Mapped[str | None] = mapped_column(Text)
    claim_signature: Mapped[str | None] = mapped_column(String(132))
    # queued | processing | invalid | error | PASS | REVIEW | FAIL | REVIEW_APPROVED | REVIEW_REJECTED | MINTED
    status: Mapped[str] = mapped_column(String(20), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    artist: Mapped[Artist | None] = relationship(lazy="joined")
    report: Mapped["Report | None"] = relationship(back_populates="submission", uselist=False)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # report_id
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), unique=True)
    report_hash: Mapped[str] = mapped_column(String(64), unique=True)
    decision: Mapped[str] = mapped_column(String(20))  # the pipeline's verdict (never changes)
    score: Mapped[int] = mapped_column(SmallInteger)
    policy_version: Mapped[str] = mapped_column(String(32))
    checks: Mapped[dict] = mapped_column(JSONB)
    body: Mapped[dict] = mapped_column(JSONB)  # the rest of the public report (without checks / evidence)
    evidence_uri: Mapped[str | None] = mapped_column(Text)
    review: Mapped[dict | None] = mapped_column(JSONB)
    publication: Mapped[dict | None] = mapped_column(JSONB)  # token URI + links, created at attestation
    created_at: Mapped[datetime] = _created()

    submission: Mapped[Submission] = relationship(back_populates="report")
    attestations: Mapped[list["Attestation"]] = relationship(back_populates="report", order_by="Attestation.id")


class Attestation(Base):
    """A signed EIP-712 Verdict. PASS: handed to the artist; REVIEW / FAIL: submitted by the backend."""

    __tablename__ = "attestations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), index=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    registry_address: Mapped[str] = mapped_column("registry", String(42))
    artist: Mapped[str] = mapped_column(String(42))
    verdict: Mapped[dict] = mapped_column(JSONB)  # Verdict.to_json()
    signature: Mapped[str] = mapped_column(String(132))
    nonce: Mapped[Decimal] = mapped_column(UINT256)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_tx: Mapped[str | None] = mapped_column(String(66))
    created_at: Mapped[datetime] = _created()

    report: Mapped[Report] = relationship(back_populates="attestations")
    __table_args__ = (UniqueConstraint("chain_id", "registry", "nonce", name="uq_attestation_nonce"),)


class IndexerCursor(Base):
    """Last block whose events are in `chain_events`, per chain (with its hash, to detect reorgs)."""

    __tablename__ = "indexer_cursors"
    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    network: Mapped[str] = mapped_column(String(32))
    block: Mapped[int] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChainEvent(Base):
    __tablename__ = "chain_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    block: Mapped[int] = mapped_column(BigInteger)
    block_hash: Mapped[str] = mapped_column(String(66))
    tx: Mapped[str] = mapped_column(String(66))
    log_index: Mapped[int] = mapped_column(Integer)
    contract: Mapped[str] = mapped_column(String(42))
    event: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSONB)
    block_time: Mapped[int | None] = mapped_column(BigInteger)  # migration 0006

    __table_args__ = (
        UniqueConstraint("chain_id", "tx", "log_index", name="uq_chain_event"),
        Index("ix_chain_events_block", "chain_id", "block", "log_index"),
        Index("ix_chain_events_event", "chain_id", "event"),
    )


class Token(Base):
    """Projection of chain_events: one row per minted token (any MintGated collection)."""

    __tablename__ = "tokens"
    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract: Mapped[str] = mapped_column(String(42), primary_key=True)
    token_id: Mapped[Decimal] = mapped_column(UINT256, primary_key=True)
    owner: Mapped[str] = mapped_column(String(42), index=True)
    artist: Mapped[str] = mapped_column(String(42), index=True)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    report_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))  # active | flagged | revoked
    minted_block: Mapped[int] = mapped_column(BigInteger)
    minted_tx: Mapped[str] = mapped_column(String(66))


class DisputeEvidence(Base):
    """What a reporter submitted (pinned) before sending `openDispute`; matched by `evidence_uri`."""

    __tablename__ = "dispute_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    registry_address: Mapped[str] = mapped_column("registry", String(42))
    contract: Mapped[str] = mapped_column(String(42))
    token_id: Mapped[Decimal] = mapped_column(UINT256)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    disputant: Mapped[str] = mapped_column(String(42), index=True)
    evidence_uri: Mapped[str] = mapped_column(Text, unique=True)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    doc: Mapped[dict] = mapped_column(JSONB)
    original_file: Mapped[str | None] = mapped_column(Text)  # storage-relative path of an uploaded original
    created_at: Mapped[datetime] = _created()


class SafeProposal(Base):
    """A governance Safe transaction collecting owner signatures (EIP-712 SafeTx, Safe v1.4.1)."""

    __tablename__ = "safe_proposals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    safe: Mapped[str] = mapped_column(String(42))
    safe_tx_hash: Mapped[str] = mapped_column(String(66), unique=True)
    to: Mapped[str] = mapped_column(String(42))
    value: Mapped[Decimal] = mapped_column(UINT256)
    data: Mapped[str] = mapped_column(Text)
    operation: Mapped[int] = mapped_column(SmallInteger)
    nonce: Mapped[Decimal] = mapped_column(UINT256)
    kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    signatures: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # owner -> 0x sig
    status: Mapped[str] = mapped_column(String(12), server_default="pending")  # pending | executed | replaced
    executed_tx: Mapped[str | None] = mapped_column(String(66))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (Index("ix_safe_proposals_chain_status", "chain_id", "status"),)


class Dispute(Base):
    """Projection of DisputeOpened / DisputeResolved / DisputeReasoned (+ the backend's evidence)."""

    __tablename__ = "disputes"
    chain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registry_address: Mapped[str] = mapped_column("registry", String(42), primary_key=True)
    dispute_id: Mapped[Decimal] = mapped_column(UINT256, primary_key=True)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    disputant: Mapped[str] = mapped_column(String(42))
    bond: Mapped[Decimal] = mapped_column(UINT256)
    evidence_uri: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10))  # open | upheld | rejected
    opened_block: Mapped[int] = mapped_column(BigInteger)
    opened_tx: Mapped[str] = mapped_column(String(66))
    resolved_block: Mapped[int | None] = mapped_column(BigInteger)
    resolved_tx: Mapped[str | None] = mapped_column(String(66))
    reviewer: Mapped[str | None] = mapped_column(String(42))
    # migration 0006
    evidence_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("dispute_evidence.id"))
    reason_uri: Mapped[str | None] = mapped_column(Text)
    reason_hash: Mapped[str | None] = mapped_column(String(64))
    backend_evidence: Mapped[dict | None] = mapped_column(JSONB)
    backend_evidence_uri: Mapped[str | None] = mapped_column(Text)

    evidence: Mapped[DisputeEvidence | None] = relationship(lazy="joined")


class AuditLog(Base):
    """Append-only (a trigger rejects UPDATE and DELETE)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(128), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReferenceItem(Base):
    """Artwork new uploads are compared against. Phase 2: Mint-Gate mints; Phase 4: on-chain NFTs.

    Phase 4 copy-engine fingerprint (filled while status = pending -> ready by an analysis worker
    for Mint-Gate mints, by `python -m ingest embed` for everything else): PDQ bit(256), SSCD and
    DINOv2 halfvecs, `copy_vec` = the fused ANN vector, `nn_*` = similarity to the reference's own
    nearest neighbour (density-relative scoring). `collection_member`: generative-collection item.
    """

    __tablename__ = "reference_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # mintgate | mintgate-v1 | (Phase 4: opensea, ...)
    chain_id: Mapped[int | None] = mapped_column(Integer)
    contract: Mapped[str | None] = mapped_column(String(42))
    token_id: Mapped[Decimal | None] = mapped_column(UINT256)
    image_uri: Mapped[str] = mapped_column(Text, unique=True)  # minted/<report_id>.png for our own mints
    created_at: Mapped[datetime] = _created()
    # Phase 4: on-chain NFTs (source "alchemy"). image_uri = nft/<chain>/<contract>/<token>.jpg (128 px thumb).
    collection_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("nft_collections.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[list | None] = mapped_column(JSONB)
    trait_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256 of the canonical trait set
    status: Mapped[str] = mapped_column(String(12), server_default="ready")  # pending | ready | failed
    pdq = mapped_column(BIT(256), nullable=True)
    pdq_quality: Mapped[int | None] = mapped_column(SmallInteger)
    sscd = mapped_column(HALFVEC(SSCD_DIM), nullable=True)
    dino = mapped_column(HALFVEC(DINO_DIM), nullable=True)
    copy_vec = mapped_column(HALFVEC(SSCD_DIM + DINO_DIM), nullable=True)
    nn_sscd: Mapped[float | None] = mapped_column(REAL)
    nn_dino: Mapped[float | None] = mapped_column(REAL)
    collection_member: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())

    __table_args__ = (
        Index("ix_reference_items_pdq", "pdq", postgresql_using="hnsw", postgresql_ops={"pdq": "bit_hamming_ops"}),
        Index("ix_reference_items_sscd", "sscd", postgresql_using="hnsw", postgresql_ops={"sscd": "halfvec_ip_ops"}),
        Index(
            "ix_reference_items_copy_vec",
            "copy_vec",
            postgresql_using="hnsw",
            postgresql_ops={"copy_vec": "halfvec_ip_ops"},
        ),
        # migration 0005: one-off works get their own graphs (HNSW recall in the shared graph: 51 %)
        Index(
            "ix_reference_items_copy_vec_oneoff",
            "copy_vec",
            postgresql_using="hnsw",
            postgresql_ops={"copy_vec": "halfvec_ip_ops"},
            postgresql_where=text("NOT collection_member"),
        ),
        Index(
            "ix_reference_items_sscd_oneoff",
            "sscd",
            postgresql_using="hnsw",
            postgresql_ops={"sscd": "halfvec_ip_ops"},
            postgresql_where=text("NOT collection_member"),
        ),
        Index("ix_reference_items_collection_trait", "collection_id", "trait_hash"),
        Index("ix_reference_items_status", "status"),
        Index("uq_reference_items_token", "chain_id", "contract", "token_id", "source", unique=True),
    )


class NftCollection(Base):
    """A tracked on-chain collection: backfill cursor (Alchemy pageKey) + live-sync block."""

    __tablename__ = "nft_collections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    contract: Mapped[str] = mapped_column(String(42))
    name: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str | None] = mapped_column(String(16))
    total_supply: Mapped[Decimal | None] = mapped_column(UINT256)
    cap: Mapped[int] = mapped_column(Integer)
    page_key: Mapped[str | None] = mapped_column(Text)
    ingested: Mapped[int] = mapped_column(Integer, server_default="0")
    backfill_done: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())
    sync_block: Mapped[int | None] = mapped_column(BigInteger)
    tracked: Mapped[bool] = mapped_column(Boolean, server_default=sa_true())
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("chain_id", "contract", name="uq_nft_collections_chain_contract"),)
