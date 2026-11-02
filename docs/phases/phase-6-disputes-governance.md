# Phase 6: Disputes, governance and the public explorer

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-6-disputes-governance.md.
> Contracts from Phase 1 already support disputes: build the off-chain flow, UI and governance. Tests, PROGRESS.md, commit.

## Goal
A verdict can be **contested and corrected transparently**, and no single key controls the system.

## Tasks
1. **Dispute flow** (end to end):
   - A reporter opens a dispute on a token: evidence (original file, URL, timestamps, prior on-chain
     claims) is pinned to IPFS; `openDispute(tokenId, evidenceURI)` with a bond from their wallet.
   - The backend automatically attaches its own evidence: SSCD match to the earlier on-chain token,
     the earliest signed submission (Phase 3), and the reverse-search result (Phase 5).
   - Reviewer dashboard (REVIEWER role, SIWE-gated): side-by-side evidence, decision with a written
     reason; the reason is pinned to IPFS, its hash goes on-chain with `resolveDispute`.
   - Outcomes: token flagged/revoked (metadata + UI show it), bond returned or slashed, audit log entry.
2. **Governance**: create a **Safe multisig** (2-of-3, Sepolia + Base Sepolia, owner's wallets);
   transfer ADMIN and PAUSER roles to it; document the key-rotation and emergency-pause runbook.
   Optional: a TimelockController for policy-hash changes (policy updates become announced on-chain).
3. **Policy registry on-chain**: `setPolicy(hash, uri)` emits the policy JSON's IPFS CID + SHA-256, so
   anyone can verify which rules applied to a verdict.
4. **Public explorer**: pages for stats (verdicts by decision, disputes, revocations per chain, from the
   indexer), the policy history, and each token's provenance timeline.
5. **Notifications**: email or webhook to reviewers on new disputes and REVIEW items (simple SMTP or Discord webhook).
6. Tests: contract dispute tests extended (bond accounting invariants in Foundry), backend dispute API tests.

## Acceptance criteria
- A full dispute (open → evidence → resolve → token flagged) works on Sepolia, visible in the explorer.
- ADMIN is held by a Safe; a single leaked key cannot change roles or the policy.
