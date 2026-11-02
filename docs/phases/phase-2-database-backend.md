# Phase 2: Database, job queue, and the backend on contract v2

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-2-database-backend.md.
> Use docker compose for Postgres/Redis locally. Write migrations and tests, update PROGRESS.md, commit.

## Goal
Replace JSON files and in-memory state with a real data layer, make analysis asynchronous, and
make the backend an **attestation issuer + chain indexer** for the Phase 1 contracts.

## Tasks
1. **Infrastructure**: `docker-compose.yml` with PostgreSQL 16 + **pgvector**, Redis 7. Config via `DATABASE_URL`, `REDIS_URL`.
   If Docker Desktop is not installed, install it (owner approval) or use the native Windows Postgres/Redis (Memurai).
2. **Schema** (SQLAlchemy 2 + Alembic migrations):
   - `artists(id, wallet, created_at)`
   - `submissions(id, artist_id, image_sha256, status, created_at)`
   - `reports(id, submission_id, report_hash, decision, score, policy_version, checks jsonb, evidence_uri)`
   - `attestations(id, report_id, chain_id, signature, nonce, expires_at, submitted_tx)`
   - `chain_events(chain_id, block, tx, log_index, event, args jsonb)`, unique (chain_id, tx, log_index)
   - `tokens(chain_id, contract, token_id, owner, image_sha256, status[active|flagged|revoked])`
   - `disputes(...)` (used in Phase 6)
   - `audit_log(actor, action, target, at, meta jsonb)`: append-only
   - `reference_items(id, source, chain_id, contract, token_id, image_uri, phash bigint, embedding vector)`: used in Phase 4
3. **Job queue**: Redis + RQ. `POST /api/analyze` returns `202 {job_id}`; `GET /api/jobs/<id>` for status;
   the worker runs the pipeline (models loaded once per worker). The frontend polls.
4. **Rate limiting**: move to Redis (Flask-Limiter with Redis storage).
5. **Attestation issuance**: after a PASS / REVIEW_APPROVED verdict, sign an EIP-712 `Verdict` (Phase 1
   `eip712.py`) bound to the **artist's wallet**; store it; return it to the client to submit on-chain.
   The server no longer mints and no longer pays gas for mints.
6. **Chain indexer** (`backend/indexer/`): polls `VerdictSubmitted`, `Minted`, `Dispute*` events on
   each configured network (block cursor stored in the DB, reorg-safe with N confirmations),
   upserts `chain_events` and `tokens`. Replaces the startup sync.
7. Migrate existing JSON reports with a one-off script.
8. Tests: repository-layer tests (testcontainers or a docker-compose test DB), job flow, indexer against a local hardhat node.

## Acceptance criteria
- A server restart loses nothing; two API instances + one worker run concurrently without conflicts.
- Analysis is async; the API p95 latency for submit is < 200 ms.
- The indexer reproduces on-chain state into Postgres from scratch (`--from-block`).
