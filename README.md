# Mint-Gate: AI forensics that verifies artwork *before* it becomes an  NFT

[![CI](https://github.com/1005Manasvi/mint-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/1005Manasvi/mint-gate/actions/workflows/ci.yml)
[![Contract coverage](https://img.shields.io/badge/contract%20coverage-100%25-brightgreen)](docs/security.md)
[![Slither](https://img.shields.io/badge/slither-0%20high%20%2F%200%20medium-brightgreen)](docs/security.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A blockchain proves who owns an NFT. It cannot tell whether the image is original: a
plagiarised, edited or AI-generated picture mints as easily as real art. **Mint-Gate** sits in
front of the mint. Provenance, AI-image, manipulation and copy checks score every upload, and a calibrated policy engine
decides **PASS**, **REVIEW** (human verification) or **FAIL**. Only verified artwork can be
minted. Every verdict, including the rejections, is anchored on Ethereum.

> The blockchain does not detect forgery. The AI verifies the artwork off-chain; the smart
> contract stores the verdict and **refuses to mint** without a passing one.

**Contracts v2** ([design](docs/contracts.md)): the backend *signs* EIP-712 verdicts, the artist
submits them and mints from their own wallet, minted art can be disputed and revoked, and any
ERC-721 can reuse the registry through `MintGated`. All source-verified (Sourcify exact match):

| Network | `MintGateRegistry` | `MintGateNFTV2` |
|---|---|---|
| Ethereum Sepolia | [`0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB`](https://sepolia.etherscan.io/address/0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB) | [`0x59900d4F93fAb95F3B8aFBD4E9fEbF15Fa0F4f22`](https://sepolia.etherscan.io/address/0x59900d4F93fAb95F3B8aFBD4E9fEbF15Fa0F4f22) |
| Base Sepolia | [`0xae989b374DF85a942b4caE07940713Ff89418F32`](https://sepolia.basescan.org/address/0xae989b374DF85a942b4caE07940713Ff89418F32) | [`0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB`](https://sepolia.basescan.org/address/0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB) |

**Governance + disputes (Phase 6, [runbooks](docs/governance.md)):** no single key controls the system.
Registry admin, pauser and treasury go to a **2-of-3 Safe**; the decision policy is announced in a
**PolicyRegistry** behind a **1-hour timelock** (the Safe is the only proposer), so every policy change is
public before it applies and every verdict's policy hash can be checked against it. Anyone can **dispute** a
token with a bond and pinned evidence; Mint-Gate adds its own evidence (copy search, earliest signed
submission, reverse search); a reviewer wallet decides with a **written reason whose SHA-256 goes on chain**;
an upheld dispute revokes the token and refunds the bond. A public **explorer** (`/explorer`) shows statistics,
the policy history and pending Safe transactions, which owners sign there with their own wallet.

| Network | Safe 2-of-3 | `PolicyRegistry` | `DisputeResolver` | `MintGateTimelock` |
|---|---|---|---|---|
| Ethereum Sepolia | [`0xEdE5…90Fb`](https://sepolia.etherscan.io/address/0xEdE58D8Ff74e72fc7568e68c8927a68884a290Fb) | [`0xaa3d…78Fe`](https://sepolia.etherscan.io/address/0xaa3d83420EB3f46C9765DF991F4559D04BF178Fe) | [`0xdb04…48Cc`](https://sepolia.etherscan.io/address/0xdb0490759F59dc31dd44a3A2d4F05756b37f48Cc) | [`0xC568…104F`](https://sepolia.etherscan.io/address/0xC56840B470611668521fA00cA1d911f57431104F) |
| Base Sepolia | [`0x66bf…2C67`](https://sepolia.basescan.org/address/0x66bf57DC8ea2bE63dCa3CA4791d438FC38b02C67) | [`0x7d56…A017`](https://sepolia.basescan.org/address/0x7d56206Fa46A7Cd90c36E6588Cba407949e9A017) | [`0xC568…104F`](https://sepolia.basescan.org/address/0xC56840B470611668521fA00cA1d911f57431104F) | [`0x1B2c…1c7d`](https://sepolia.basescan.org/address/0x1B2c47088CaE82b17690DF461FD05Fb3146a1c7d) |

v1 (`MintGateNFT`, retired from the app in Phase 2) stays on Ethereum Sepolia at
[`0xae989b374DF85a942b4caE07940713Ff89418F32`](https://sepolia.etherscan.io/address/0xae989b374DF85a942b4caE07940713Ff89418F32)
· [Sourcify](https://sourcify.dev/#/lookup/0xae989b374DF85a942b4caE07940713Ff89418F32).
Same deployer and nonce, so v1 on Sepolia and the v2 registry on Base Sepolia share an address.

**Stack:** PyTorch · frozen CLIP ViT-B/32 + SigLIP 2 probes · TruFor · SSCD / DINOv2 / PDQ · C2PA · OpenCV ·
scikit-learn · Flask + gunicorn · Solidity 0.8.24 / OpenZeppelin 5 / Hardhat + Foundry · EIP-712 · web3.py ·
IPFS (Pinata) · MetaMask · Docker

## Results

Every headline number below has its sample size and a **95 % confidence interval** (Wilson for
rates, stratified bootstrap for AUCs). Models are only adopted if they beat the incumbent on a
benchmark built *before* them; details and every rejected candidate: [docs/models.md](docs/models.md).

**Training data:** 20,309 images matched to what is actually minted: 3,700 from real NFT collections
(BAYC, MAYC, Azuki, …; minted before modern generators), 3,000 museum / WikiArt paintings, photos,
pixel art, and AI images from DALL·E 3, Midjourney, SD3, SDXL and older generators. **Flux is held
out of training entirely.**

**AI detection: chosen out-of-source (Phase 5).** The test that matters uses sources that contributed
*nothing* to training: 1,698 genuine images (947 NFTs from 47 unseen collections, 54 pixel-art NFTs,
300 Rijksmuseum paintings, 397 COCO / DOCCI photos) and 733 images from 2025-26 generators
(z-image, Flux.2, GPT-image-1.5, Midjourney 7, Illustrious, Recraft, …) plus Flux.1-dev. Candidates
were ranked on one half, the winner is reported on the other half (n = 1,191).

| Held-out out-of-source half | v1: probe on CLIP-B/32 | **v5: probes on CLIP-B/32 + SigLIP 2** |
|---|---|---|
| ROC-AUC | 0.894 [0.875, 0.911] | **0.925 [0.909, 0.938]** |
| AI images flagged (n = 364) | 75.8 % [71.2, 79.9] | **88.7 % [85.1, 91.6]** |
| AI images auto-failed | 46.2 % [41.1, 51.3] | **69.0 % [64.0, 73.5]** |
| Genuine NFTs flagged (n = 455) | 5.7 % [3.9, 8.2] | 7.2 % [5.2, 10.0] |
| Genuine paintings flagged (n = 150) | 2.7 % [1.0, 6.7] | 6.7 % [3.7, 11.8] |
| Genuine photos flagged (n = 198) | 45.0 % [38.2, 51.9] | 52.5 % [45.6, 59.4] |

The gain over v1 is significant (paired bootstrap, +4.6 pp [+1.8, +7.6] balanced accuracy). CLIP-L/14
and SigLIP 2 So400m also won but cost 4-5 s per image on a CPU; DINOv2 lost; a LoRA fine-tune did
not beat the frozen probe. **Honest weak spot:** photographs from new sources (professional DOCCI
photos especially) are flagged by every detector tested, v1 included.

**Provenance (Phase 5):** C2PA Content Credentials, Stable Diffusion / SDXL invisible watermarks and
generator metadata, read from the raw file. On real files: **0 false hits on 1,452 genuine files**
([0, 0.3] %), AI evidence on 15.9 % [13.4, 18.8] of 729 AI files (Midjourney 7: 100 % via IPTC
metadata; GPT-image-1.5: 47.5 % via OpenAI's C2PA manifest). Cheap and precise, low recall.

**Manipulation forensics (Phase 5):** TruFor (CVPR 2023) replaces the ResNet-18, which was at chance
on real forgeries (AUC 0.49-0.58) and flagged 43 % of genuine NFTs from new collections:

| Real forgery dataset | ResNet-18 AUC | **TruFor AUC** | TruFor pixel F1 |
|---|---|---|---|
| COVERAGE copy-move (100 / 100) | 0.486 [0.401, 0.567] | **0.769 [0.702, 0.834]** | 0.524 [0.449, 0.599] |
| CocoGlide diffusion inpainting (300 / 300) | 0.579 [0.533, 0.625] | **0.768 [0.729, 0.804]** | 0.390 [0.347, 0.431] |
| Columbia splicing (180 manipulated) | – | 98.3 % [95.2, 99.4] flagged | 0.800 [0.760, 0.838] |

TruFor flags 6-8 % of genuine NFT art, so it can only send uploads to human review, never reject
them. Its licence is non-commercial and it needs a GPU (2-36 s per image on a CPU), so it runs in
the GPU profile only.

**Copy detection (Phase 4, benchmark-gated):** 4,000 protected works, 600 attacked copies (14 attacks),
5,967 genuine distractors, tuned on val, reported on test.

| Paintings, test split (144 copies, 1,486 genuine) | micro-AP ³ | TPR at 1 % FPR | Genuine flagged (policy) |
|---|---|---|---|
| v1: pHash + CLIP + ORB | 0.820 | 54.2 % [46.0, 62.1] | 5.0 % [4.0, 6.2] |
| **v4: PDQ + SSCD + DINOv2-S + region crops** | **0.905** | **89.6 % [83.5, 93.6]** | **2.2 % [1.6, 3.1]** |

**NFT-native evaluation (Phase 5b): real copymints and real AI collections.** Real copymint tokens on
Ethereum, each uploaded as the original token *file*, and real AI-generated NFT collections (all
contracts verified, never in the index; per-collection detail, addresses and evidence:
[docs/models.md](docs/models.md#phase-5b-nft-native-evaluation-real-copymints-real-ai-collections)):

| Real NFTs (n = 300 each) | Stopped (REVIEW or FAIL) | FAIL | Notes |
|---|---|---|---|
| RR/BAYC: Ryder Ripps' copy of Bored Apes | **56.7 %** [51.0, 62.2] | 0.0 % | right original named 296 / 299 |
| Phunky Ape Yacht Club: mirrored Bored Apes | **59.3 %** [53.7, 64.7] | 0.0 % | right original named 296 / 300 |
| CryptoPhunks V2: mirrored CryptoPunks | 5.3 % [3.3, 8.5] | 2.0 % | **open gap**: mirrored 24 px punks look like siblings |
| AI NFTs: 12 BrainDrops projects + BGAN Punks | **91.7 %** [88.0, 94.3] | 66.0 % | detector alone 85.7 % of 518; BGAN Punks (GAN pixel art) 0 % |

Before Phase 5b the gate let 69 % of RR/BAYC and 66 % of PHAYC through; a new REVIEW-only `copy_member`
check (copy engine 1.1.0) now catches re-mints of indexed tokens. Copymints of detailed art are still
not *auto-failed*: the index keeps 128 px thumbnails, too lossy to prove pixel identity (larger
references are the planned fix).

**End-to-end gate decisions** (full pipeline, policy 5.1.0, GPU profile, live index of 288,860
references incl. 273,814 Ethereum NFTs; **n = 300 per category**, fewer only where the pool is smaller):

| Upload | n | PASS | REVIEW | FAIL |
|---|---|---|---|---|
| Genuine NFT-collection art ⁴ | 300 | **86.7 %** [82.3, 90.0] | 12.3 % | **1.0 %** [0.3, 2.9] |
| Genuine NFT art, collections never seen ⁴ | 300 | 79.7 % [74.8, 83.8] | 17.0 % | 3.3 % [1.8, 6.0] |
| Genuine pixel-art punk | 300 | **96.0 %** [93.1, 97.7] | 2.0 % | 2.0 % [0.9, 4.3] ¹ |
| Genuine painting, Rijksmuseum (never seen) | 300 | 84.3 % [79.8, 88.0] | 15.7 % | **0.0 %** [0.0, 1.3] |
| Genuine photo, COCO / DOCCI (never seen) | 300 | 42.3 % [36.9, 48.0] | 33.7 % | 24.0 % [19.5, 29.1] |
| **Modern AI** (DALL·E 3, Midjourney, SD3, SDXL) | 300 | **0.3 %** [0.1, 1.9] | 1.0 % | 98.7 % [96.6, 99.5] |
| **Unseen Flux** | 300 | **2.0 %** [0.9, 4.3] | 2.7 % | 95.3 % [92.3, 97.2] |
| **2025-26 generators** (never seen) | 300 | 11.3 % [8.2, 15.4] | 24.0 % | 64.7 % [59.1, 69.9] |
| **Real AI NFT collections** (never seen) | 300 | 8.3 % [5.7, 12.0] | 25.7 % | 66.0 % [60.5, 71.1] |
| Exact copy of a protected work | 300 | 0.0 % [0.0, 1.3] | 0.0 % | **100 %** [98.7, 100] |
| Edited copy of a protected painting | 300 | 1.7 % [0.7, 3.8] | 30.7 % | 67.7 % [62.2, 72.7] |
| Copy-move / spliced forgery | 295 / 295 | 0.7 % / **0.0 %** | 16.3 % / 78.3 % | 83.0 % / 21.7 % |
| Re-upload of an indexed Ethereum NFT's 128 px thumbnail ⁵ | 300 | 2.0 % [0.9, 4.3] | 0.7 % | **97.3 %** [94.8, 98.6] |
| Real copymint, full-size token file (RR/BAYC / PHAYC) | 300 / 300 | 43.3 % / 40.7 % | 56.7 % / 59.3 % | 0.0 % / 0.0 % |
| Edited copy of an indexed Ethereum NFT | 300 | 64.0 % [58.4, 69.2] | 27.3 % | 8.7 % [6.0, 12.4] |
| Edited copy of a generative-collection NFT | 300 | 58.3 % [52.7, 63.8] | 40.7 % | 1.0 % [0.3, 2.9] ² |

Pooled over all 24 categories: **77.4 % [75.5, 79.1] of genuine uploads pass** (n = 1,978) and
6.2 % [5.2, 7.4] are rejected; **78.5 % [77.4, 79.7] of forgeries are stopped** (n = 4,648);
on the 20 categories of policy 5.0.0 87.4 % [86.2, 88.4] (5.0.0: 87.1 %), on v1's categories 92.0 % [90.9, 93.0]; the
four real-world Phase 5b categories alone 53.2 % [50.4, 56.1] (n = 1,200). False alarms per domain (REVIEW or FAIL): collection NFTs
16.8 % ⁴, pixel art 9.6 %, paintings 19.5 %, photos 45.9 %.

**CPU latency, the open issue:** the extra SigLIP 2 pass costs ~0.6 s on 2 CPU threads, so a CPU-only
deployment now takes **p50 1.5-1.6 s, p95 1.9-2.0 s** per upload (n = 40 uploads per run, two runs), above the 1.5 s budget (v4: 1.10 /
1.35 s). The GPU profile is unaffected; an ONNX Runtime export (-180 ms) is built and waits for a
Linux check in Phase 7.

¹ `copy_exact` matches to a *different* punk (a sibling inside the 128 px pixel budget). Policy 5.0.0
failed 18 / 300; its note called them dataset duplicates, but Phase 5b's live gate showed they were
sibling false matches. The new member rule (BOX, dihedral, level 16) cuts them to 6.

³ micro-AP is a ranking statistic over all (query, reference) predictions; no CI was computed for it.

² A known limitation. Genuine items of a generative collection share traits by design, so an edited
copy can't be told apart from a new sibling by appearance alone; TruFor now sends a third of them to
review, but only pixel-identical copies are blocked.

⁴ These genuine sets are images of tokens that *are* indexed (huggingnft BAYC / Azuki / MAYC, the OOS
on-chain tokens), so uploading one is a re-mint: 22 of 23 `copy_member` REVIEWs checked point to the
image's own token. FAIL is unchanged.

⁵ Re-uploads the index's own 128 px thumbnail, so it measures index recall. Uploading the token's real
file is the copymint row below it.

## How it works

```
upload ─▶ provenance (raw bytes): C2PA, SD/SDXL watermark, generator metadata ─┐
       ─▶ preprocessing ─▶ ┌ AI detector: probes on CLIP-B/32 + SigLIP 2        │
 (validate, RGB,           │ TruFor manipulation map (GPU, REVIEW-only)        │   policy engine ─▶ PASS ─▶ mint
  SHA-256, identical       │ copy-move: block matching + shift voting          │─▶ calibrated     REVIEW ─▶ human reviewer
  normalisation to         │ copy engine: PDQ + SSCD/DINOv2 + region crops    │   risk + score   FAIL ─▶ blocked
  the training data)       │   vs pgvector: 15k protected works, 270k        │          │
                           │   Ethereum NFTs (48 collections), all minted here ┘          │
                                                                                          ▼
   artist: Sign-In with Ethereum, signs the upload's SHA-256 (claim of authorship, stored with the report)
   API (stateless, N instances) ─▶ Postgres (submissions, reports, attestations, audit log) + Redis/RQ jobs
   analysis worker (models loaded once) ─▶ verdict ─▶ PASS: EIP-712 attestation for the artist's wallet
   chain worker: submits REVIEW / FAIL verdicts, mirrors reviewer decisions (resolveReview)
   artist wallet: MintGateNFTV2.submitAndMint ─▶ MintGateRegistry (one mint per image, disputes, revocation)
   indexer: registry + NFT + governance events ─▶ chain_events ─▶ tokens / disputes (reorg-safe, rebuildable)
   dispute: reporter pins evidence + openDispute(bond) ─▶ worker adds Mint-Gate's evidence ─▶ Discord alert
            ─▶ reviewer wallet: DisputeResolver.resolveDispute(id, upheld, sha256(reason), reasonURI) ─▶ revoked
   governance: Safe 2-of-3 = registry admin + pauser + treasury; Safe ─▶ timelock (1 h) ─▶ PolicyRegistry
   IPFS: artwork + ERC-721 metadata + full verification report
   /token/<chain>/<contract>/<id>: public page that re-checks chain + report hash in the browser
```

Engineering decisions worth knowing:
- **Dataset-bias control.** In GenImage, real photos are JPEGs and fakes are PNGs, so a model
  can learn "JPEG = real". Every image in both classes gets the same resize + JPEG re-encode,
  and uploads get the identical "detector view" at inference.
- **Out-of-source evaluation.** In-distribution accuracy overstated the CNN. Testing on unseen
  collections and a different Flux publisher exposed this and drove the policy design. Phase 5
  rebuilt the test (2025-26 generators, 47 unseen NFT collections, museum paintings, new photos),
  split it into a selection half and a reporting half, and gave every number a 95 % CI.
- **Provenance first, then pixels.** C2PA credentials, invisible watermarks and generator metadata
  are read from the raw upload: an intact AI claim fails the upload, trusted camera credentials
  stop the AI probe from auto-rejecting a photo. OpenAI's manifests carry an expired certificate;
  that is a credential problem, not tampering, and is handled as such.
- **Licence-aware profiles.** TruFor is non-commercial: `MINTGATE_PERMISSIVE_ONLY=1` runs without it
  and with a probe trained only on MIT / CDLA-Permissive / public-domain / CC BY data, under its own
  calibrated policy.
- **Benchmark-gated copy detection (Phase 4).** A copy-detection benchmark (14 attacks including
  collage and Stable-Diffusion re-generation, ~10 genuine distractors per copy, val/test split) was
  built *before* any new model. Every candidate had to beat the incumbent on it: SSCD + DINOv2-S +
  region crops replaced CLIP / pHash (paintings micro-AP 0.820 → 0.905, TPR at 1 % FPR 0.54 → 0.90),
  PDQ replaced pHash, and ALIKED + LightGlue was **rejected** (+0.3 pp, below the gate), so ORB was
  retired without a replacement. Details: [docs/models.md](docs/models.md).
- **Collection-aware copy detection.** In a generative collection (CryptoPunks, Bored Apes),
  genuine siblings look alike by design, and CryptoPunks siblings are even PDQ-close. Collection
  members therefore count only as exact copies, and only when PDQ and SSCD agree; copy scores are
  relative to each work's own nearest neighbour in the index.
- **An on-chain NFT database.** 275,882 Ethereum tokens from 48 collections (Alchemy NFT API,
  resumable per collection) are fingerprinted into Postgres/pgvector (HNSW, halfvec) with their
  traits; a sync loop follows new `Transfer(from=0)` mints of tracked collections.
- **Data-driven thresholds.** REVIEW/FAIL thresholds sit at measured 5 % / 1 % false-alarm
  rates on genuine validation art. The policy file's SHA-256 is stored on-chain with every verdict.
- **The chain is the source of truth.** An indexer follows the registry and NFT events
  (`--from-block` re-derives everything from scratch; reorgs deeper than the confirmation depth
  are rolled back). Every minted artwork becomes a reference item that all workers compare
  new uploads against.
- **The server never mints.** It signs an EIP-712 verdict bound to the artist's wallet; the
  artist submits it and mints (and pays gas). The contract enforces the rule again
  (`NotMintable`, `ImageAlreadyMinted`, `ArtistMismatch`).
- **Wallet identity, verifiable provenance.** Artists sign in with their wallet (SIWE, EIP-4361) and
  sign each upload; only that wallet receives the attestation and mints (paying gas, with live
  confirmations and decoded contract errors). A token's public page re-reads the chain and
  recomputes the report hash in the browser, so it does not have to trust the Mint-Gate server.
- **Contestable, explained decisions (Phase 6).** A revocation needs a bonded dispute, evidence from both
  sides and a reviewer whose written reason is pinned and hashed on chain. Bond accounting is checked by
  Foundry invariants (conservation, snapshot, payouts follow the outcome); the whole dispute and the Safe
  hand-over run in a Playwright browser test against the real Safe v1.4.1 bytecode.
- **Asynchronous and stateless.** Uploads return `202 {job_id}` in ~50 ms; RQ workers run the
  models. A killed worker's job is retried automatically; API restarts lose nothing.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/detection/` | preprocessing, provenance (C2PA / watermark / metadata), AI detector + frozen backbones, TruFor, copy-move, copy engine + pgvector index, policy, pipeline |
| `backend/training/` | dataset build, out-of-source set (`oos_data`), backbone benchmark (`ai_backbones`), LoRA, retraining gate, statistics, calibration, end-to-end evaluation |
| `backend/chain/` | v2 registry client (`registry.py`, Redis nonce lock), networks, EIP-712 verdict signer (`eip712.py`), IPFS + metadata, `deployments/<network>.json` |
| `backend/db/`, `backend/migrations/` | SQLAlchemy 2 models + repository layer, Alembic migrations, legacy JSON import (`db.migrate_json`) |
| `backend/jobs/` | Redis + RQ queues, tasks (analysis, verdict anchoring, review mirroring), Windows-safe worker |
| `backend/indexer/` | chain indexer: events → `chain_events` → `tokens` / `disputes`, block cursor in Postgres |
| `backend/disputes/`, `backend/explorer.py` | dispute API (reporter evidence, backend evidence, reviewer reasons), public explorer + Safe-signature API |
| `backend/governance/` | Safe proposals (EIP-712 SafeTx), policy pinning, CLI `python -m governance` |
| `backend/notify.py` | Discord webhook alerts for disputes and REVIEW items |
| `backend/server.py`, `wsgi.py` | stateless Flask API (Redis rate limits, CSP, proxy-aware), gunicorn entry point |
| `Blockchain/contracts/MintGateNFT.sol` | v1: ERC-721 + AccessControl provenance contract |
| `Blockchain/contracts/v2/` | v2: `MintGateRegistry` (EIP-712 verdicts, disputes, revocation, pause), `MintGateNFTV2` (artist self-mint, ERC-2981), `MintGated` / `IMintGate` (reuse by other collections); Phase 6: `DisputeResolver`, `PolicyRegistry`, `MintGateTimelock` |
| `Blockchain/vendor/safe-1.4.1/` | Safe v1.4.1 artifacts (LGPL-3.0) for local tests; testnets use the canonical deployments |
| `Blockchain/test/` | Hardhat unit tests (v1 + v2, 100 % coverage) and Foundry fuzz / invariant suites (`test/foundry/`) |
| `docs/contracts.md` | v2 state machines, interface, roles, upgrade strategy, threat model, gas |
| `backend/tests/` | pytest suite: policy, preprocessing, reference index, copy-move, repository layer, API + job flow, indexer on a Hardhat node |
| `docs/security.md` | Slither results, coverage, known limitations |
| `docs/governance.md` | Safe / timelock / policy registry, role map, dispute process, key-rotation and emergency runbooks |
| `backend/auth.py` | Sign-In with Ethereum, Redis sessions, CSRF, signed-upload verification |
| `frontend/` | vanilla-JS ES modules (`js/`), vendored ethers v6 (`vendor/`), main app (incl. reviewer dashboard), public token page (`token.html`: timeline, report a token), explorer (`explorer.html`) |
| `e2e/` | Playwright browser test against a local Hardhat node (`stack.py` = whole stack with fake models) |
| `deploy/` | production Dockerfile and deploy tooling |
| `PROJECT_REPORT.md` | full methodology and results |

## Run locally

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
.\venv\Scripts\python.exe -m pip install -r requirements.txt
cd Blockchain; npm ci; npx hardhat test; cd ..
docker compose up -d                                          # Postgres 16 + pgvector, Redis 7
powershell -ExecutionPolicy Bypass -File .\run_local.ps1     # migrations, local chain, v2 deploy, indexer,
                                                              # workers, server -> http://127.0.0.1:5000
```

Separately (from `backend/`): `python -m alembic upgrade head`, `python -m jobs.worker analysis`,
`python -m jobs.worker chain`, `python -m indexer [--from-deploy] [--once]`, `python server.py`.

Tests (CI runs the fast suite, Hardhat tests + coverage, Slither and the pre-commit hooks):

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -m "not slow"     # ~130 tests: test Postgres (docker compose), fakeredis,
                                                       # fake models; chain tests start a Hardhat node
cd Blockchain; npm run coverage; cd ..
node --test "frontend/tests/*.test.mjs"               # report-hash parity with Python
cd e2e; npm ci; npx playwright install chromium; npx playwright test   # browser: sign in, upload, mint, verify
```

Reproduce the models from `backend/`:
`training.fetch_realworld` → `training.fetch_v2` → `train_cnn --epochs 10` → `train_ai_detector` →
`build_index` → `calibrate` → `evaluate_system` → `independent_check`.

Secrets live in `backend/.env` and `Blockchain/.env` (templates: `.env.example`), and both are git-ignored.

## Security

- Uploads: 10 MB limit, format allow-list, `verify()` before decode, decompression-bomb guard.
- Per-IP rate limits shared by all API instances (Flask-Limiter on Redis). Strict
  Content-Security-Policy (`script-src 'self'`): no third-party scripts, ethers v6 is vendored.
- Sign-In with Ethereum with single-use Redis nonces; `__Host-` session cookie (HttpOnly, Secure,
  SameSite=Strict, 1 h); CSRF token + Origin check on every state-changing request; uploads must be
  signed by the session wallet.
- Attestations are issued only to the signed-in artist wallet and expire after 24 h; nonces
  are random 256-bit and unique per chain (DB constraint). Append-only `audit_log` (a trigger
  rejects UPDATE / DELETE). Backend transactions take a Redis lock per sender (no nonce races).
- All data rendered with `textContent`. Errors to clients are generic.
- In production the server refuses to start without a strong reviewer key and a verifier key.
  Reviewers are wallets holding `REVIEWER_ROLE` on chain (SIWE); the shared key is a fallback
  that `REVIEWER_KEY_DISABLED=1` turns off.
- Governance: registry admin / pauser / treasury held by a 2-of-3 Safe; policy changes through a 1 h
  timelock; a single leaked key cannot change roles or the policy (tested in `Governance.test.js`).
- Model weights load with `torch.load(weights_only=True)`. Classical models are stored as JSON, never pickle.
- Contracts: Slither 0 high / 0 medium (v2: one accepted informational; v1: 2 justified lows), 100 % test
  coverage (CI gate 95 %), Foundry fuzz + invariant suites in CI. Details and the trust model's
  known limits: [docs/security.md](docs/security.md); v2 threat model: [docs/contracts.md](docs/contracts.md).

## Data and licences

NFT collections (huggingnft, MIT), Rapidata FLUX/SD3/MJ/DALL·E 3 (CDLA-Permissive-2.0), SDXL
(MIT), WikiArt, Tiny-GenImage (CC BY-NC-SA 4.0), AI Art & Real Art / Real-VS-AI-Art (research
use). Evaluation only (never trained on): OpenFake (CC BY-NC 4.0), real forgery sets via the KhangTruong Tampered-Images bundle (CASIA v2, Columbia, COVERAGE, IMD2020, CocoGlide; research use), Rijksmuseum (CC0), COCO (CC BY), DOCCI (CC BY). Permissive extras: ArtBench public-domain paintings, COCO-train CC BY photos. **TruFor's code and weights are non-commercial** (GRIP-UNINA). This is a **non-commercial portfolio project**. Automated detection is probabilistic: a
PASS is evidence, not a legal guarantee of authorship.

Code: [MIT](LICENSE). The data licences above still apply to the datasets and to model weights trained on them.
