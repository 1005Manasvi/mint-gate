# Phase 1: Smart contracts v2 (blockchain core)

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-1-contracts-v2.md.
> Design first (write the interface and state machine into docs/contracts.md), then implement,
> test exhaustively (Hardhat + Foundry fuzz), run Slither, deploy to testnets, update PROGRESS.md, commit.

## Goal
Move trust from "the server's hot wallet does everything" to **cryptographic attestations the
artist submits themselves**, with disputes, revocation and multisig-ready governance. The
contract becomes the product's core, usable by other collections too.

## Design
1. **`MintGateRegistry`**: the verdict registry, independent of any NFT contract.
   - `Verdict` = { imageHash, reportHash, policyHash, score, decision, artist, issuedAt, expiresAt }.
   - The verifier (backend) signs an **EIP-712 typed `Verdict`**; anyone can submit it with the signature
     (`submitVerdict`). The registry checks: signer has VERIFIER_ROLE, not expired, nonce unused, artist matches.
   - Every verdict is stored, failures included. Events are indexed by imageHash / artist.
   - REVIEW resolution by REVIEWER_ROLE (as in v1).
   - `isMintable(imageHash, artist) → (bool, reportHash)`.
2. **`MintGateNFT` v2**: an ERC-721 that **the artist mints from their own wallet** (`msg.sender == verdict.artist`),
   paying their own gas. Requires `registry.isMintable`. One token per imageHash, across all collections.
   ERC-2981 royalties for the artist. tokenURI = IPFS metadata.
3. **Disputes**: `openDispute(tokenId, evidenceURI)` with a small ETH bond (anti-spam);
   REVIEWER resolves → `upheld` (token **flagged/revoked**, bond returned, creator status recorded) or
   `rejected` (bond to treasury). Revocation is a status flag (no burning other people's tokens), shown
   by `tokenURI`/metadata and the frontend.
4. **`IMintGate` + `MintGated` abstract contract**: any third-party ERC-721 can inherit `MintGated`
   to require a passing verdict in its own `_mint`. This is what makes it a reusable protocol.
5. **Governance**: AccessControl roles (ADMIN, VERIFIER, REVIEWER, PAUSER), `Pausable`, verifier key
   rotation without redeploying, admin role transferable to a **Safe multisig** (done in Phase 6).
6. Upgrade strategy: immutable contracts + a registry pointer (simpler and safer than proxies). Document why.

## Tasks
- Write `docs/contracts.md` (state machine diagram, roles, threat model: replay, front-running,
  signature malleability (use OZ ECDSA), griefing via disputes, key compromise → pause + rotate).
- Implement in `Blockchain/contracts/v2/`; keep v1 untouched (still deployed).
- **Tests**: Hardhat unit tests for every path and revert; add **Foundry** (`forge`) fuzz/invariant tests
  (invariants: one token per imageHash; no mint without a valid verdict; revoked stays revoked). Coverage ≥ 95 %.
- Slither clean; gas report (`hardhat-gas-reporter`) in docs.
- Deploy to **Sepolia and Base Sepolia** (L2, cheap). Store addresses in
  `backend/chain/deployments/<network>.json`. Verify the source (Sourcify v2 API + Basescan if a key exists).
- Python: add an `eip712.py` signer in `backend/chain/` with a unit test that the signature verifies on-chain (local hardhat).

## Acceptance criteria
- An artist wallet mints with a backend-signed verdict; a forged or expired signature reverts; replay reverts.
- Dispute flow works end to end on the local chain; fuzz/invariant suites pass.
- Both testnet deployments are verified; addresses are in PROGRESS.md.

## Owner prerequisites
- Base Sepolia test ETH for the deployer wallet (free faucets: Alchemy / Coinbase CDP faucet).
