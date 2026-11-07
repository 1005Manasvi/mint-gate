# Phase 5b: NFT-native evaluation data (real copymints, real AI collections)

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-5b-nft-datasets.md.
> Real on-chain data only where possible; honest evaluation (95 % CIs). Update docs, PROGRESS.md, commit.

## Why
Phase 5's evaluation sets are mostly *not* NFT-native: the AI test (OpenFake) is photo-realistic news
imagery, the forgery benchmark is edited photographs, and NFT copies are synthetic attacks. The gate
must be measured on what actually happens on NFT marketplaces: **copymint collections** and **AI-generated
collections**. Both live on-chain and can be ingested with the Phase 4 Alchemy pipeline (`backend/ingest/`).

## Step 1: real copymint collections (copy detection)
- Candidates: RR/BAYC (re-mints of Bored Apes), CryptoPhunks (horizontally mirrored CryptoPunks), PHAYC /
  Phunky Ape Yacht Club (mirrored apes), plus any other well-documented copycat collection found while
  researching. **Verify every contract address** with Alchemy `getContractMetadata` / `searchContractMetadata`
  and a public source (Etherscan / OpenSea page); never guess an address. Record chain + address.
- Ingest a sample (e.g. 300 tokens per collection, originals via Alchemy's CDN) into a separate evaluation
  set (`data/copymint/`), NOT into `reference_items` (they are forgeries, not protected works).
- Pair each copy with its original token (same token id for RR/BAYC; check the mapping per collection) and
  run the full pipeline: is the copy blocked, and does the report name the right original?
- Report per collection: FAIL / REVIEW / PASS with Wilson CIs, right-original rate, and *why* misses happen
  (mirroring vs the pixel-identity member rule, re-encoding, cropping). Mirrored members are a known gap:
  if they are missed, design and benchmark a fix (e.g. dihedral pixel-identity check for members) through
  the copy-detection gate (`benchmark/copydet.py`), without raising genuine collection false alarms (0 % today).

## Step 2: real AI-generated NFT collections (AI detection)
- Find Ethereum / Base collections whose creators **state** the art is AI-generated (collection description,
  website, contract metadata, OpenSea). Propose the list (name, chain, address, evidence link, date) to the
  owner before ingesting. Prefer 2023-2026 collections and several distinct generators / styles (pixel art,
  PFP characters, generative / abstract, anime).
- Collection-level labels are noisy: keep a per-collection evidence note, report per collection, and drop a
  collection if its claim is unclear.
- Exclude anything in the training data or the Phase 5 OOS set. Add the set as a new OOS group
  (`training/oos_data.py`, its own `select` / `report` split) and re-run `training.ai_backbones evaluate`
  (no retraining: this is evaluation), the shipped detector's per-collection detection rates with CIs, and
  the provenance check on the original files (do AI collections carry C2PA / metadata?).

## Step 3 (only if Step 2 yields < 300 images): self-generated NFT-style AI images
- Open models that fit the 4 GB GPU (SD 1.5 / SD 2.1 + permissive style LoRAs): pixel-art punks, PFP
  avatars, generative-art abstracts, anime avatars; ~1,000 images, prompts and seeds logged, licences recorded.
  Re-download SD 1.5 first (its cache was deleted after Phase 5). Evaluation only; held out of training.

## Step 4: re-measure end to end
- Add `copymint_*` and `ai_nft_collection` categories to `training/evaluate_system.py` (n = min(300, pool),
  without replacement) and re-run the affected categories (the script resumes per category).
- If a model or rule change is proposed, it must pass the existing gates (OOS protocol for AI,
  copy-detection benchmark for copies) and keep genuine-NFT false alarms at or below today's.

## Acceptance criteria
- `docs/models.md` and the README report copymint and AI-collection results per collection, with n and 95 % CIs,
  including the misses and their causes.
- Every collection used is listed with chain, address and the evidence for its label.
- PROGRESS.md updated; commit.
