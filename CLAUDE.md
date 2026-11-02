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
- **Data layer (Phase 2):** Postgres 16 + pgvector and Redis 7 (`docker-compose.yml`).
  `backend/db/` (SQLAlchemy 2 models, `repo.py` = all SQL, `migrate_json.py` legacy import),
  `backend/migrations/` (Alembic; hand-written revisions, `test_models_match_migrations` guards drift).
  `audit_log` is append-only (trigger). Submission id = RQ job id = report id.
- `backend/server.py`: **stateless** Flask API (`wsgi.py`), no torch import, sends no transactions.
  `POST /api/analyze` (image + `artist`) → 202 job; `/api/jobs/<id>`; `/api/attest/<id>` returns the
  EIP-712 verdict (PASS → artist `submitAndMint`) or `mint` args (REVIEW_APPROVED). Rate limits:
  Flask-Limiter on Redis. Images / evidence / metadata still in `backend/storage/` (shared disk).
- `backend/jobs/`: RQ queues `analysis` (pipeline, models loaded once, syncs `reference_items`) and
  `chain` (submit REVIEW/FAIL verdicts, `resolveReview`; Redis nonce lock). `backend/indexer/`:
  registry + NFT events → `chain_events` → `tokens` / `disputes` (cursor + reorg check in DB).
- `backend/chain/`: `networks.py` (deployments/*.json + per-network env), `registry.py` (v2 client),
  `eip712.py`, `metadata.py` + `ipfs.py` (Pinata). v1 client deleted.
- `Blockchain/contracts/MintGateNFT.sol`: ERC-721 + AccessControl
  (VERIFIER / REVIEWER roles); recordVerification / resolveReview / mintVerified.
  **Deployed on Sepolia `0xae989b374DF85a942b4caE07940713Ff89418F32`** (block 12044379),
  source verified on Sourcify. Verifier/deployer wallet `0xeed816c3F61fC7A7396159c267658010dB69eD7F`.
  `backend/chain/deployment.json` = Sepolia; `deployment.local.json` = old local copy.
  v1 is history only: **the app uses v2 since Phase 2**.
- **Contracts v2** (`Blockchain/contracts/v2/`, design + threat model in `docs/contracts.md`):
  `MintGateRegistry` (EIP-712 verdicts submitted by anyone, review, `voidVerdict`, one mint per
  image across collections via `consume` [COLLECTION_ROLE], disputes with bond + pull payments,
  permanent revocation flag, pause, `AccessControlDefaultAdminRules` 1 h delay), `MintGateNFTV2`
  (artist `mint` / `submitAndMint`, ERC-2981 ≤ 10 %, revoked → `REVOKED_URI`), `MintGated` + `IMintGate`.
  Deployed (all Sourcify exact match), addresses + ABIs in `backend/chain/deployments/<network>.json`:
  Sepolia registry `0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB`, NFT `0x59900d4F93fAb95F3B8aFBD4E9fEbF15Fa0F4f22`;
  Base Sepolia registry `0xae989b374DF85a942b4caE07940713Ff89418F32` (= v1's Sepolia address!), NFT `0xf45c9cB2…49fB`.
  Python signer: `backend/chain/eip712.py` (`Verdict.from_report`, `sign_verdict`).
- `frontend/`: vanilla JS, strict CSP (script-src 'self': no CDN scripts, vendor libraries locally).
- Training: `backend/training/` (fetch_realworld, fetch_v2, train_cnn, train_ai_detector,
  build_index, calibrate, evaluate_system, independent_check). Data in
  `backend/data/realworld/` (20,309 images, manifest.json); held-out generator = Flux.
- Results: README.md and PROJECT_REPORT.md. Policy v3.1.0. Tests: `backend/tests/` (pytest), `Blockchain/test/` (Hardhat) + `Blockchain/test/foundry/`; security review: `docs/security.md`.

## Commands (Windows; venv = Python 3.12 + CUDA torch 2.6)
- Python: `venv\Scripts\python.exe` (run from `backend/` as `-m training.xxx`).
- Data services: `docker compose up -d` (Postgres :5432 db `mintgate` + `mintgate_test`, Redis :6379);
  migrations: `cd backend; ..\venv\Scripts\python.exe -m alembic upgrade head`.
- Server: `cd backend; ..\venv\Scripts\python.exe server.py` → http://127.0.0.1:5000. Also needed:
  `-m jobs.worker analysis` (GPU, `--preload`), `-m jobs.worker chain`, `-m indexer` (`--once`,
  `--from-deploy`, `--network`). Local run: `CHAIN_NETWORKS=localhost DEFAULT_NETWORK=localhost`.
- Contracts: `cd Blockchain; npx hardhat test`; deploy v1: `npx hardhat run scripts/deploy.js --network sepolia`;
  v2: `npx hardhat run scripts/deploy-v2.js --network <sepolia|baseSepolia>` then
  `npx hardhat run scripts/verify-sourcify.js --network <...>` (Sourcify **v2 API**; Hardhat's
  sourcify plugin uses the removed v1 API). Gas table: `REPORT_GAS=true npx hardhat test` → `gas-report.txt`.
- Foundry: forge 1.8.7 is in `~/.foundry/bin` (not on PATH: `export PATH="$HOME/.foundry/bin:$PATH"`).
  `cd Blockchain; forge test` (fuzz + invariants, `test/foundry/`; `FOUNDRY_PROFILE=ci` = CI depth).
  forge-std comes from npm (`github:` dependency), remapped in `foundry.toml`; no git submodules.
- Local dev chain + server: `powershell -ExecutionPolicy Bypass -File .\run_local.ps1`.
- Tests (repo root): `venv\Scripts\python.exe -m pytest -m "not slow"` (`-m slow` = real models).
  Contracts: `cd Blockchain; npm run coverage` (gate >= 95 %). Lint: `venv\Scripts\pre-commit run --all-files`.
- Slither: install it in a **separate venv** (scratchpad), not the app venv; run `slither . --fail-medium`
  in `Blockchain/` (`slither.config.json` excludes OpenZeppelin + `contracts/mocks`, forces the
  hardhat framework because `foundry.toml` exists). It runs `hardhat clean --global`.
- Python tests marked `chain` start a local Hardhat node (`test_eip712.py`, `test_indexer.py`); they skip
  without compiled artifacts. DB tests use `TEST_DATABASE_URL` (default: compose `mintgate_test`; schema
  rebuilt by Alembic per session, truncated per test) and skip without Postgres unless `REQUIRE_DB=1` (CI).
- CI: `.github/workflows/ci.yml` (python, contracts [+ chain pytest], foundry, slither, lint). Accepted findings: `docs/security.md`.

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
- **Sepolia deployments are expensive now**: v2 used ~35M gas there (7× Hardhat / Base) and ethers'
  default 1 gwei tip cost 0.035 ETH. `deploy-v2.js` caps the tip (`PRIORITY_FEE_GWEI`, default 0.01).
  Always estimate with `gasUsed × (baseFee + tip)` from a dry run, not `getFeeData()` alone.
- Contract addresses repeat across chains (same deployer + nonce): always state the chain with an address.
- **Never reformat or edit `Blockchain/contracts/MintGateNFT.sol`** (v1): it must stay byte-identical
  to the Sourcify-verified deployment (it is in `.prettierignore`). New contracts go in `contracts/v2/`.
- Git history stores files with CRLF; ruff and prettier keep line endings as they are. Do not mass-normalise.
- Tests: `server.py` has no import side effects any more; workers get fake models via
  `jobs.tasks.set_pipeline`, the chain via the `FakeRegistry` fixture, Redis via fakeredis
  (**needs `fakeredis[lua]`**: redis-py locks use Lua). RQ jobs run with an in-process burst worker.
- **RQ on Windows:** use `SimpleWorker` (no fork). Never `with_scheduler=True`: RQ's scheduler child
  process survives a hard-killed worker and holds the lock forever (retries stop). `jobs/worker.py` runs
  the scheduler in a daemon thread; `maintenance_interval=60, worker_ttl=75` → crash recovery ~3.5 min.
- Killing processes by command line: a PowerShell filter like `*jobs.worker*` also matches the PowerShell
  process running it; filter on `Name -eq 'python.exe'` or kill by PID.
- Bash heredocs in this harness collapse `\\` to `\`: write Python edit scripts with the Write tool.

## Model-selection rules (apply in every phase)
1. **Benchmark before adopting.** A new model replaces the current one only if it wins on the task's
   benchmark: copy detection → `backend/benchmark/copydet.py` (micro-AP, TPR at 1 % FPR, per domain);
   AI detection → out-of-source protocol (`training/independent_check.py`). "Newer" alone is not a reason.
2. **Generalisation over in-distribution accuracy.** v1 lesson: ResNet-18 scored 0.974 AUC in-distribution
   but only 64 % on unseen NFT collections; the frozen CLIP probe scored 100 %. Always test out-of-source.
3. **Domain-aware evaluation.** Report paintings / one-off art, generative-collection NFTs, photos and
   pixel art separately. Collection siblings are not copies.
4. **Hardware budget.** Training ≤ 4 GB VRAM (base/small backbones, AMP, frozen features + probes or LoRA).
   Deployed inference: CPU, ≤ 1.5 s per upload (export to ONNX, fp16/int8 when accuracy holds).
5. **Licences.** Record every model's weight licence in `docs/models.md`; prefer permissive (Apache/MIT/BSD).
   Mark research-only weights clearly, and keep a permissive-only configuration working.
6. **Fewer models wins ties.** Retire a model when a better one covers its role.

## Conventions
- Honest metrics only: held-out data, out-of-source checks, report limitations.
- Every new on-chain feature needs Hardhat tests (and fuzz tests from Phase 1 on).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Commit only when asked or when the phase file says so.
- Keep this file and `docs/phases/PROGRESS.md` current. They are the handoff between chats.
