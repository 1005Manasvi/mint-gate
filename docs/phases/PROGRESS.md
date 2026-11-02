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
