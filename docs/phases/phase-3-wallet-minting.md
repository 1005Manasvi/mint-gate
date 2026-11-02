# Phase 3: Wallet identity and artist-side minting

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-3-wallet-minting.md.
> Keep the strict CSP (no CDN scripts: vendor any library locally). Add tests, update PROGRESS.md, commit.

## Goal
The artist is a **cryptographically identified wallet**, not a typed address. They sign their
submission, receive a verdict attestation, and mint from their own wallet.

## Tasks
1. **Sign-In with Ethereum (EIP-4361)**: `GET /api/auth/nonce`, `POST /api/auth/verify` (server
   verifies the SIWE message signature, issues a short-lived HttpOnly, Secure, SameSite=Strict session cookie).
   Nonces are single-use and stored in Redis. Add CSRF protection for state-changing endpoints.
2. **Signed submission**: the client signs `imageHash` (EIP-191 personal_sign) when uploading; the server
   checks the signer equals the session wallet. This binds "who claimed this artwork first" (useful in disputes).
3. **Chain selection**: Sepolia and Base Sepolia (from `deployments/*.json`), with network switch/add via
   `wallet_switchEthereumChain` / `wallet_addEthereumChain`.
4. **Mint from the artist wallet**: vendor **viem** (or ethers v6) as a local ES module under `frontend/vendor/`.
   Flow: get attestation → `submitVerdict` + `mint` (or the combined `mintWithVerdict`) → show gas estimate,
   pending, confirmed (with confirmations), and errors decoded from custom errors.
5. **My artworks** page: the wallet's submissions, verdicts, tokens and dispute status (from Postgres, fed by the indexer).
6. **Public provenance page** `/token/<chain>/<contract>/<id>`: verdict, policy hash, report (IPFS),
   on-chain links, and a "verify integrity" button (recomputes the report hash client-side).
7. Accessibility and mobile layout pass; empty and error states.
8. Tests: SIWE verification unit tests (valid, expired, wrong domain, replayed nonce);
   Playwright end-to-end against a local hardhat node with a test wallet (optional, if time allows).

## Acceptance criteria
- Without a SIWE session, submitting and minting are impossible. A mint is paid by the artist wallet.
- A token's public page proves its provenance without trusting the backend (hash recomputed in the browser).
