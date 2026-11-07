# Progress log

Each phase appends a short entry when it finishes: what was done, key numbers, open issues,
and anything the next phase must know. Keep entries brief: this file is read at the start of every chat.

## Phase -1: v1 baseline (done, 2026-11-02)
- Models final: CLIP-probe AI detector (test AUC 0.982, unseen Flux AUC 0.990, out-of-source:
  100 % genuine NFTs / 94 % Flux), ResNet-18 REVIEW-only (test AUC 0.974, OOD weak).
- Policy v3.1.0. End-to-end: 3 % genuine wrongly failed, 85.8 % of forgeries stopped.
- MintGateNFT v1 on Sepolia `0xae989b374DF85a942b4caE07940713Ff89418F32`, Sourcify exact match.
- Local git repo has 2 commits (not pushed). No public deployment (HF Docker needs PRO).
- Accounts ready: HF (token in backend/.env), Pinata JWT, Sepolia wallet funded with 0.05 ETH.
- Pending owner actions: create GitHub repo `1005Manasvi/mint-gate`; sign up for Azure for Students.

## Phase 0: Foundation (done, 2026-11-02)
- **pytest** `backend/tests/`: 72 fast tests (~30 s, ~20 s of it is the torch/transformers import)
  + 3 `slow` (real CLIP / probe / ResNet). API tests use fake models, a 2-image fixture index and
  an in-memory `FakeChain` that enforces the contract rules (`tests/conftest.py`). conftest pins
  env vars **before** `config` loads, so tests never use `backend/.env`, Pinata or a real node.
- **Bug found and fixed:** decompression-bomb guard only worked above 80 MP (Pillow raises only
  at 2x its limit). `prepare()` now checks the header pixel count.
- **Contracts:** 13 Hardhat tests, **100 %** lines/branches; `npm run coverage` fails < 95 %.
  **Slither 0.11.6:** 0 high/medium; 2 low (`reentrancy-benign/-events` after `_safeMint`)
  justified in `docs/security.md`. v1 source left byte-identical (Sourcify exact match).
- **Tooling:** ruff (lint + format, py312, 120 cols) + Prettier 3 (Solidity plugin) via
  `.pre-commit-config.yaml`; whole codebase formatted (commit in `.git-blame-ignore-revs`).
  CI `.github/workflows/ci.yml`: jobs python, contracts, slither, lint. MIT LICENSE,
  CONTRIBUTING.md, README badges.
- **Pushed** to https://github.com/1005Manasvi/mint-gate (public). First CI run on `85b905f`:
  all 4 jobs green (python 84 s, contracts 34 s, slither 31 s, lint 31 s).
- Wallet `0xeed8…eD7F`: ~0.036 Sepolia ETH, 0.0002 Base Sepolia ETH (faucet, 2026-11-02).
- **For Phase 1:** v2 must follow checks-effects-interactions (set URI + emit before `_safeMint`)
  and pin `pragma solidity 0.8.24;`, so Slither is fully clean. Format v2 with Prettier (v1 is in `.prettierignore`).

## Phase 1: Contracts v2 (done, 2026-11-02)
- **Design** in `docs/contracts.md` (state machines for verdict / image / dispute, interface,
  roles, immutable-contracts-+-pointer upgrade strategy, threat model, invariants, gas, deployments).
- **Contracts** `Blockchain/contracts/v2/`: `MintGateRegistry` (EIP-712 `Verdict` with nonce +
  30-day max window; anyone submits; signer must hold VERIFIER_ROLE at submission; REVIEW
  resolution; `voidVerdict` for unused verdicts; `consume` = one mint per image across all
  collections, COLLECTION_ROLE only; disputes keyed by **imageHash** (not tokenId, so they work for
  any collection) with a snapshotted bond, pull payments, artist strikes; revocation is a permanent
  flag; PAUSER pauses / ADMIN unpauses; `AccessControlDefaultAdminRules`), `MintGateNFTV2`
  (ownerless; artist `mint` / `submitAndMint`, front-run-safe; ERC-2981 ≤ 10 %, 0 when revoked;
  `REVOKED_URI` + ERC-4906 `refreshMetadata`), `MintGated` + `IMintGate` for third-party collections.
  v1 untouched.
- **Tests:** Hardhat 91 (78 v2), **100 %** coverage. Foundry: 11 fuzz properties (1k runs, CI 5k) +
  4 invariants (one token per image; no mint without valid verdict; revoked stays revoked;
  solvency) over a 13-action handler (256×128, CI 512×256). Python `backend/chain/eip712.py`
  signer: byte-identical to ethers, accepted on a local Hardhat node (`pytest` marker `chain`).
  86 fast pytest pass. **Slither: no v2 findings** (4 justified inline suppressions, docs/security.md).
- **Deployed + Sourcify exact match (all 4):** Sepolia registry `0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB`,
  NFT `0x59900d4F93fAb95F3B8aFBD4E9fEbF15Fa0F4f22`; Base Sepolia registry
  `0xae989b374DF85a942b4caE07940713Ff89418F32` (same address as v1 on Sepolia: always name the chain),
  NFT `0xf45c9cB2358f1FC2D977Cff6d6661521e16349fB`. Files: `backend/chain/deployments/<network>.json`.
  Etherscan/Basescan not verified (no API key). Deployer holds all roles on testnets.
- **Gas:** submitAndMint ~410k (artist pays), submitVerdict ~189k. **Sepolia surprise:** deploy used
  35.2M gas (7× Hardhat/Base), and ethers' default 1 gwei tip made it cost **0.035 ETH**; the wallet
  then had ~0.0007 Sepolia ETH; topped up to **0.2209 Sepolia ETH** via the PoW faucet on 2026-11-03
  (Base Sepolia **0.0051**, Coinbase faucet). `deploy-v2.js` now caps the tip.
  Sepolia calls cost ~3–7× the Hardhat gas too. Target balances: **0.05 Sepolia ETH** (min 0.01),
  **0.01 Base Sepolia ETH** (min 0.003; each dispute locks a 0.001 ETH bond).
- CI: new `foundry` job (forge 1.8.7, `FOUNDRY_PROFILE=ci`, ~2 min) and the chain pytest in `contracts`.
- **Owner actions:** top up Sepolia ETH (faucet) before Phase 2 sends transactions there; optional
  Etherscan API key (one v2 key covers Basescan) for explorer verification.
- **For Phase 2/3:** the backend should sign PASS verdicts with `Verdict.from_report` + `sign_verdict`
  and return `verdict.to_json()` + signature; it submits FAIL / REVIEW itself (`submitVerdict`).
  Index `VerdictSubmitted`, `ImageMinted`, `DisputeOpened/Resolved`, `ImageRevoked` from the registry
  (the NFT's own event is only `VerifiedMint`). Prefer Base Sepolia for frequent transactions.

## Phase 2: Database, job queue, backend on contracts v2 (done, 2026-11-03)
- **Infra:** `docker-compose.yml` = `pgvector/pgvector:pg16` (pgvector 0.8.7) + `redis:7-alpine` (AOF),
  bound to 127.0.0.1; `deploy/postgres-init.sql` creates `mintgate_test`. Config: `DATABASE_URL`,
  `REDIS_URL`, `CHAIN_NETWORKS` (default `baseSepolia`), `DEFAULT_NETWORK`, per-network
  `RPC_URL_<NET>` / `CONFIRMATIONS_<NET>` / `PRIORITY_FEE_GWEI_<NET>` / `VERIFIER_PRIVATE_KEY_<NET>`.
- **Schema** (SQLAlchemy 2 + Alembic `0001`, hand-written, up/down tested, ORM drift test): artists,
  submissions (id = RQ job id = report id), reports (checks + body JSONB, evidence in
  `storage/evidence/`), attestations (uint256 nonce NUMERIC(78), unique per chain + registry),
  indexer_cursors, chain_events (unique chain_id+tx+log_index), tokens, disputes, audit_log
  (**trigger makes it append-only**), reference_items (signed-BIGINT pHash, `vector(512)` + HNSW cosine).
- **Async API:** `POST /api/analyze` (now needs `artist` wallet) → `202 {job_id}`; `GET /api/jobs/<id>`;
  frontend polls. API imports no torch and sends no transactions (`wsgi.py`: any number of
  gunicorn workers). Rate limits: Flask-Limiter on Redis. `/api/mint` is gone →
  `POST /api/attest/<id>`: PASS = EIP-712 verdict bound to the artist (24 h, reused while
  valid) + token URI → artist calls `submitAndMint`; REVIEW_APPROVED → `mint(imageHash, …)`.
- **Workers** (`python -m jobs.worker analysis|chain`, RQ 3 `SimpleWorker`: no fork, models loaded once):
  analysis queue runs the pipeline and syncs new `reference_items` into its index before each job;
  chain queue submits REVIEW / FAIL verdicts (`ANCHOR_FAILED_VERDICTS=1`) and mirrors reviews
  (`resolveReview`), under a **Redis nonce lock per sender**, retried with back-off.
- **Indexer** (`python -m indexer [--network N] [--from-block B | --from-deploy] [--once]`):
  registry + NFT events → chain_events → tokens / disputes projections (rebuildable); cursor +
  block hash in Postgres; N confirmations; reorg → rewind 64 blocks + rebuild. Marks
  attestations submitted, submissions MINTED, adds minted art to reference_items. Replaces the
  startup sync; `chain/client.py` (v1) deleted. **The app no longer uses v1.**
- **Legacy import:** `python -m db.migrate_json` (idempotent, `--dry-run`): 31 v1 reports imported
  into the dev DB (8 minted → `reference_items` source `mintgate-v1`, fingerprinted by the worker).
- **Tests:** 110 pytest (5 `chain`): repo layer 9, API + job flow 19, jobs 2, indexer on a Hardhat
  node 4 (projection, `--from-deploy` and empty-DB rebuild reproduce identical state,
  confirmations, `evm_revert` reorg, chain worker anchors + resolves a REVIEW). fakeredis[lua] for RQ.
  CI: Postgres service in `python` + `contracts` jobs, `REQUIRE_DB=1` (DB tests may not skip);
  indexer tests run in `contracts` (compiled artifacts).
- **Measured on the live local stack** (Hardhat + v2, 2 API instances, real-model GPU worker,
  chain worker, indexer): 40 concurrent submits over both APIs → all done, no conflicts; submit
  latency median 76 / **p95 195 ms** under load (8 clients, Flask dev servers, GPU busy),
  **42 / 55 ms** uncontended; artist minted with the attestation and paid gas (~493k gas);
  indexer → MINTED; re-upload from another wallet → FAIL vs `minted/<id>`; `/verify` matches
  chain; `--from-deploy` re-index → identical tokens (md5). **Worker killed mid-job → re-run
  automatically after 210 s.**
- **Bugs found + fixed:** (1) RQ's scheduler child process **outlives a hard-killed worker on Windows**
  and holds the scheduler lock forever, so retries never ran → scheduler runs in a daemon thread
  (`jobs/worker.py`); (2) idle RQ workers only run maintenance every ~7–10 min → `maintenance_interval=60`,
  `worker_ttl=75` (recovery went from 14.5 min to 3.5 min); (3) an idempotent `resolve_review` re-run
  erased the stored tx hash.
- **Not verified live:** a REVIEW verdict through the real models (no demo sample scores REVIEW);
  covered by API tests (fake registry) and the Hardhat chain-worker test. Nothing was sent to
  Sepolia / Base Sepolia in this phase (no testnet gas spent).
- **For Phase 3:** the artist address is still *claimed*, not proven: add SIWE + signed submission and
  bind `submissions.artist_id` to the session wallet; mint from the wallet with vendored viem
  (`/api/attest` already returns `call` + args); "My artworks" can query submissions / tokens by
  wallet (indexes exist). `deploy/Dockerfile` + `hf_space.py` are obsolete (Phase 7 rebuilds deploy
  as api / worker / indexer). Run the indexer for `baseSepolia,sepolia` once real mints start.

## Phase 3: Wallet identity and artist-side minting (done, 2026-11-03)
- **SIWE (EIP-4361)** in `backend/auth.py` (own parser + `SiweMessage.text()`, no SIWE library):
  `GET /api/auth/nonce` (Redis, 5 min, single use via `GETDEL` after all other checks),
  `POST /api/auth/verify` (domain in `SIWE_DOMAINS`, URI on that domain, configured chain id, EIP-55,
  issued ≤ 10 min / not future, exp / nbf, EIP-191 signer), `GET /api/auth/me`, `POST /api/auth/logout`.
  Session: `__Host-mg_session` cookie (HttpOnly, Secure, SameSite=Strict, 1 h), Redis keeps only the
  token's SHA-256. CSRF: `X-CSRF-Token` (per session) + Origin check on every state-changing call.
- **Signed submission:** wallet personal_signs domain + address + SHA-256 of the exact file bytes +
  time; `/api/analyze` requires a session and that signature (artist = session wallet; the `artist`
  form field is gone). Alembic `0002` adds `submissions.file_sha256 / claim_message / claim_signature`;
  `report.claim` is public via the API, excluded from the IPFS report. `/api/attest` only for the
  artist's session wallet (403 otherwise).
- **Frontend** rewritten as ES modules (`frontend/js/`), ethers **6.17.0** vendored (`frontend/vendor/`,
  MIT). Network selector (Sepolia / Base Sepolia / local from `deployments/*.json`, switch + add via
  `wallet_switchEthereumChain` / `wallet_addEthereumChain`), mint from the artist wallet: attestation →
  gas estimate + max cost → wallet → pending (tx link) → confirmations n / N → token link; custom
  errors of NFT **and** registry decoded into plain sentences. Royalty input (≤ 10 %). Raw call data
  kept under "Advanced". **My artworks** tab (`GET /api/me/artworks`: submissions, verdicts, tokens,
  disputes). **Public provenance page** `/token/<chain>/<contract>/<id>` (`token.html`): indexer view +
  "Verify integrity" = 9 checks read from the chain (wallet if on that chain, else the public RPC) and
  content (report hash recomputed in the browser, pixel hash of the image, registry from the NFT itself).
- **CSP kept strict** (`script-src 'self'`); `connect-src` = self + configured chains' *public* RPC
  (`PUBLIC_RPC_<NET>`, never `RPC_URL_<NET>`) + IPFS gateway; `img-src` gains `blob:`.
- **Bugs found + fixed:** (1) upload preview was blocked by the CSP since Phase 0 (`blob:` URL), caught
  by the E2E CSP watcher; (2) `pinJSONToIPFS` lets Pinata re-serialise JSON (`1.0` → `1`), so an IPFS
  report could never be re-hashed: reports are now pinned as exact bytes (`pinFileToIPFS`); (3) mobile
  layout hid the tabs completely (<900 px): now a scrollable tab bar; (4) Windows `.js` MIME type.
- **Accessibility / mobile:** WAI-ARIA tabs with arrow keys, skip link, focus-visible outlines,
  keyboard dropzone, live regions for job / mint / verification status, alerts instead of `alert()`,
  reduced-motion, empty and error states (no wallet, not signed in, no artworks, not indexed), checked
  at 390 px and 1280 px.
- **Tests:** 127 fast pytest (+17: `test_auth.py` SIWE valid / expired / exp / nbf / future / wrong
  domain / wrong URI / wrong signer / wrong chain / replayed + unknown nonce / parser round trip;
  cookie flags, CSRF, Origin, logout, attest-by-other-wallet, my artworks, networks + CSP; signed-upload
  tests in `test_api.py`; `test_canonical_vector.py`). Node: 5 (`frontend/tests/`, JS hash ==
  Python hash on a shared tricky vector). **Playwright 2 tests, green** (bundled Chromium and Chrome):
  no-session 401s; full journey on a Hardhat node (SIWE → signed upload → PASS → other wallet gets
  403 → mint paid by the artist, 2 confirmations with 1 s interval mining → indexer → My artworks
  "minted" → token page, all 9 checks ✓, zero CSP violations). New CI job `e2e`.
- **Not done / for later:** no live testnet mint this phase (owner's choice: first real MetaMask mint on
  Base Sepolia is a manual step); EIP-1271 (smart-contract wallets) not supported; reviewer still uses
  `REVIEWER_KEY` (wallet-based reviewers → Phase 6); no negative E2E for a tampered report (covered by
  the Node + pytest hash tests).
- **For Phase 4:** `/api/token/<net>/<contract>/<id>` already accepts any collection address, and the
  provenance page works for any `MintGated` collection (it reads the registry from the NFT). Run the
  indexer for `baseSepolia,sepolia` once real mints start.

## Phase 4: Benchmark-gated copy engine + on-chain NFT index (done, 2026-11-04)
- **Benchmark first** (`backend/benchmark/`, data in `data/benchmark/copydet/`, results `model/benchmarks/`):
  4,000 refs, 600 attacked copies, 5,967 distractors (~10:1; NFT siblings, paintings, photos, same
  attack mix), 14 attacks incl. collage + SD 1.5 img2img re-generation (36 NFT regen distractors
  not generated: GPU memory). DISC21 micro-AP, R@1/@10, TPR @ 1/5 % FPR, per domain; val tunes, test
  reports. Baseline = v1 engine under frozen policy 3.1.0 (`model/benchmarks/policy_v3.1.0.json`).
- **Gate results (paintings, test):** v1 micro-AP 0.820 / TPR@1% 0.542 → shipped SSCD + DINOv2-S (w 0.8)
  + 18 region crops **0.905 / 0.896**. PDQ (dihedral) replaces pHash. DINOv2-B = tie → ViT-S.
  **ALIKED + LightGlue rejected** (+0.3 pp as a check; ORB retired, `local_match.py` unused).
  CPU budget (`benchmark.latency components`): crops cost 0.4–3.6 s → CPU profile runs **no crops**
  (`regions_cpu` = null), GPU profile 18 × 320. ONNX fp32 = PyTorch (no gain); dynamic int8 unusable.
- **Policy 4.0.0** (`training/calibrate.py --keep-detectors`; detector thresholds unchanged): checks
  `copy_exact` (PDQ bits) + `copy_semantic` replace phash / clip / orb. Benchmark test: paintings 97.2 %
  copies flagged / 2.2 % genuine (v1 93.1 / 5.0); NFT genuine flagged **0 %** (v1 6.5 %), NFT copies
  auto-failed 10.7 % (v1 0 %).
- **On-chain index:** 48 Ethereum collections (Alchemy, cap 6,000), **275,882 tokens ingested,
  269,816 fingerprinted** + 15,037 seed = **284,861 references** in pgvector (migrations 0003–0005,
  halfvec + HNSW; 128 px thumbs in `storage/nft/thumbs/`, ~1.1 GB of HNSW graphs). Search p50 7 ms /
  p95 15 ms (needs `shared_buffers` 1 GB: compose). Sync (`ingest sync`) ran ~3,900 blocks: no mint
  in tracked collections (minted out), path covered by the fake-Alchemy test only.
- **Live-index lessons (each fixed + re-measured):** shared HNSW graph found painting sources 51 % →
  partial one-off index (0005) 100 %; SSCD-cosine member rule failed 36 % of genuine new punks →
  pixel-identity rule (128 px NEAREST RGB, ≤ 13 px, 2× margin); PDQ HNSW missed 6 % → exact
  in-memory PDQ (9 MB, 22–33 ms); PDQ quality gate blinded flat NFT art → gate for one-off works only.
- **End-to-end (live index, n = 50/category):** genuine pass 83 % (v1 77 %), wrongly failed 3.5 % (v1 3.0 %),
  v1-comparable forgeries stopped 84.9 % (v1 85.8 %); exact re-upload of an indexed Ethereum NFT
  **98 % FAIL**; edited copies of collection NFTs still pass (documented limit). Latency: CPU 2 threads
  **p50 1.10 s / p95 1.35 s**, GPU p50 0.83 s.
- **Tests:** 140 fast pytest (+ `test_copy_engine.py` 7, `test_ingest.py` 6, pgvector index test), slow
  test for the real engine; frontend shows v4 checks + on-chain matches; `start_mintgate.bat` launcher.
- **Owner actions:** enable **Base mainnet** on the Alchemy app (dashboard → networks: the API returns
  403), then add Base collections to `ingest/collections.py`; run `ingest sync` + `ingest embed --follow`
  as services (Phase 7). The dev DB holds the index (~4 GB incl. thumbs): back it up before `docker
  compose down -v`.
- **For Phase 5/7:** deploy needs `pdqhash` built from source (no Linux wheel → `build-essential`),
  SSCD weights via `benchmark.fetch_models` (SHA-256 pinned, not in git), Postgres `shared_buffers` ≥ 1 GB.
  Recalibrate whenever the index grows a lot: thresholds from the 4k benchmark did not all transfer.

## Phase 5: AI detection, manipulation forensics, provenance, statistics (done, 2026-11-07)
- **Statistics everywhere** (`training/stats.py`): Wilson CIs for rates, stratified bootstrap for AUCs,
  paired bootstrap for model comparisons. `evaluate_system` now samples **without replacement** (v4 drew
  with replacement), n = 300 per category, resumable per category.
- **OOS protocol v2** (`training/oos_data.py` → `data/oos/`, 2,431 images): v1 sample kept + 47 unseen NFT
  collections (incl. 40 on-chain ones via Alchemy), Rijksmuseum, COCO / DOCCI photos, OpenFakeTiny 2025-26
  generators (CC BY-NC, evaluation only). 50/50 select / report halves. Primary metric = balanced accuracy at
  the deployed review threshold (genuine domains weighted equally).
- **AI detector** (`training/ai_backbones.py`, `detection/backbones.py`, vision towers only via HTTP range
  reads): shipped **CLIP-B/32 + SigLIP 2 B/16 probes (w 0.3/0.7)**, +4.6 pp [+1.8, +7.6] over v1 (select);
  report half AUC 0.925 vs 0.894, AI flagged 88.7 % vs 75.8 %. CLIP-L/14 + So400m win but 4-5 s on CPU;
  DINOv2 loses; LoRA (`training/lora_finetune.py`) rejected; int8 rejected (-14.8 pp, `benchmark/int8_gate.py`).
  **Finding:** OOS photos flagged 45-53 % by every detector (DOCCI worst).
- **Permissive profile** (`MINTGATE_PERMISSIVE_ONLY=1` → `ai_detector.permissive.json` + `policy.permissive.json`):
  permissive-data probe flagged 100 % of paintings/photos until `training/fetch_permissive.py` added
  ArtBench-PD paintings + CC BY COCO-train photos; now OOS AUC 0.929, no TruFor.
- **Forgery benchmark** (`benchmark/build_forgery.py`, `benchmark/forgery.py`; CASIA v2, Columbia, COVERAGE,
  IMD2020, CocoGlide with masks): ResNet-18 at chance → **retired**. **TruFor** adopted as REVIEW-only
  `manipulation` check (+0.28 / +0.19 AUC on COVERAGE / CocoGlide, 6-8 % genuine flags), GPU profile only,
  non-commercial (vendored `third_party/trufor/`, weights → safetensors with `weights_only=True`).
- **Provenance** (`detection/provenance.py`, check `provenance` + policy `positive_evidence`): C2PA, SD/SDXL
  watermarks, metadata, optional reverse search. Real files: 0 / 1,452 genuine false hits, 15.9 % of AI files
  caught (MJ7 100 %, GPT-image 47.5 % via expired-cert C2PA). 23 tests with real signed fixtures.
- **Policy 5.0.0** (`training/calibrate.py`): AI thresholds from the detector file, TruFor from the forgery
  benchmark, provenance fixed, copy unchanged. **End to end (n = 300/category, GPU):** genuine PASS 78.5 %
  [76.6, 80.3], wrongly failed 6.8 % [5.8, 8.0] (15 of 18 punk FAILs are dataset duplicates); forgeries stopped
  87.1 % [85.9, 88.2]; per-domain false alarms NFT 11.7 %, pixel 11.9 %, paintings 19.5 %, photos 45.9 %.
- **Retraining gate** (`training/retrain_gate.py`) + docs "Adding a new generator"; HiDream demo: refused
  (photo FPR +2.01 pp > 2 pp; HiDream already caught 40/40).
- **Tests:** 169 fast pytest (+ provenance 23, policy positive evidence 4), 5 slow (real backbones, TruFor).
- **Open / for Phase 7:** **CPU profile exceeds the 1.5 s budget** (p50 1.5-1.6 s, p95 1.9-2.0 s; v4 1.10/1.35).
  ONNX Runtime export (`benchmark/onnx_backbones.py`, `MINTGATE_CPU_ONNX=1`, −180 ms) crashes next to the copy
  engine on Windows: verify on Linux. Deploy needs `training.ai_backbones fetch` (+ `benchmark.forgery fetch`
  for the GPU profile), `c2pa-python`, `invisible-watermark`; `C2PA_TRUST_ANCHORS` for positive evidence.
  Owner decisions (2026-11-07): keep the new detector on CPU despite the budget (verify ONNX on Linux in
  Phase 7); portfolio-only licensing. The SD 1.5 HF cache was deleted: `benchmark.build_copydet --regen`
  needs `huggingface-cli download runwayml/stable-diffusion-v1-5` first (it loads with local_files_only). The laptop's commit memory is the bottleneck
  for long jobs (see CLAUDE.md gotchas).

### Ready for Phase 6 (owner inputs, 2026-11-07)
- Safe 2-of-3 owners (Sepolia + Base Sepolia): `0xeed816c3F61fC7A7396159c267658010dB69eD7F` (deployer),
  `0x1F79bD0C6CF753D4708E729e4677e0fDd2E5E2E2`, `0xCb036B4E03086FDf56b4977722F3aA10EaAc57c2`.
- `DISCORD_WEBHOOK_URL` set in `backend/.env` (format checked). Alerts: disputes + REVIEW items (env switches).
- PolicyRegistry behind a TimelockController (1 h), proposer = the Safe.
- Balances: Sepolia 0.251 ETH, Base Sepolia 0.025 ETH (bridged).
