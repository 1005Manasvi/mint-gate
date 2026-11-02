# Mint-Gate: AI forensics that verifies artwork *before* it becomes an NFT

A blockchain can prove who owns an NFT. It cannot tell whether the image is original:
a plagiarised, edited, cloned or AI-generated picture mints just as easily as real art.
**Mint-Gate** sits in front of the mint. It runs six forensic checks on every upload,
and a calibrated policy engine decides **PASS**, **REVIEW** (human verification) or **FAIL**.
Only verified artwork can be minted. Every verdict, including the rejections, is anchored on
Ethereum as tamper-evident provenance.

> **Design principle:** the blockchain does not detect forgery. The AI verifies the artwork
> off-chain. The smart contract stores the verdict and refuses to mint without a passing one.

**Stack:** PyTorch (ResNet-18 fine-tuned on GPU) · CLIP ViT-B/32 · OpenCV · scikit-learn ·
Flask + gunicorn · Solidity 0.8.24 / OpenZeppelin 5 / Hardhat · web3.py · IPFS (Pinata) ·
MetaMask · Docker · Hugging Face Spaces · Ethereum Sepolia

## Results (all on held-out data)

Training data is 17,086 real-world images: photos, museum/WikiArt paintings, pixel-art NFTs,
and images from 8 AI generators. **Midjourney was excluded from training entirely**, to
measure generalisation to an unseen generator.

| Component | Metric |
|---|---|
| ResNet-18 forgery classifier (test, n = 1,891) | **85.1 %** accuracy · ROC-AUC **0.941** · precision 92.7 % |
| AI-generated detector, CLIP linear probe (test, n = 1,621) | **90.6 %** accuracy · ROC-AUC **0.966** |
| AI detector on **unseen Midjourney** vs genuine art | ROC-AUC **0.926** · 77 % of Midjourney images detected |
| Copy-move detector (clone forgeries) | ROC-AUC **0.90** · 69 % caught at 5 % false alarms |
| Robustness: ResNet after JPEG q=70 re-compression | 87.1 % accuracy (no degradation) |

**End-to-end gate decisions** (full pipeline + policy v2.2.0, n = 50 uploads per category):

| Upload | PASS | REVIEW | FAIL |
|---|---|---|---|
| Genuine painting / drawing | **84 %** | 6 % | 10 % |
| Genuine photograph | **82 %** | 18 % | 0 % |
| Genuine new pixel-art NFT | 62 % | 32 % | 6 % |
| AI-generated art (Ideogram, SDXL, Civitai, …) | 6 % | 32 % | **62 %** |
| AI images (SD 1.5, ADM, GLIDE, VQDM, Wukong, BigGAN) | 16 % | 34 % | **50 %** |
| **Midjourney (never seen in training)** | 32 % | 46 % | 22 % |
| Exact copy of a minted / protected work | 0 % | 0 % | **100 %** |
| Edited copy of a protected painting | **0 %** | 56 % | 44 % |
| Copy-move forgery | 0 % | 0 % | **100 %** |
| Spliced forgery | 2 % | 8 % | **90 %** |
| Edited copy of a pixel-art NFT | 90 % | 10 % | 0 % |

**Limitations, stated honestly:**
- About 5 % of genuine uploads are wrongly rejected, and about 20 % are sent to human review.
- 18 % of all forgeries pass the gate.
- Edited copies of NFTs from a *generative collection* (CryptoPunks) are not caught. Genuine
  siblings in such collections are naturally as similar as copies (pHash/CLIP/ORB ROC-AUC < 0.5),
  so only exact copies are blocked there; the contract enforces this too.

Full methodology: [PROJECT_REPORT.md](PROJECT_REPORT.md).

## Architecture

```
upload ─▶ preprocessing ─▶ ┌ ResNet-18 forgery classifier          ┐
 (validate, RGB,           │ AI detector: CLIP probe + spectral     │   policy engine
  SHA-256, training-       │ copy-move (block matching, voting)     │─▶ (calibrated risk ─▶ PASS ─▶ mint
  identical normalisation) │ pHash  ┐ vs protected collection:      │    + weighted score)  REVIEW ─▶ reviewer
                           │ CLIP   │ 9,000 NFTs + 1,657 paintings  │                       FAIL ─▶ blocked
                           └ ORB    ┘ + everything minted here      ┘          │
                                                                               ▼
       MintGateNFT.sol (Sepolia): recordVerification · resolveReview · mintVerified (1 token per image)
       IPFS: artwork + ERC-721 metadata + full verification report
```

Key engineering decisions:
- **Dataset-bias control.** In GenImage, real photos are JPEGs and fakes are PNGs. Every image
  in both classes gets the same resize + JPEG re-encode, and uploads go through the identical
  "detector view" at inference.
- **Density-relative copy score.** A single similarity threshold can't serve both a generative
  collection (neighbours 0.99 similar) and diverse paintings (0.88). The score measures how far
  an upload stands out above each reference's own nearest neighbour. pHash and ORB only count
  against references in sparse regions.
- **Data-driven thresholds.** Every REVIEW/FAIL threshold is set at a measured 5 % / 1 %
  false-alarm rate on genuine validation art (`training/calibrate.py`), and the hash of the
  policy file is recorded on-chain with every verdict.
- **Durable state comes from the chain.** The hosting disk is ephemeral, so on start-up the
  server rebuilds the set of minted artworks from `VerifiedMint` events plus IPFS.
- **Defence in depth.** The mint rule is enforced by the API and again by the contract
  (`NotMintable`, `ImageAlreadyMinted`).

## Repository layout

| Path | Purpose |
|---|---|
| `backend/detection/` | preprocessing, ResNet-18, AI detector, copy-move, pHash, CLIP, ORB, reference index, policy, pipeline |
| `backend/training/` | data download (`fetch_realworld`), training, index build, calibration, system evaluation, examples |
| `backend/chain/` | web3.py contract client, IPFS pinning |
| `backend/server.py`, `wsgi.py` | Flask API (rate limits, CSP, proxy-aware), gunicorn entry point |
| `Blockchain/contracts/MintGateNFT.sol` | ERC-721 + AccessControl provenance contract (7 tests) |
| `frontend/` | web UI (MetaMask connect, Etherscan/IPFS links) |
| `deploy/` | Dockerfile + one-command Hugging Face Spaces deploy |
| `run_local.ps1` | local dev: Hardhat chain + deploy + server |

## Run locally

Requirements: Python 3.12, Node 18+. An NVIDIA GPU is optional (used for training).

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
.\venv\Scripts\python.exe -m pip install -r requirements.txt
cd Blockchain; npm install; npx hardhat test; cd ..
powershell -ExecutionPolicy Bypass -File .\run_local.ps1      # then open http://127.0.0.1:5000
```

Reproduce the models from `backend/`: `python -m training.fetch_realworld` →
`train_cnn --epochs 10` → `train_ai_detector` → `build_index` → `calibrate` → `evaluate_system`.

## Deploy (Sepolia + Hugging Face Spaces)

1. **Wallet.** Create a dedicated MetaMask account and fund it with Sepolia test ETH
   (Google Cloud Web3 faucet). Put its private key in `Blockchain/.env` as `DEPLOYER_PRIVATE_KEY`
   (see `.env.example`).
2. **Contract.**
   ```powershell
   cd Blockchain
   npx hardhat run scripts/deploy.js --network sepolia
   npx hardhat verify --network sepolia <address> <deployer> <deployer> <deployer>   # optional, needs ETHERSCAN_API_KEY
   ```
   This writes `backend/chain/deployment.json`.
3. **Secrets.** Copy `backend/.env.example` to `backend/.env` and set `HF_TOKEN`,
   `VERIFIER_PRIVATE_KEY` (same key), a random `REVIEWER_KEY` (16+ characters) and `PINATA_JWT`.
4. **App.** `python deploy/hf_space.py --space <hf-username>/mint-gate`.
   The secrets are stored as encrypted Space secrets, never in the repository. The Space builds in about 10 minutes.

## Security

- Upload validation: 10 MB limit, format allow-list, `verify()` before decode, decompression-bomb guard.
- Per-IP rate limits (analyses/hour, mints/day). Strict CSP; framing allowed only by Hugging Face.
- All data rendered with `textContent`. No third-party scripts; the wallet uses the browser's `window.ethereum`.
- In production the server refuses to start without a strong `REVIEWER_KEY` and a verifier key.
  Keys live only in environment secrets; the reviewer key is checked in constant time.
- Model weights load with `torch.load(weights_only=True)`. Classical models are stored as JSON, never pickle.
- Errors to clients are generic; details are only logged server-side.

## Data and licences

Tiny-GenImage (CC BY-NC-SA 4.0), AI Art & Real Art and Real-VS-AI-Art (research use), plus
CryptoPunks images. This is a **non-commercial portfolio/research project**. Automated detection
is probabilistic: a PASS is evidence, not a legal guarantee of authorship.
