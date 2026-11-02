# Mint-Gate: AI forensics that verifies artwork *before* it becomes an NFT

A blockchain proves who owns an NFT. It cannot tell whether the image is original: a
plagiarised, edited or AI-generated picture mints as easily as real art. **Mint-Gate** sits in
front of the mint. Six forensic checks score every upload, and a calibrated policy engine
decides **PASS**, **REVIEW** (human verification) or **FAIL**. Only verified artwork can be
minted. Every verdict, including the rejections, is anchored on Ethereum.

> The blockchain does not detect forgery. The AI verifies the artwork off-chain; the smart
> contract stores the verdict and **refuses to mint** without a passing one.

**Smart contract (Ethereum Sepolia, source verified):**
[`0xae989b374DF85a942b4caE07940713Ff89418F32`](https://sepolia.etherscan.io/address/0xae989b374DF85a942b4caE07940713Ff89418F32)
· [Sourcify exact match](https://sourcify.dev/#/lookup/0xae989b374DF85a942b4caE07940713Ff89418F32)

**Stack:** PyTorch (ResNet-18 fine-tuned on an RTX 3050) · CLIP ViT-B/32 · OpenCV ·
scikit-learn · Flask + gunicorn · Solidity 0.8.24 / OpenZeppelin 5 / Hardhat · web3.py ·
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
     MintGateNFT.sol (Sepolia): recordVerification · resolveReview · mintVerified (one token per image)
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
- **The chain is the source of truth.** On start-up the server rebuilds the set of minted
  artworks from `VerifiedMint` events plus IPFS, so ephemeral hosting loses nothing.
- **Defence in depth.** The mint rule is enforced by the API and again by the contract
  (`NotMintable`, `ImageAlreadyMinted`).

## Repository layout

| Path | Purpose |
|---|---|
| `backend/detection/` | preprocessing, ResNet-18, AI detector, copy-move, pHash, CLIP, ORB, reference index, policy, pipeline |
| `backend/training/` | dataset build (`fetch_realworld`, `fetch_v2`), training, index, calibration, evaluation, independent check |
| `backend/chain/` | web3.py contract client, IPFS pinning |
| `backend/server.py`, `wsgi.py` | Flask API (rate limits, CSP, proxy-aware), gunicorn entry point |
| `Blockchain/contracts/MintGateNFT.sol` | ERC-721 + AccessControl provenance contract (7 tests) |
| `frontend/` | web UI (MetaMask connect, Etherscan/IPFS links) |
| `deploy/` | production Dockerfile and deploy tooling |
| `PROJECT_REPORT.md` | full methodology and results |

## Run locally

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
.\venv\Scripts\python.exe -m pip install -r requirements.txt
cd Blockchain; npm install; npx hardhat test; cd ..
powershell -ExecutionPolicy Bypass -File .\run_local.ps1     # local chain + server -> http://127.0.0.1:5000
```

Reproduce the models from `backend/`:
`training.fetch_realworld` → `training.fetch_v2` → `train_cnn --epochs 10` → `train_ai_detector` →
`build_index` → `calibrate` → `evaluate_system` → `independent_check`.

Secrets live in `backend/.env` and `Blockchain/.env` (templates: `.env.example`), and both are git-ignored.

## Security

- Uploads: 10 MB limit, format allow-list, `verify()` before decode, decompression-bomb guard.
- Per-IP rate limits. Strict Content-Security-Policy. No third-party scripts; the wallet uses
  `window.ethereum`.
- All data rendered with `textContent`. Errors to clients are generic.
- In production the server refuses to start without a strong reviewer key and a verifier key.
  The reviewer key is checked in constant time.
- Model weights load with `torch.load(weights_only=True)`. Classical models are stored as JSON, never pickle.

## Data and licences

NFT collections (huggingnft, MIT), Rapidata FLUX/SD3/MJ/DALL·E 3 (CDLA-Permissive-2.0), SDXL
(MIT), WikiArt, Tiny-GenImage (CC BY-NC-SA 4.0), AI Art & Real Art / Real-VS-AI-Art (research
use). This is a **non-commercial portfolio project**. Automated detection is probabilistic: a
PASS is evidence, not a legal guarantee of authorship.
