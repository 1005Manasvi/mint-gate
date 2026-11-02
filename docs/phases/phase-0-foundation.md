# Phase 0: Foundation (GitHub, CI, test baseline)

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-0-foundation.md.
> Work through the tasks in order, run the tests, update PROGRESS.md, and commit at the end.

## Goal
A professional repo that reviewers trust: public GitHub with history, automated tests, CI badge.

## Prerequisite (owner)
- Create the empty repo `https://github.com/1005Manasvi/mint-gate` (Public, no README).

## Tasks
1. **Python test suite** (`backend/tests/`, pytest):
   - `test_policy.py`: risk mapping, advisory, `max_status` REVIEW cap, score, decision rules.
   - `test_preprocessing.py`: invalid bytes, oversize, decompression bomb, RGBA → RGB, SHA-256 stability.
   - `test_reference_index.py`: copy_score (exact = 1.0, collection members excluded), sparse mask.
   - `test_copy_move.py`: clean image vs synthetic clone (`detection/manipulations.py`).
   - `test_api.py`: Flask test client with a **mocked chain client** and tiny fixture models:
     analyse → review (bad key 403) → mint gate (FAIL 403) → verify (tamper detection).
   - Mark GPU/model-heavy tests `@pytest.mark.slow`; CI runs fast tests only.
2. **Contract checks**: `npx hardhat coverage` (via hardhat-toolbox), aiming at ≥ 95 % line coverage;
   Slither static analysis (`pip install slither-analyzer`); fix or justify every finding in `docs/security.md`.
3. **GitHub Actions** (`.github/workflows/ci.yml`): job `python` (3.12, CPU torch, pytest -m "not slow"),
   job `contracts` (Node 20, `npm ci`, hardhat test + coverage), job `slither`.
4. **Pre-commit**: ruff (lint + format) for Python, prettier for JS/Solidity; `.pre-commit-config.yaml`.
5. **Repo hygiene**: LICENSE (MIT for code; data licences stay documented), CI and coverage badges
   in the README, `CONTRIBUTING.md` (short).
6. **Push**: add the `origin` remote and push `main` (Git Credential Manager opens a browser login).
   From now on, **one commit per logical change**.

## Acceptance criteria
- `pytest -m "not slow"` passes locally and in CI; hardhat tests pass; Slither has no high or medium issue left unexplained.
- The GitHub repo is public with green CI and a badge in the README.

## Notes
- Do not commit data or `.env` files (already git-ignored). Model weights (45 MB) may stay.
- Keep the tests fast (< 60 s in CI) with small fixture images in `backend/tests/fixtures/`.
