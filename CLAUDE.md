# Mint-Gate: project context for Claude Code

AI forensics gate that verifies artwork **before** NFT minting. Blockchain stores verdicts and
enforces "no mint without a passing verdict". Detection is off-chain.
Owner: Manasvi Patil (GitHub / HF: `1005Manasvi`). Goal: a **real-world, blockchain-focused**
portfolio project. Work is split into phases: see `docs/phases/README.md`.
**Always read `docs/phases/PROGRESS.md` first** and update it when a phase finishes.

## Architecture (current state)
- `backend/detection/`: preprocessing → 6 checks → `policy.py` (PASS / REVIEW / FAIL).
  - AI detector = CLIP ViT-B/32 linear probe (`ai_detector.py`, JSON weights). This is the main AI signal.
  - ResNet-18 (`resnet_classifier.py`, `model/best_model.pt`) is **REVIEW-only**
    (`max_status`): it generalises poorly to unseen NFT collections.
  - Copy detection: `reference_index.py` (pHash + CLIP + nearest-neighbour density) and
    `feature_match.py` (ORB). Generative-collection members (`/`-less names = CryptoPunks,
    `art/nft_*`) are protected against **exact copies only**.
  - `copy_move.py`: block matching with shift-vector voting.
- `backend/server.py`: Flask API (`wsgi.py` for gunicorn). Reports are **JSON files in
  `backend/storage/`** (no database yet). Rate limits are in memory.
- `backend/chain/client.py` (web3.py) + `ipfs.py` (Pinata). The startup sync rebuilds minted
  artworks from `VerifiedMint` events.
- `Blockchain/contracts/MintGateNFT.sol`: ERC-721 + AccessControl
  (VERIFIER / REVIEWER roles); recordVerification / resolveReview / mintVerified.
  **Deployed on Sepolia `0xae989b374DF85a942b4caE07940713Ff89418F32`** (block 12044379),
  source verified on Sourcify. Verifier/deployer wallet `0xeed816c3F61fC7A7396159c267658010dB69eD7F`.
  `backend/chain/deployment.json` = Sepolia; `deployment.local.json` = old local copy.
- `frontend/`: vanilla JS, strict CSP (script-src 'self': no CDN scripts, vendor libraries locally).
- Training: `backend/training/` (fetch_realworld, fetch_v2, train_cnn, train_ai_detector,
  build_index, calibrate, evaluate_system, independent_check). Data in
  `backend/data/realworld/` (20,309 images, manifest.json); held-out generator = Flux.
- Results: README.md and PROJECT_REPORT.md. Policy v3.1.0.

## Commands (Windows; venv = Python 3.12 + CUDA torch 2.6)
- Python: `venv\Scripts\python.exe` (run from `backend/` as `-m training.xxx`).
- Server: `cd backend; ..\venv\Scripts\python.exe server.py` → http://127.0.0.1:5000
- Contracts: `cd Blockchain; npx hardhat test`; deploy:
  `npx hardhat run scripts/deploy.js --network sepolia`.
  Source verification: Sourcify **v2 API** (Hardhat's sourcify plugin uses the removed v1 API).
- Local dev chain + server: `powershell -ExecutionPolicy Bypass -File .\run_local.ps1`.

## Hard-won gotchas (do not relearn these)
- **Secrets**: `backend/.env`, `Blockchain/.env`. **Never print or read their values**; check
  presence and format only. Both are git-ignored. `Blockchain/scripts/.env` is an OLD wallet key
  (the owner decides whether to delete it).
- GPU is an RTX 3050 with **4 GB**: run GPU jobs **sequentially** (CLIP + training in parallel → OOM).
- Windows paging file: `ProcessPoolExecutor` workers must not import torch (see
  `training/spectral_worker.py`). Cap at 4 workers.
- venv `python.exe` is a launcher with a child process. To stop a server, kill every
  process whose command line contains `server.py`.
- The `rm` safety check blocks wildcard deletes: delete explicit paths.
- Network is flaky/slow (~1 MB/s): use resumable downloads, parquet row-group reads, HF caches;
  delete caches after processing.
- **Hugging Face Docker Spaces now need PRO.** Free hosting plan = **Azure for Students**
  (Azure CLI installed at `C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd`).
- Long jobs: run in the background with a log file in `backend/storage/`, never block.

## Conventions
- Honest metrics only: held-out data, out-of-source checks, report limitations.
- Every new on-chain feature needs Hardhat tests (and fuzz tests from Phase 1 on).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Commit only when asked or when the phase file says so.
- Keep this file and `docs/phases/PROGRESS.md` current. They are the handoff between chats.
