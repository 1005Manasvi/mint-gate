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

## Phase 0: Foundation (done locally, 2026-11-02; push pending)
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
- **Not done (owner):** repo `1005Manasvi/mint-gate` did not exist yet (404), and adding the remote
  needs your approval. To finish: create it (Public, no README), then
  `git remote add origin https://github.com/1005Manasvi/mint-gate.git; git push -u origin main`,
  and confirm CI is green. **CI has not run on GitHub yet.**
- **For Phase 1:** v2 must follow checks-effects-interactions (set URI + emit before `_safeMint`)
  and pin `pragma solidity 0.8.24;`, so Slither is fully clean. Format v2 with Prettier (v1 is in `.prettierignore`).
