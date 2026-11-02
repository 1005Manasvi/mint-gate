# Mint-Gate v2 roadmap: a real-world, blockchain-first build

**How to use this.** Run **one phase per new Claude Code chat**, in order. Open a new chat
in this project folder and paste the phase's "Start prompt". Claude reads `CLAUDE.md`
automatically and `PROGRESS.md` on instruction, so no context is lost and tokens are saved.
At the end of each phase Claude updates `PROGRESS.md` and commits.

Everything stays **free**: Sepolia and Base Sepolia testnets, Azure for Students,
Alchemy/Pinata free tiers, GitHub Actions.

| Phase | Focus | Main outcome | Est. effort |
|---|---|---|---|
| [0](phase-0-foundation.md) | Foundation | GitHub repo, CI (pytest + Hardhat + Slither), test baseline | 1 chat |
| [1](phase-1-contracts-v2.md) | **Blockchain core** | Contract v2: EIP-712 attestations, artist self-mint, disputes, revocation, pause, multisig-ready, Foundry fuzz tests, Sepolia + Base Sepolia | 1–2 chats |
| [2](phase-2-database-backend.md) | Database + backend | PostgreSQL + pgvector + Redis, job queue, attestation signer, on-chain event indexer | 1–2 chats |
| [3](phase-3-wallet-minting.md) | **Wallet UX** | Sign-In with Ethereum, artist-signed submissions, mint from the artist's own wallet, tx tracking | 1 chat |
| [4](phase-4-onchain-index.md) | **On-chain plagiarism DB** | Ingest real NFTs (Ethereum + Base) via Alchemy, SSCD copy embeddings, vector search at scale, live sync of new mints | 1–2 chats |
| [5](phase-5-provenance-signals.md) | Provenance + ML | C2PA, AI-watermark detection, reverse image search, real forgery datasets, recalibration with confidence intervals | 1–2 chats |
| [6](phase-6-disputes-governance.md) | **Disputes + governance** | Report → evidence on IPFS → reviewer bond → on-chain resolution; Safe multisig admin; public provenance explorer | 1–2 chats |
| [7](phase-7-deploy-ops.md) | Production | Azure Container Apps + Azure Postgres + Key Vault, monitoring, load test, docs, demo video | 1 chat |

## Limitation → phase coverage (from the assessment)

| Limitation / red flag | Fixed in |
|---|---|
| No public GitHub repo, two "dump" commits | 0 (and phase-by-phase commits from then on) |
| No Python tests, no CI | 0 (each later phase adds its own tests) |
| Server hot wallet signs every mint and pays all gas | 1 (EIP-712 attestations, artist mints) + 7 (Key Vault) |
| Single-admin trust model | 1 (roles, pause, Safe-ready) + 6 (Safe multisig) |
| No database: JSON files, in-memory state | 2 |
| Synchronous 1–3 s analysis, single worker | 2 (Redis job queue) |
| Recipient address not proven, no artist identity | 3 (SIWE + signed submission) |
| Plagiarism search only covers 15k local images | 4 (real on-chain NFTs + vector DB) |
| Weak copy detection on edited copies | 4 (SSCD) |
| Art stolen from the web (not on-chain) is undetectable | 5 (reverse image search) |
| New AI generators appear monthly | 5 (watermark/C2PA + retraining gate) |
| Synthetic-only forgery evaluation, small samples | 5 (real forgery datasets, n ≥ 300, 95 % CIs) |
| No way to contest or correct a verdict | 6 (disputes) |
| No live deployment | 7 |
| Non-commercial data licences | 5 (permissive-only retrain option, documented) |
