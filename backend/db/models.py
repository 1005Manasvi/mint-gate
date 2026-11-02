"""SQLAlchemy 2 models. The schema itself is owned by Alembic (`backend/migrations/`).

Hashes are lower-case hex without 0x (as in reports); addresses are EIP-55 checksummed;
uint256 values (token ids, nonces, bonds) are NUMERIC(78, 0).
"""

from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UINT256 = Numeric(78, 0)
EMBEDDING_DIM = 512  # CLIP ViT-B/32


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


class Dispute(Base):
    """Projection of DisputeOpened / DisputeResolved (the dispute UI is Phase 6)."""

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

    `phash` is the 64-bit pHash stored as a signed BIGINT; `embedding` is filled lazily by a
    worker when NULL (the indexer and the legacy migration do not load CLIP).
    """

    __tablename__ = "reference_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # mintgate | mintgate-v1 | (Phase 4: opensea, ...)
    chain_id: Mapped[int | None] = mapped_column(Integer)
    contract: Mapped[str | None] = mapped_column(String(42))
    token_id: Mapped[Decimal | None] = mapped_column(UINT256)
    image_uri: Mapped[str] = mapped_column(Text, unique=True)  # minted/<report_id>.png for our own mints
    phash: Mapped[int | None] = mapped_column(BigInteger)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index(
            "ix_reference_items_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
