# Phase 4: On-chain plagiarism database at scale

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-4-onchain-index.md.
> GPU is 4 GB and the network is slow: batch, resume, run sequentially, log to backend/storage/. Update PROGRESS.md, commit.

## Goal
Check uploads against **real NFTs on real chains**, robustly to edits, in milliseconds.
This fixes "it only knows 15k local images".

## Tasks
1. **Copy-detection model**: Meta **SSCD** (`sscd_disc_mixup`, TorchScript, ~100 MB) to replace
   CLIP/pHash/ORB as the primary copy signal. 512-d descriptors, cosine similarity, with SSCD's
   published score normalisation. Benchmark against the current pHash + CLIP + ORB on the existing
   edited-copy sets (paintings and NFTs). Keep ORB only for the visual evidence image.
2. **Vector store**: pgvector (HNSW index) in the Phase 2 `reference_items` table, or a FAISS IVF-PQ
   sidecar if it grows past ~2M items. Document the choice and the latency numbers.
3. **Ingestion** (`backend/ingest/`):
   - Alchemy NFT API (free tier; `ALCHEMY_API_KEY` in `.env`): `getNFTsForContract` with cached thumbnails
     for a curated list of top collections on **Ethereum mainnet and Base** (start with ~50, ≈ 200k tokens).
   - Image fetch via Alchemy's cached CDN or IPFS gateways with retry and timeouts; store embeddings, not images
     (keep 128 px thumbnails only for evidence display).
   - Resumable checkpoints per collection; respect rate limits.
4. **Live sync**: subscribe to (or poll) `Transfer(from=0x0)` mint events for tracked collections and new
   Mint-Gate mints; embed new tokens within minutes.
5. **Collection awareness**: store trait metadata. A new token whose trait combination exactly matches an
   existing token in the same collection is a **trait-level duplicate**. This fixes the "edited copy of a
   collection NFT" limitation.
6. Policy update: a `copy_sscd` check replaces `clip`/`phash`/`orb` in `policy.json` (bump the version);
   recalibrate with `training/calibrate.py` (extend it).
7. Report: index size, p50/p95 search latency, copy-detection AUC on the edited-copy benchmark
   (NFT and painting separately) before vs after.

## Acceptance criteria
- ≥ 200k real on-chain NFTs indexed; search p95 < 100 ms.
- Edited copies of indexed NFTs and paintings are caught at a materially higher rate than v1 (report the numbers).
- New mints in tracked collections are searchable within 10 minutes.

## Owner prerequisites
- A free Alchemy account and API key, put into backend/.env as `ALCHEMY_API_KEY` (never pasted in chat).
