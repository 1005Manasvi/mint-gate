# Mint-Gate: project context for Claude Code

AI forensics gate that verifies artwork **before** NFT minting. Blockchain stores verdicts and
enforces "no mint without a passing verdict". Detection is off-chain.
Owner: Manasvi Patil (GitHub / HF: `1005Manasvi`). Goal: a **real-world, blockchain-focused**
portfolio project. Work is split into phases: see `docs/phases/README.md`.
**Always read `docs/phases/PROGRESS.md` first** and update it when a phase finishes.

## Architecture (current state)
- `backend/detection/`: provenance + preprocessing → checks → `policy.py` (PASS / REVIEW / FAIL), policy
  **v5.1.0** (`policy.permissive.json` = `5.1.0-permissive` when `MINTGATE_PERMISSIVE_ONLY=1`).
  - AI detector (Phase 5) = linear probes on frozen **CLIP-B/32 + SigLIP 2 B/16** (w 0.3 / 0.7),
    `ai_detector.py` format 2 + `backbones.py` (vision towers only, fp16 safetensors in `model/backbones/`,
    SHA-256 manifest, fetched by `python -m training.ai_backbones fetch`). Chosen by the out-of-source gate
    (`training/ai_backbones.py`, OOS v2 set `training/oos_data.py` → `data/oos/`, select / report halves).
    CPU: `MINTGATE_CPU_INT8=1` = dynamic int8 Linear layers (gated by `benchmark/int8_gate.py`).
    Permissive profile: CLIP-B/32 probe on MIT / CDLA-P data + `training/fetch_permissive.py` extras.
  - `provenance.py` (raw upload bytes): C2PA (`c2pa-python`; AI claim from any intact manifest, positive
    evidence only from a trusted one: `C2PA_TRUST_ANCHORS`), SD/SDXL DWT-DCT watermarks (native sizes only),
    generator metadata, optional reverse search (`REVERSE_SEARCH=vision|tineye`). Check `provenance`.
  - TruFor (`trufor.py`, vendored `third_party/trufor/`, **non-commercial**): REVIEW-only `manipulation`
    check + heatmap, GPU profile only (`MINTGATE_TRUFOR=auto`), never permissive. Weights converted once to
    safetensors with `weights_only=True` (`python -m benchmark.forgery fetch`). ResNet-18 **retired** in
    Phase 5 (chance on real forgeries); `resnet_classifier.py` / `best_model.pt` remain for history only.
  - Copy detection (Phase 4): `copy_engine.py` = PDQ (`copy_exact`, dihedral) + SSCD/DINOv2-S
    fusion + 18 SSCD region crops, density-relative (`copy_semantic`); params `model/copy_engine.json`
    are written by `benchmark.copydet evaluate` (val-tuned). Index: `copy_index.py` (`PgCopyIndex` =
    pgvector HNSW on `reference_items`, one-off works in their own partial HNSW indexes [0005];
    PDQ searched **exactly** from an in-memory cache; `MemoryCopyIndex` for tests). Copy engine
    **1.1.0** (Phase 5b, `benchmark/member_rule.py --ship`): a PDQ-close collection member is an exact
    copy only if pixel-identical (`member_rule` = BOX, dihedral, level 16; `member_max_diff_px` 22 at
    128 px); re-mints are caught by the REVIEW-only `copy_member` check (SSCD of upload + mirror vs
    members: cos − nn_sscd ≥ 0.15, `member_semantic`). Profiles: GPU = 18 SSCD region crops, CPU = none
    (`regions_cpu`, 1.5 s budget). LightGlue (`local_match.py`) was benchmarked and rejected: unused. v1 `reference_index.py` / `hashing.py` / `feature_match.py` / `training/build_index.py`
    are kept only for the benchmark baseline (`build_index` still writes `index/files.json` = seed list).
  - `copy_move.py`: block matching with shift-vector voting.
- **Benchmark + on-chain index (Phase 4):** `backend/benchmark/` (`build_copydet`, `copydet` steps
  features / orb / lightglue / evaluate / latency, `export_onnx`; data in `data/benchmark/copydet/`,
  results `model/benchmarks/`; the v1 baseline uses the frozen `model/benchmarks/policy_v3.1.0.json`).
  `backend/ingest/` (`python -m ingest fetch|seed|embed|sync|stats`): Alchemy NFT API → 48 Ethereum
  collections (cap 6,000 each, `ingest/collections.py`), spool → `embed` (GPU) → 128 px thumbs in
  `storage/nft/thumbs/`; `sync` polls `Transfer(from=0)` in 10-block windows (free tier). Tables
  `nft_collections` + `reference_items` (migrations 0003 / 0004: PDQ `bit(256)`, SSCD / DINOv2 /
  fused `halfvec`, `nn_*`, `collection_member`, `attributes`, `trait_hash`, `status`).
- **Data layer (Phase 2):** Postgres 16 + pgvector and Redis 7 (`docker-compose.yml`).
  `backend/db/` (SQLAlchemy 2 models, `repo.py` = all SQL, `migrate_json.py` legacy import),
  `backend/migrations/` (Alembic; hand-written revisions, `test_models_match_migrations` guards drift).
  `audit_log` is append-only (trigger). Submission id = RQ job id = report id.
- `backend/server.py`: **stateless** Flask API (`wsgi.py`), no torch import, sends no transactions.
  `POST /api/analyze` (image + claim signature; artist = SIWE session wallet) → 202 job; `/api/jobs/<id>`;
  `/api/attest/<id>` (session wallet must be the artist) returns the EIP-712 verdict (PASS → artist
  `submitAndMint`) or `mint` args (REVIEW_APPROVED); `/api/me/artworks`; `/api/networks` (public RPC +
  ABIs). Rate limits: Flask-Limiter on Redis. Images / evidence / metadata still in `backend/storage/`.
- `backend/auth.py` (Phase 3): SIWE (EIP-4361, own parser) + Redis nonces/sessions (`__Host-mg_session`,
  1 h) + CSRF (`X-CSRF-Token` + Origin) + signed-upload claim (`claim_message`, stored on `submissions`).
  JS twins of `SiweMessage.text()` / `claim_message()` live in `frontend/js/wallet.js`: keep them identical.
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
- **Governance + disputes (Phase 6, `docs/governance.md`):** next to the immutable v2 contracts:
  `DisputeResolver` (holds REVIEWER_ROLE; `resolveDispute(id, upheld, reasonHash, reasonURI)` for registry
  reviewers, emits `DisputeReasoned` *before* the registry's events, ERC-4906 refresh), `PolicyRegistry`
  (`setPolicy(sha256, version, uri)`, owner = timelock), `MintGateTimelock` (OZ TimelockController, 1 h,
  proposer = Safe, executor = anyone, no admin), Safe v1.4.1 2-of-3 (canonical proxy; owners `0xeed8…eD7F`,
  `0x1F79…E2E2`, `0xCb03…57c2`). Deployments in the `governance` block of `deployments/<net>.json`:
  Sepolia Safe `0xEdE58D8Ff74e72fc7568e68c8927a68884a290Fb`, timelock `0xC56840B4…104F`, PolicyRegistry
  `0xaa3d8342…78Fe`, resolver `0xdb049075…48Cc`; Base Sepolia Safe `0x66bf57DC…2C67`, timelock `0x1B2c4708…1c7d`,
  PolicyRegistry `0x7d56206F…A017`, resolver `0xC56840B4…104F` (= Sepolia's timelock address!).
  Hand-over done 2026-11-11 (pauser + treasury = Safe; admin transfer begun): **admin becomes the Safe only
  when a second owner signs the `accept-admin` proposal on `/explorer`**. Scripts: `deploy-governance.js`,
  `handover.js`, `scripts/lib/safe.js` (vendored artifacts `Blockchain/vendor/safe-1.4.1/`, LGPL-3.0).
  Backend: `backend/governance/` (`safe.py` EIP-712 SafeTx + proposals table `safe_proposals`, `actions.py`,
  CLI `python -m governance policies|propose|sign|exec|timelock-exec|list`), `backend/disputes/` (`api.py`
  blueprint: reporter evidence, reviewer reasons; `evidence.py`: documents + the backend's evidence = copy
  search / earliest signed submission / original comparison / reverse search, job `dispute_evidence` on the
  analysis queue, queued by the indexer after `DisputeOpened` commits), `backend/explorer.py` (stats, policy
  history, governance, token timeline, Safe signatures), `backend/notify.py` (Discord, `NOTIFY_DISPUTES`,
  `NOTIFY_REVIEWS`, chain-queue jobs). Reviewers = SIWE wallets with on-chain REVIEWER_ROLE (cached 60 s);
  `REVIEWER_KEY` is a fallback (`REVIEWER_KEY_DISABLED=1`). Migration 0006 (`dispute_evidence`, dispute
  reason / backend evidence columns, `safe_proposals`, `chain_events.block_time`). Pages: `/explorer`,
  Disputes tab, token page timeline + "Report this token". Policies pinned: `backend/chain/policies.json`.
- `frontend/`: vanilla-JS ES modules in `js/` (app, wallet, provenance, canonical, ui, token), strict CSP
  (script-src 'self': no CDN scripts). ethers **6.17.0** vendored as `vendor/ethers-6.17.0.min.js` (copied
  from `Blockchain/node_modules/ethers/dist/ethers.min.js`; prettier-ignored). Public token page
  `/token/<chain>/<contract>/<id>` (`token.html`) verifies chain + report hash in the browser;
  `js/canonical.js` must stay byte-compatible with `detection/report_hash.py` (shared vector
  `backend/tests/fixtures/canonical_vector.json`, regenerate with `python -m tests.test_canonical_vector`).
- Training: `backend/training/` (fetch_realworld, fetch_v2, ai_backbones, oos_data, lora_finetune,
  fetch_permissive, retrain_gate, provenance_eval, stats (Wilson / bootstrap CIs), calibrate,
  evaluate_system (n <= 300 per category, sampled without replacement, resumable), independent_check (v1)).
  Data in `backend/data/realworld/` (20,309 images, manifest.json); held-out generator = Flux.
  Forgery benchmark: `benchmark/build_forgery.py` + `benchmark/forgery.py` (`data/benchmark/forgery/`).
- **NFT-native evaluation (Phase 5b):** `training/copymint_data.py` (RR/BAYC, CryptoPhunks V2, PHAYC →
  `data/copymint/`, never in `reference_items`), `training/copymint_eval.py` (full pipeline per copy,
  miss causes; `--copy-params` for before/after), `training/ai_nft_data.py` (12 BrainDrops projects +
  Bastard GAN Punks V2 → OOS group `ai_nft:*`, originals in `data/ai_nft/raw/`, `as_upload` for > 10 MB
  files), `training.provenance_eval --nft`, `evaluate_system` categories `copymint_*` / `ai_nft_collection`.
- Results: README.md, PROJECT_REPORT.md, `docs/models.md` (model register + benchmark). Policy v5.1.0. Tests: `backend/tests/` (pytest), `Blockchain/test/` (Hardhat) + `Blockchain/test/foundry/`; security review: `docs/security.md`; governance + runbooks: `docs/governance.md`.

## Commands (Windows; venv = Python 3.12 + CUDA torch 2.6)
- Python: `venv\Scripts\python.exe` (run from `backend/` as `-m training.xxx`).
- Data services: `docker compose up -d` (Postgres :5432 db `mintgate` + `mintgate_test`, Redis :6379);
  migrations: `cd backend; ..\venv\Scripts\python.exe -m alembic upgrade head`.
- Server: `cd backend; ..\venv\Scripts\python.exe server.py` → http://127.0.0.1:5000. Also needed:
  `-m jobs.worker analysis` (GPU, `--preload`), `-m jobs.worker chain`, `-m indexer` (`--once`,
  `--from-deploy`, `--network`). Local run: `CHAIN_NETWORKS=localhost DEFAULT_NETWORK=localhost`.
- Governance (Phase 6): `npx hardhat run scripts/deploy-governance.js --network <net>` (env `SAFE_OWNERS`,
  `SAFE_THRESHOLD`, `TIMELOCK_DELAY`; needs `python -m governance policies --pin` first), then `scripts/handover.js`;
  Safe actions: `cd backend; ..\venv\Scripts\python.exe -m governance propose <net> <action> --sign`.
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
- Frontend unit tests: `node --test "frontend/tests/*.test.mjs"`. Browser E2E: `cd e2e; npx playwright test`
  (starts its own Hardhat node :8547 + `e2e/stack.py` :5057 = API + fake-model worker + indexer, DB
  `mintgate_e2e`, Redis db 15; needs compose + compiled contracts). `PW_CHANNEL=chrome` uses installed Chrome.
- CI: `.github/workflows/ci.yml` (python, contracts [+ chain pytest], foundry, slither, e2e, lint). Accepted findings: `docs/security.md`.

## Hard-won gotchas (do not relearn these)
- **Phase 6:** Sepolia now charges ~204,600 gas for a plain ETH transfer to a *new* address (state-creation
  repricing; also why contract creation costs 7×): never hard-code 21,000, always `eth_estimateGas`.
  Node's `fetch` to sourcify.dev fails intermittently (`UND_ERR_CONNECT_TIMEOUT`) while curl works: retry
  `verify-sourcify.js` (it skips verified contracts). Sourcify reports the timelock as a partial `match`
  although its runtime code is byte-identical to the local build. `vm.prank` is consumed by the *first*
  external call, including a getter in the argument list (`grantRole(role, address(handler.resolver()))`
  pranks the getter). `flushall()` on the test fakeredis also wipes SIWE sessions: delete only the
  `mintgate:role:*` keys. A Slither `incorrect-equality` hit on an array length is Medium (fails CI): suppress
  inline with a reason. The test deployment in conftest has a `governance` block (owners = `OWNER_KEYS`;
  ABIs from a real deployment file or compiled artifacts). Requests drops `__Host-` (Secure) cookies on
  http://127.0.0.1: scripts must clear `cookie.secure` after SIWE login.
- **Phase 5b:** `repo.add_nft_items` must set `collection_member` (before 5b only migration 0004 did, so
  every later backfill / sync row was a one-off and its siblings "copied" it). Plain `ingest embed` does
  not fill `nn_sscd` (only `--follow` / `--rebuild-nn`): it now warns; `ingest.embed.update_nn` repairs a
  few thousand rows (~40 min for 4k). Re-uploads must be tested with the token's *file*, not the 128 px
  thumbnail (Phase 4's 98 % did that). The 128 px JPEG thumbnails are too lossy to prove identity of
  detailed art (open: larger references). Seed CryptoPunks carry red / purple "for sale" backgrounds.
  Level 48 hides dark-on-dark punk traits (moles, beards). Alchemy `searchContractMetadata` is gone
  (use public pages + `getContractMetadata`); its CDN serves some files as AVIF (not uploadable) and
  `cdn-cgi/image/width=N` renditions; ipfs.io / dweb.link refuse scripts, Pinata's gateway works.
  BrainDrops originals reach 44 MB / 100 MP (upload limit 10 MB / 40 MP). Docker Desktop crashed once
  under memory pressure (GPU eval + CPU eval + Postgres): run long jobs one at a time.
- **Phase 5:** the laptop runs out of *commit* memory (page file can't grow when C: is full; another
  project's jobs share the machine): long jobs crash with MemoryError / "bad allocation" / access
  violations in MKL. Every long job checkpoints and resumes (`benchmark.forgery run`, `evaluate_system`,
  `lora_finetune --score-only`); run GPU jobs one at a time; keep C: above ~3 GB free. Loading many
  models into ONE CPU process crashes (`benchmark.latency components`): time new steps with
  `benchmark.latency_ai` instead. Fetch only vision towers (`backbones.fetch_vision`, HTTP range reads:
  SigLIP 2's checkpoint is 80 % text tower). transformers 5: SigLIP's vision class has no `vision_model.`
  prefix and DINOv2 keys were renamed (load via `from_pretrained`). Never `torch.load(weights_only=False)`
  a downloaded checkpoint (blocked, and rightly): allow-list globals by exact name, convert to safetensors.
  OpenAI's C2PA certificates are *expired*: credential codes are not integrity failures. SD/SDXL
  DWT-DCT watermarks die on one JPEG q90 save and need natural texture to decode. TruFor was trained on
  CASIA v2 + IMD2020: those are not out-of-source for it. OOS photos (DOCCI) are flagged by every probe.
  Bash heredocs collapse `\\n` too: patch files with Python scripts written by the Write tool.
- **Phase 4:** SSCD TorchScript returns a constant vector in fp16: run it fp32 (SHA-256 pinned).
  SD 1.5 img2img overflows the 4 GB GPU into shared memory after a while (10× slower): restart it.
  The laptop **sleeps** during long jobs unless sleep-on-AC is off. Bulk pgvector loads: drop the
  HNSW indexes, load, recreate. Alchemy free tier: `eth_getLogs` max 10 blocks; Base mainnet must be
  enabled on the Alchemy app (dashboard). `pdqhash` has no Linux wheel (sdist: needs a compiler).
  Raw-SQL halfvec values come back as text: parse with `copy_index._array`. Postgres needs
  `shared_buffers` ≥ 1 GB for the 1.2 GB of HNSW graphs (compose `command:`), else p95 is 130-300 ms.
  One HNSW graph over 280k clustered NFTs found painting sources only 51 % of the time (partial
  indexes fix it); HNSW over PDQ bits misses ~6 % (hence the exact cache). Thresholds calibrated on
  the 4k-reference benchmark did NOT transfer to the 285k index (SSCD member rule failed 36 % of
  genuine punks): always re-check with `training.evaluate_system` against the live index.
  Bulk `UPDATE`s of reference_items: drop the HNSW indexes first (`ingest.embed.drop_hnsw`).
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
  The Write tool may decode `\uXXXX` in JS string literals: build such test strings from parts.
- Quote workflow step names that contain `: ` (invalid YAML → CI runs zero jobs; bitten twice).
- Windows maps `.js` to `text/plain` via the registry: `server.py` registers `text/javascript`
  (with nosniff, ES modules are refused otherwise).
- Pinata `pinJSONToIPFS` re-serialises JSON (`1.0` → `1`), breaking report hashes: pin reports as
  files (`metadata.publish` does). Fake models for non-pytest use: `tests/fakes.py` (conftest pins env).
- `cdn.playwright.dev` sometimes fails DNS here: retry `npx playwright install chromium` or use `PW_CHANNEL=chrome`.

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
