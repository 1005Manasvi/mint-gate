# Mint-Gate: AI Image Forensics as a Gate Before NFT Minting

**Project report**: Manasvi Patil · November 2026

## 1. Problem

An NFT proves *ownership and provenance* of a token, not the originality of its artwork.
Plagiarised, edited, cloned or AI-generated images mint as easily as originals, and a smart
contract cannot inspect pixels. Mint-Gate runs image forensics **off-chain before minting**.
It uses the blockchain for what it does well: a public, tamper-evident record of every verdict,
plus an on-chain rule that refuses to mint without a passing one.

## 2. System

| Stage | Technique | Role in policy v4.0.0 |
|---|---|---|
| Preprocessing | format allow-list, decompression-bomb guard, RGB, SHA-256; *detector view* (same 256 px + JPEG normalisation as the training data) | — |
| AI detector | linear probe on frozen CLIP ViT-B/32 features (Ojha et al., CVPR 2023); spectral fingerprint kept as an informational signal | weight 0.40, can FAIL |
| ResNet-18 | ImageNet-pretrained, fully fine-tuned (GPU) on authentic vs AI + manipulated | weight 0.15, **REVIEW only** |
| Copy-move | 16×16 block matching, shift-vector voting; score = dominance of one displacement | weight 0.15 |
| `copy_semantic` (Phase 4) | SSCD + DINOv2-S fusion (+ 18 SSCD region crops on GPU), density-relative, vs one-off works | weight 0.20 |
| `copy_exact` (Phase 4) | PDQ 256-bit (8 dihedral hashes), exact in-memory search; collection members only when pixel-identical | weight 0.10 |

Each check's value maps to a risk (0.5 at REVIEW, 1.0 at FAIL). The score is
`100·(1 − Σ wᵢ·riskᵢ)`. Any FAIL-level check gives FAIL; any REVIEW-level check or a score
below 70 gives REVIEW.

The **protected collection** is a Postgres/pgvector index of 284,861 references: 269,816 Ethereum
NFTs from 48 collections (Phase 4), the 15,037-work seed collection (9,000 CryptoPunks, 3,347 items
from real NFT collections, 2,690 museum/WikiArt paintings), plus everything minted through Mint-Gate.
v1's pHash, CLIP copy score and ORB were replaced after a copy-detection benchmark (section 4.5).

## 3. Data

| Version | Content | Result |
|---|---|---|
| v0 (original) | 10k CryptoPunks vs 8.6k AI paintings | 99.75 % accuracy, but it learned "pixel art vs painting" (domain shortcut) |
| v1 | + ImageNet photos, 7 GenImage generators, museum art | 85 % accuracy; mostly photos and 2022-era generators, little NFT art |
| **v2 (final)** | rebalanced towards what is minted | below |

**Dataset v2 (20,309 images):**
- Authentic (8,885): 3,700 real-NFT-collection images (BAYC, MAYC, Azuki, Trippy Toadz,
  CryptoSkulls, nftrex, Dooggies; all pre-2022, so human-made), 2,985 paintings
  (WikiArt / museums), 1,000 CryptoPunks, 1,200 photos.
- AI (10,424): DALL·E 3, Midjourney and SD3 (1,500 each), SDXL (666), AI-art generators
  (3,458), older generators (300 each).

**Splits:** random 80/10/10. **Flux (1,000 images) is held out completely** as `test_unseen`.

**Bias control.** In GenImage the real images are JPEG and the fakes PNG ("Fake or JPEG?",
Grommelt et al. 2024). Every image in both classes is resized to a 256 px short side and
re-encoded as JPEG (quality 88–96), and uploads get the same treatment at inference.

**Augmentation.** 35 % of authentic training images also appear with a synthetic copy-move or
splice, labelled *forged*. Robustness augmentations: random JPEG (q 60–95), rescaling, blur.

**Training.** ResNet-18, all layers, AdamW + OneCycle (max LR 3e-4), label smoothing 0.05,
mixed precision, batch 48, 10 epochs on an RTX 3050 Laptop GPU (~100 s/epoch). The best epoch
is chosen by validation ROC-AUC. The AI detector is a logistic regression on CLIP embeddings,
with C and the ensemble weight chosen on validation; the spectral model's weight came out at 0.

## 4. Results

### 4.1 In-distribution test (n = 2,212) and unseen generator (Flux, n = 1,000)

| Metric | ResNet-18 | CLIP-probe AI detector |
|---|---|---|
| Accuracy | 91.1 % | **92.8 %** |
| ROC-AUC | 0.974 | **0.982** |
| Precision / recall (forged) | 95.3 % / 89.6 % | — |
| Flux (unseen) caught | 95.3 % | **97.9 %** (AUC vs genuine 0.990) |
| After JPEG q=70 | 91.6 % (AUC 0.970) | — |
| After 50 % downscale | 89.4 % (AUC 0.959) | — |

ResNet per group: DALL·E 3 100 %, SD3 100 %, Midjourney 97.9 %, SDXL 95.3 %, manipulated 94.1 %,
paintings 89.8 %, photos 77.5 %; weakest on old generators (ADM 61 %, VQDM 55 %).

### 4.2 Out-of-source check

The test split shares *sources* with the training data: the same NFT collections, and the same
Rapidata compression pipeline as the held-out Flux images. So we also evaluated on 480 artworks
from **4 NFT collections never used in training** and **200 Flux images from a different
publisher**:

| | Genuine NFTs correct | Flux caught | ROC-AUC |
|---|---|---|---|
| **CLIP-probe AI detector** | **100 %** | **94 %** | **0.9998** |
| ResNet-18 | 64 % (CyberKongz 43 %) | 77 % | 0.79 |

**Finding.** The fine-tuned CNN partly memorised the collections it saw. Frozen CLIP features
generalise to new art styles and generators, replicating Ojha et al. The policy was redesigned
accordingly: the CLIP probe makes the AI decision, and the ResNet can only request human review.

### 4.3 Calibration (policy v3.1.0)

| Check | REVIEW / FAIL | Genuine flagged | AUC |
|---|---|---|---|
| AI detector | 0.675 / 0.925 | 5.1 % / 1.0 % (val) | 0.982 |
| ResNet-18 (REVIEW only) | 0.517 / — | 5.1 % (val) | 0.974 |
| Copy-move | 3.8 / 6.0 | 5 % | 0.90 (64 % of clones caught at 5 % false alarms) |
| CLIP copy score (paintings) | 0.059 / 0.088 | 5 % | **0.91** (60 % of edited copies caught at REVIEW) |

![Calibration](assets/calibration.png)

*Figure (regenerated in Phase 4): policy v4.0.0 copy checks `copy_exact` / `copy_semantic` on the
benchmark test split; the v3.1.0 rows in the table above are history.*

**Finding: collection density.** Genuine items of a generative collection are near-identical to
existing ones: CryptoPunk nearest-neighbour similarity is 0.99, Bored-Ape-type collections 0.94,
paintings 0.88. Applying pHash/ORB to collection members rejected 26 % of genuine Bored Apes in
an intermediate version. Those checks now apply only to one-off artworks, and collection members
are protected against exact copies.

### 4.4 End-to-end evaluation, policy v3.1.0 (n = 50 per category; v4.0.0: section 4.5 and README)

![System evaluation](assets/system_evaluation.png)

| Upload | PASS | REVIEW | FAIL |
|---|---|---|---|
| Genuine NFT-collection art | 88 % | 12 % | 0 % |
| Genuine pixel-art NFT | 82 % | 18 % | 0 % |
| Genuine painting | 76 % | 12 % | 12 % (6.7 % at n = 120; 2 of 8 are dataset duplicates) |
| Genuine photo | 62 % | 38 % | 0 % |
| Modern AI (DALL·E 3, MJ, SD3, SDXL) | 0 % | 12 % | 88 % |
| Unseen Flux | 0 % | 24 % | 76 % |
| AI-art generators / older generators | 12 % / 16 % | 42 % / 52 % | 46 % / 32 % |
| Exact copy of a protected work | 0 % | 0 % | 100 % |
| Edited copy of a protected painting | 4 % | 6 % | 90 % |
| Copy-move / splice forgery | 2 % / 0 % | 20 % / 84 % | 78 % / 16 % |
| Edited copy of a generative-collection NFT | 94 % | 6 % | 0 % |

Summary:
- Genuine uploads: 77 % PASS, **3 % wrongly FAILed**.
- Forgeries: **85.8 % stopped**, 58.4 % rejected automatically.
- **No modern-AI or Flux image passes.**

### 4.5 Copy detection v4 (Phase 4): benchmark first, then models

Built before any model was swapped: 4,000 protected works, 600 attacked copies (14 attacks incl.
collage and SD 1.5 img2img re-generation), 5,967 genuine distractors (NFT siblings, paintings,
photos) with the same attacks; DISC21 micro-AP, TPR at fixed FPR; tuned on val, reported on test.
Every component passed a val gate (≥ 0.005 micro-AP) or was dropped; full table: `docs/models.md`.

| Paintings (test) | micro-AP | TPR @ 1 % FPR | Recall@1 |
|---|---|---|---|
| v1 engine (pHash + CLIP + ORB) | 0.820 | 0.542 | 0.938 |
| SSCD alone | 0.909 | 0.868 | 0.993 |
| **SSCD + DINOv2-S + region crops (shipped, GPU)** | **0.905** | **0.896** | 0.986 |

Decisions: DINOv2-S over -B (tie, 4× cheaper); ALIKED + LightGlue rejected (+0.3 pp as a check:
ORB retired without a replacement); int8 ONNX rejected (SSCD 20× slower, DINOv2 lost accuracy).
Region crops cost 3.6 s on a 2-vCPU box, so the CPU profile runs without them (p50 1.10 s, p95
1.35 s per upload; GPU p50 0.83 s).

**What the small benchmark could not show** (found by re-running `evaluate_system` against the
live 285k index, each fixed and re-measured):
1. One HNSW graph over 280k clustered NFTs returned a painting copy's source only 51 % of the time
   (exact search: 100 %): one-off works got their own partial HNSW index (100 %).
2. A member rule calibrated on 1,500 CryptoPunks (PDQ + SSCD cosine ≥ 0.978) failed 36 % of genuine
   new punks against 9,000+: members now count only when pixel-identical (128 px nearest RGB,
   2× margin below the closest genuine val sibling).
3. HNSW over PDQ bits missed 6 % of exact matches: PDQ is searched exactly in memory (9 MB).
4. The PDQ quality gate switched exact detection off for flat NFT art: re-uploads of indexed
   Ethereum NFTs passed 38 %; after the fixes 0 % pass (98 % FAIL).

Policy v4.0.0 on the benchmark test split: paintings 97.2 % of copies flagged / 2.2 % of genuine
(v1: 93.1 % / 5.0 %); generative-collection NFTs 0 % of genuine flagged (v1: 6.5 %).
End-to-end against the live index: 83 % of genuine uploads pass (v1 77 %), 3.5 % wrongly failed
(v1 3.0 %); exact re-uploads of indexed NFTs 98 % FAIL.

## 5. Blockchain layer

`MintGateNFT.sol`: ERC-721 + OpenZeppelin AccessControl, Solidity 0.8.24. Deployed on
**Ethereum Sepolia** at `0xae989b374DF85a942b4caE07940713Ff89418F32` (block 12,044,379);
source verified on Sourcify (exact match).

| Function | Role | Behaviour |
|---|---|---|
| `recordVerification` | VERIFIER | Stores every verdict immutably: report hash, image hash, score, decision, policy hash and version. Failures included. |
| `resolveReview` | REVIEWER | Human decision on REVIEW verdicts only. |
| `mintVerified` | VERIFIER | Reverts unless PASS / REVIEW_APPROVED. One token per report and one per image hash. |

- The artwork, ERC-721 metadata and full verification report are pinned to IPFS.
- `/api/verify` recomputes the report hash and compares it with the chain. A tampered report
  ("FAIL" edited to "PASS, 99") is detected, and the contract still refuses the mint.
- On start-up the server rebuilds its minted-artwork list from `VerifiedMint` events.
- Contract tests pass 7/7. The end-to-end flow (analyse → review → mint → verify → restart sync)
  was tested, and the production package was tested in production mode.

## 6. Limitations and future work

1. **Error rates.** About 5 % of genuine paintings are wrongly rejected, and 12–38 % of genuine
   uploads go to human review (photos most often). 14 % of forgeries pass, mainly older-generator
   images and edited copies of collection NFTs.
2. **Generative collections.** Edited copies of collection items are indistinguishable from new
   siblings by appearance alone; only pixel-identical copies are blocked (10.7 % of attacked NFT
   copies on the benchmark, 0 % genuine flagged). Trait hashes are stored, but an identical trait
   set means an identical image only in some collections (Pixelmon yes, Checks no).
   JPEG re-uploads of textured collection art usually exceed the pixel budget.
3. **Generators.** Coverage is 2024-era. New generators need periodic retraining; the
   out-of-source protocol in `training/independent_check.py` is designed for exactly that.
4. **Trust model.** The platform's verifier key mints on artists' behalf (sponsored gas).
   Production would use separate verifier/reviewer keys behind a multisig, and EIP-712
   attestations so artists can mint from their own wallets.
5. **Data licensing.** Some training data is non-commercial (CC BY-NC-SA, research-use), so the
   project is non-commercial.

## 7. Reproducibility

From `backend/`:
`training.fetch_realworld` → `training.fetch_v2` → `train_cnn --epochs 10` → `train_ai_detector`
→ `build_index` → `independent_check`. Copy engine (Phase 4): `benchmark.fetch_models` →
`benchmark.build_copydet` (+ `--regen`) → `benchmark.copydet features|crops|orb|lightglue|evaluate`
→ `benchmark.latency components` → `benchmark.copydet evaluate` → `calibrate --keep-detectors`;
on-chain index: `ingest fetch` → `ingest seed` → `ingest embed` → `ingest embed --rebuild-nn`;
then `evaluate_system --n 50`, `benchmark.latency index|pipeline|copy_recall`.

Outputs are in `backend/model/` (`model_meta.json`, `metrics.json`, `ai_detector.json`,
`system_eval.json`, `independent_check.json`) and `backend/policy.json`.
