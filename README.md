# Mint-Gate: AI forensics that verifies artwork *before* it becomes an  NFT

[![CI](https://github.com/1005Manasvi/mint-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/1005Manasvi/mint-gate/actions/workflows/ci.yml)
[![Contract coverage](https://img.shields.io/badge/contract%20coverage-100%25-brightgreen)](docs/security.md)
[![Slither](https://img.shields.io/badge/slither-0%20high%20%2F%200%20medium-brightgreen)](docs/security.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A blockchain proves who owns an NFT. It cannot tell whether the image is original: a
plagiarised, edited or AI-generated picture mints as easily as real art. **Mint-Gate** sits in
front of the mint. Six forensic checks score every upload, and a calibrated policy engine
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

v1 (`MintGateNFT`, which the current app still uses) stays on Ethereum Sepolia at
[`0xae989b374DF85a942b4caE07940713Ff89418F32`](https://sepolia.etherscan.io/address/0xae989b374DF85a942b4caE07940713Ff89418F32)
· [Sourcify](https://sourcify.dev/#/lookup/0xae989b374DF85a942b4caE07940713Ff89418F32).
Same deployer and nonce, so v1 on Sepolia and the v2 registry on Base Sepolia share an address.

**Stack:** PyTorch (ResNet-18 fine-tuned on an RTX 3050) · CLIP ViT-B/32 · OpenCV ·
scikit-learn · Flask + gunicorn · Solidity 0.8.24 / OpenZeppelin 5 / Hardhat + Foundry · EIP-712 · web3.py ·
IPFS (Pinata) · MetaMask · Docker

## Results

**Training data (v2):** 20,309 images chosen to match what is actually minted:
- 3,700 images from **real NFT collections** (BAYC, MAYC, Azuki, Trippy Toadz, …; all minted
  before modern image generators existed)
- 3,000 museum/WikiArt paintings, plus some photos and pixel art
- **modern AI images**: DALL·E 3, Midjourney, Stable Diffusion 3 and SDXL, plus older generators

**Flux was held out of training entirely**, to test generalisation to an unseen generator.

| Model | Held-out test | Unseen generator (Flux) |
|---|---|---|
| **AI detector**: linear probe on frozen CLIP | **92.8 % acc · ROC-AUC 0.982** | ROC-AUC **0.990** · 97.9 % caught |
| ResNet-18 (all layers fine-tuned on GPU) | 91.1 % acc · ROC-AUC 0.974 | 95.3 % caught |

**Out-of-source check (the honest number).** These images come from sources that contributed
*nothing* to training: 480 artworks from 4 NFT collections never seen (CyberKongz, CrypToadz,
Shiboshis, EtherBears), and 200 Flux images from a different publisher.

| Model | Genuine NFTs correct | Flux caught | ROC-AUC |
|---|---|---|---|
| **CLIP-probe AI detector** | **100 %** | **94 %** | **0.9998** |
| ResNet-18 | 64 % | 77 % | 0.79 |

The ResNet's in-distribution score partly came from memorising collections it had seen. The
CLIP probe generalises. The policy therefore lets the CLIP probe decide on AI content, and
uses the ResNet only to request human review.

**Copy detection (Phase 4, benchmark-gated).** A copy-detection benchmark was built *before* any
new model: 4,000 protected works, 600 attacked copies (14 attacks: crop, rotation, perspective,
recolour, overlays, screenshots, collages, Stable-Diffusion re-generation, ...) and 5,967 genuine
distractors, tuned on val, reported on test ([details](docs/models.md)).

| Paintings, test split | micro-AP | TPR at 1 % FPR | Genuine flagged (policy) |
|---|---|---|---|
| v1: pHash + CLIP + ORB | 0.820 | 0.542 | 5.0 % |
| **v4: PDQ + SSCD + DINOv2-S + region crops** | **0.905** | **0.896** | **2.2 %** |

LightGlue was benchmarked too and **rejected** (+0.3 pp, below the gate). Genuine items of generative
collections are never flagged any more (6.5 % → 0 %), because a collection member only counts as
copied when it is pixel-identical.

**End-to-end gate decisions** (full pipeline, policy 4.0.0, against the live index of 284,861
references incl. 269,816 Ethereum NFTs; n = 50 uploads per category; v1 = policy 3.1.0):

| Upload | PASS | REVIEW | FAIL | v1 PASS / FAIL |
|---|---|---|---|---|
| Genuine art from an NFT collection | **98 %** | 2 % | **0 %** | 88 % / 0 % |
| Genuine pixel-art NFT | 94 % | 4 % | 2 % | 82 % / 0 % |
| Genuine painting | 72 % | 16 % | 12 % ¹ | 76 % / 12 % |
| Genuine photograph | 68 % | 32 % | 0 % | 62 % / 0 % |
| **Modern AI** (DALL·E 3, Midjourney, SD3, SDXL) | **0 %** | 12 % | 88 % | 0 % / 88 % |
| **Unseen Flux** | **0 %** | 24 % | 76 % | 0 % / 76 % |
| Other AI-art generators | 12 % | 44 % | 44 % | 12 % / 46 % |
| Exact copy of a protected work | 0 % | 0 % | **100 %** | 0 % / 100 % |
| Edited copy of a protected painting | **2 %** | 26 % | 72 % | 4 % / 90 % |
| Copy-move / spliced forgery | 4 % / 0 % | 18 % / 84 % | 78 % / 16 % | PASS 2 % / 0 % |
| **Exact re-upload of an indexed Ethereum NFT** (new) | **0 %** | 2 % | **98 %** | not indexed |
| Edited copy of an indexed Ethereum NFT (new) | 54 % | 40 % | 6 % | not indexed |
| Edited copy of a generative-collection NFT | 100 % | 0 % | 0 % ² | 94 % / 0 % |

Overall: **83 % of genuine uploads pass** (v1 77 %), **3.5 % are wrongly rejected** (v1 3.0 %: one
genuine punk in 200 uploads); on the same forgery categories as v1, 84.9 % are stopped (v1 85.8 %).
CPU-only deployment: **p50 1.10 s, p95 1.35 s per upload** (2 threads); search over the 285k-NFT
index p95 15 ms.

¹ v1 found that many of these are near-duplicates of protected paintings in a second source dataset.

² A known limitation. Genuine items of a generative collection share traits by design, so an
edited copy can't be told apart from a new sibling by appearance alone. Only exact (pixel-identical)
copies are blocked; letting similarity count would wrongly flag genuine NFTs.

## How it works

```
upload ─▶ preprocessing ─▶ ┌ CLIP-probe AI detector   (decides on AI content) ┐
 (validate, RGB,           │ ResNet-18                 (REVIEW-only signal)    │   policy engine ─▶ PASS ─▶ mint
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
   indexer: registry + NFT events ─▶ chain_events ─▶ tokens / disputes (reorg-safe, rebuildable)
   IPFS: artwork + ERC-721 metadata + full verification report
   /token/<chain>/<contract>/<id>: public page that re-checks chain + report hash in the browser
```

Engineering decisions worth knowing:
- **Dataset-bias control.** In GenImage, real photos are JPEGs and fakes are PNGs, so a model
  can learn "JPEG = real". Every image in both classes gets the same resize + JPEG re-encode,
  and uploads get the identical "detector view" at inference.
- **Out-of-source evaluation.** In-distribution accuracy overstated the CNN. Testing on unseen
  collections and a different Flux publisher exposed this and drove the policy design.
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
- **Asynchronous and stateless.** Uploads return `202 {job_id}` in ~50 ms; RQ workers run the
  models. A killed worker's job is retried automatically; API restarts lose nothing.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/detection/` | preprocessing, ResNet-18, AI detector, copy-move, pHash, CLIP, ORB, reference index, policy, pipeline |
| `backend/training/` | dataset build (`fetch_realworld`, `fetch_v2`), training, index, calibration, evaluation, independent check |
| `backend/chain/` | v2 registry client (`registry.py`, Redis nonce lock), networks, EIP-712 verdict signer (`eip712.py`), IPFS + metadata, `deployments/<network>.json` |
| `backend/db/`, `backend/migrations/` | SQLAlchemy 2 models + repository layer, Alembic migrations, legacy JSON import (`db.migrate_json`) |
| `backend/jobs/` | Redis + RQ queues, tasks (analysis, verdict anchoring, review mirroring), Windows-safe worker |
| `backend/indexer/` | chain indexer: events → `chain_events` → `tokens` / `disputes`, block cursor in Postgres |
| `backend/server.py`, `wsgi.py` | stateless Flask API (Redis rate limits, CSP, proxy-aware), gunicorn entry point |
| `Blockchain/contracts/MintGateNFT.sol` | v1: ERC-721 + AccessControl provenance contract |
| `Blockchain/contracts/v2/` | v2: `MintGateRegistry` (EIP-712 verdicts, disputes, revocation, pause), `MintGateNFTV2` (artist self-mint, ERC-2981), `MintGated` / `IMintGate` (reuse by other collections) |
| `Blockchain/test/` | Hardhat unit tests (v1 + v2, 100 % coverage) and Foundry fuzz / invariant suites (`test/foundry/`) |
| `docs/contracts.md` | v2 state machines, interface, roles, upgrade strategy, threat model, gas |
| `backend/tests/` | pytest suite: policy, preprocessing, reference index, copy-move, repository layer, API + job flow, indexer on a Hardhat node |
| `docs/security.md` | Slither results, coverage, known limitations |
| `backend/auth.py` | Sign-In with Ethereum, Redis sessions, CSRF, signed-upload verification |
| `frontend/` | vanilla-JS ES modules (`js/`), vendored ethers v6 (`vendor/`), main app + public token page (`token.html`) |
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
  The reviewer key is checked in constant time.
- Model weights load with `torch.load(weights_only=True)`. Classical models are stored as JSON, never pickle.
- Contracts: Slither 0 high / 0 medium (v2: no findings; v1: 2 justified lows), 100 % test
  coverage (CI gate 95 %), Foundry fuzz + invariant suites in CI. Details and the trust model's
  known limits: [docs/security.md](docs/security.md); v2 threat model: [docs/contracts.md](docs/contracts.md).

## Data and licences

NFT collections (huggingnft, MIT), Rapidata FLUX/SD3/MJ/DALL·E 3 (CDLA-Permissive-2.0), SDXL
(MIT), WikiArt, Tiny-GenImage (CC BY-NC-SA 4.0), AI Art & Real Art / Real-VS-AI-Art (research
use). This is a **non-commercial portfolio project**. Automated detection is probabilistic: a
PASS is evidence, not a legal guarantee of authorship.

Code: [MIT](LICENSE). The data licences above still apply to the datasets and to model weights trained on them.
