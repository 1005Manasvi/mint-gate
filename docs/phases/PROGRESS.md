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
