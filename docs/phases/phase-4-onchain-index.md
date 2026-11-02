# Phase 4: Modern plagiarism engine and the on-chain NFT database

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-4-onchain-index.md.
> Build the benchmark FIRST, then add each model behind the benchmark gate. GPU is 4 GB and the
> network is slow: batch, resume, run sequentially, log to backend/storage/. Update PROGRESS.md, commit.

## Goal
Replace the 2000s/2010s copy-detection techniques (pHash, ORB, block matching) with a 2022–2025
stack, and search **real NFTs on real chains** in milliseconds. Every component must **beat the
current one on the benchmark** before it ships (see "Model-selection rules" in CLAUDE.md).

## Step 1: copy-detection benchmark (`backend/benchmark/copydet.py`), built before any new model
- **Queries**: edited copies of protected works made with a richer attack set than v1:
  crop 5–40 %, rotate ±30°, flip, perspective, recolour/hue shift, blur, noise, JPEG q 30–95,
  text/logo/watermark overlay, border/frame, screenshot-style rescale, **collage** (copied work
  is 30–60 % of the canvas), **re-generation** (img2img at low strength, if feasible offline).
- **Distractors**: genuine non-copies (held-out paintings, NFT siblings from the same collection,
  photos), at a ratio of at least 10 distractors per positive.
- **Metrics**: micro-AP (DISC21 protocol), recall@1/@10, TPR at 1 % and 5 % FPR, reported separately
  for **paintings / one-off art** and **generative-collection NFTs**, plus p95 latency (CPU and GPU).
- **Baseline**: the current pHash + CLIP copy score + ORB. Store the results in `backend/model/benchmarks/`.

## Step 2: candidate models (each one: benchmark → keep only if it improves)
| Role | Candidate | Year | Notes |
|---|---|---|---|
| Fast exact / near-exact hash | **PDQ** (Meta, `pdqhash`) | 2019 | Industry standard (StopNCII); replaces pHash as the first-pass filter |
| Copy fingerprint (primary) | **SSCD** `sscd_disc_mixup` (TorchScript) | 2022 | Trained specifically for copy detection; 512-d; use its score normalisation |
| Copy fingerprint (fusion) | **DINOv2 ViT-S/14 or B/14** (try DINOv3-S if its licence fits) | 2023–25 | Strong under heavy edits; fuse with SSCD (max or learned weighting, tuned on validation) |
| Geometric / partial-copy verification | **ALIKED or SuperPoint + LightGlue** (`kornia` / `lightglue`) | 2023 | Replaces ORB; inlier count after LightGlue matching = verification score and evidence image |
| Collage / region copy | Region proposals (grid + saliency crops) embedded with SSCD | — | Catches a copied work inside a larger canvas |

**Licence check is mandatory before shipping** (record it in `docs/models.md`). Prefer permissive
weights: PDQ (BSD), DINOv2 (Apache-2.0), LightGlue (Apache-2.0), ALIKED (check). Treat SuperPoint
(non-commercial) and DINOv3 / SSCD weights (verify the current terms) as research-only unless confirmed.

## Step 3: a two-stage search pipeline
1. **Retrieve**: PDQ (exact) + ANN search on fused SSCD/DINOv2 embeddings → top-50.
2. **Verify**: LightGlue geometric matching on the top-10 → copy decision + evidence image.
3. **Density-relative scoring is kept**: per-reference nearest-neighbour similarity, and collection
   members counted only on exact/trait duplicates (the lesson from v1: 26 % false rejections otherwise).

## Step 4: the on-chain NFT database
- **Vector store**: pgvector HNSW (Phase 2 `reference_items`); switch to FAISS IVF-PQ only if the index
  exceeds ~2M vectors. Report index size, p50/p95 latency and recall@10 against exact search.
- **Ingestion** (`backend/ingest/`, Alchemy NFT API free tier, `ALCHEMY_API_KEY`): top ~50 collections
  on **Ethereum + Base** (≈ 200k tokens) using cached thumbnails; resumable per collection; store
  embeddings + 128 px thumbnails only. Respect rate limits.
- **Trait duplicates**: store token attributes; an identical trait set within a collection → duplicate.
- **Live sync**: poll `Transfer(from=0x0)` for tracked collections and Mint-Gate mints; new tokens are
  searchable within 10 minutes.

## Step 5: integration
- New policy checks `copy_exact` (PDQ), `copy_semantic` (fused embedding, density-relative) and
  `copy_geometric` (LightGlue inliers). They replace `phash`, `clip` and `orb`. Bump the policy major version;
  recalibrate (extend `training/calibrate.py`) and re-run `evaluate_system`.
- **CPU inference speed** for deployment: export the chosen models to **ONNX** (fp16/int8 where accuracy
  holds); target ≤ 1.5 s end-to-end on a 2-vCPU container.

## Acceptance criteria
- A benchmark table (baseline vs every candidate vs the final fusion) in `docs/models.md` and PROJECT_REPORT.
- The final engine beats the v1 baseline on micro-AP **and** TPR at 1 % FPR for paintings; collection-NFT
  false-positive rate does not increase.
- ≥ 200k on-chain NFTs indexed; search p95 < 100 ms; new mints searchable within 10 minutes.

## Owner prerequisites
- A free Alchemy account and API key in backend/.env as `ALCHEMY_API_KEY` (never pasted in chat).
