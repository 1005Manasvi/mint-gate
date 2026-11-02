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

## Phase 0
_not started_
