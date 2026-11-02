# Mint-Gate: AI forensics that verifies artwork *before* it becomes an NFT

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

**End-to-end gate decisions** (full pipeline + policy v3.1.0, n = 50 uploads per category):

| Upload | PASS | REVIEW | FAIL |
|---|---|---|---|
| Genuine art from an NFT collection | **88 %** | 12 % | **0 %** |
| Genuine pixel-art NFT | 82 % | 18 % | 0 % |
| Genuine painting | 76 % | 12 % | 12 % ¹ |
| Genuine photograph | 62 % | 38 % | 0 % |
| **Modern AI** (DALL·E 3, Midjourney, SD3, SDXL) | **0 %** | 12 % | 88 % |
| **Unseen Flux** | **0 %** | 24 % | 76 % |
| Other AI-art generators | 12 % | 42 % | 46 % |
| Exact copy of a protected work | 0 % | 0 % | **100 %** |
| Edited copy of a protected painting | 4 % | 6 % | **90 %** |
| Copy-move forgery | 2 % | 20 % | 78 % |
| Spliced forgery | 0 % | 84 % | 16 % |
| Edited copy of a generative-collection NFT | 94 % | 6 % | 0 % ² |

Overall: **3 % of genuine uploads wrongly rejected** and 77 % passed directly; **86 % of
forgeries stopped** (REVIEW or FAIL).

¹ On a larger sample (n = 120) the rate is 6.7 %. Two of those eight "genuine" paintings are
near-duplicates of protected paintings (the same artwork appears in two source datasets), so
the real false-alarm rate is about 5 %.

² A known limitation. Genuine items of a generative collection share traits by design, so an
edited copy can't be told apart from a new sibling by appearance alone. Only exact copies are
blocked here, and the contract enforces that as well.

## How it works

```
upload ─▶ preprocessing ─▶ ┌ CLIP-probe AI detector   (decides on AI content) ┐
 (validate, RGB,           │ ResNet-18                 (REVIEW-only signal)    │   policy engine ─▶ PASS ─▶ mint
  SHA-256, identical       │ copy-move: block matching + shift voting          │─▶ calibrated     REVIEW ─▶ human reviewer
  normalisation to         │ CLIP copy score ┐ vs protected collection:        │   risk + score   FAIL ─▶ blocked
  the training data)       │ pHash, ORB      ┘ 15,037 works + all minted here  ┘          │
                                                                                          ▼
   API (stateless, N instances) ─▶ Postgres (submissions, reports, attestations, audit log) + Redis/RQ jobs
   analysis worker (models loaded once) ─▶ verdict ─▶ PASS: EIP-712 attestation for the artist's wallet
   chain worker: submits REVIEW / FAIL verdicts, mirrors reviewer decisions (resolveReview)
   artist wallet: MintGateNFTV2.submitAndMint ─▶ MintGateRegistry (one mint per image, disputes, revocation)
   indexer: registry + NFT events ─▶ chain_events ─▶ tokens / disputes (reorg-safe, rebuildable)
   IPFS: artwork + ERC-721 metadata + full verification report
```

Engineering decisions worth knowing:
- **Dataset-bias control.** In GenImage, real photos are JPEGs and fakes are PNGs, so a model
  can learn "JPEG = real". Every image in both classes gets the same resize + JPEG re-encode,
  and uploads get the identical "detector view" at inference.
- **Out-of-source evaluation.** In-distribution accuracy overstated the CNN. Testing on unseen
  collections and a different Flux publisher exposed this and drove the policy design.
- **Collection-aware copy detection.** In a generative collection (CryptoPunks, Bored Apes),
  genuine siblings share hashes and keypoints. pHash and ORB therefore only count against
  one-off artworks, and the CLIP copy score is relative to each work's own nearest neighbour.
  Collection members are protected against exact copies.
- **Data-driven thresholds.** REVIEW/FAIL thresholds sit at measured 5 % / 1 % false-alarm
  rates on genuine validation art. The policy file's SHA-256 is stored on-chain with every verdict.
- **The chain is the source of truth.** An indexer follows the registry and NFT events
  (`--from-block` re-derives everything from scratch; reorgs deeper than the confirmation depth
  are rolled back). Every minted artwork becomes a reference item that all workers compare
  new uploads against.
- **The server never mints.** It signs an EIP-712 verdict bound to the artist's wallet; the
  artist submits it and mints (and pays gas). The contract enforces the rule again
  (`NotMintable`, `ImageAlreadyMinted`, `ArtistMismatch`).
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
| `frontend/` | web UI (MetaMask connect, Etherscan/IPFS links) |
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
.\venv\Scripts\python.exe -m pytest -m "not slow"     # ~110 tests: test Postgres (docker compose), fakeredis,
                                                       # fake models; chain tests start a Hardhat node
cd Blockchain; npm run coverage; cd ..
```

Reproduce the models from `backend/`:
`training.fetch_realworld` → `training.fetch_v2` → `train_cnn --epochs 10` → `train_ai_detector` →
`build_index` → `calibrate` → `evaluate_system` → `independent_check`.

Secrets live in `backend/.env` and `Blockchain/.env` (templates: `.env.example`), and both are git-ignored.

## Security

- Uploads: 10 MB limit, format allow-list, `verify()` before decode, decompression-bomb guard.
- Per-IP rate limits shared by all API instances (Flask-Limiter on Redis). Strict
  Content-Security-Policy. No third-party scripts; the wallet uses `window.ethereum`.
- Attestations are bound to the artist wallet given at upload and expire after 24 h; nonces
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
