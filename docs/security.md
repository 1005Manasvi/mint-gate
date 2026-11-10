# Security review

Static analysis, test coverage and known limitations of Mint-Gate. Updated every time a contract
changes. Last review: **2026-11-02 (Phase 1)**: `MintGateNFT` v1 (Ethereum Sepolia
`0xae989b374DF85a942b4caE07940713Ff89418F32`) and contracts v2 (`MintGateRegistry`, `MintGateNFTV2`,
`MintGated`, `IMintGate`; addresses in [contracts.md](contracts.md#10-deployments)). The v2 design,
roles and threat model are in [contracts.md](contracts.md).

## Tools and how to reproduce

| Check | Tool | Command (from `Blockchain/`) | Result |
|---|---|---|---|
| Unit tests | Hardhat 2.29, chai | `npx hardhat test` | 118 passing (13 v1, 105 v2 incl. Phase 6 governance with the real Safe v1.4.1 bytecode) |
| Coverage | solidity-coverage 0.8.17 | `npm run coverage` (fails below 95 %) | 100 % statements, branches, functions, lines |
| Fuzz + invariants | Foundry (forge 1.8.7) | `forge test` (CI: `FOUNDRY_PROFILE=ci`) | 14 fuzz properties, 8 invariants (4 on bond accounting, Phase 6), all passing |
| Static analysis | Slither 0.11.6 | `slither . --fail-medium` (config: `slither.config.json`) | v2: 1 informational (accepted, below). v1: 0 high, 0 medium, 2 low |

All run in CI (`.github/workflows/ci.yml`). Slither analyses our own contracts only
(`exclude_dependencies`; the test fixtures in `contracts/mocks/` are filtered out);
OpenZeppelin findings are listed separately below.

## Slither: contracts v2

No findings. Four detector hits are suppressed inline (`// slither-disable-next-line`), each
with the reason next to it in the source:

| Where | Detector | Why it is safe |
|---|---|---|
| `MintGateRegistry.submitVerdict` (2 lines) | `timestamp` | Expiry and the 30-day / 15-minute windows are hours to days long; validators can move block time by seconds. |
| `MintGateRegistry.withdraw` | `incorrect-equality`, `timestamp` | `amount == 0` compares a credit balance that only `resolveDispute` / `withdraw` change (not `address(this).balance`, which could be forced), so an exact comparison is correct. Slither links it to `timestamp` because the same struct stores `openedAt`. |
| `MintGateNFTV2.submitAndMint` | `unused-return` | The returned signer is not needed: `submitVerdict` reverts unless the signer is a verifier. |
| `PolicyRegistry.policyAt / current / isRegistered / policyActiveAt` | `incorrect-equality`, `timestamp` | Array lengths and a 1-based index, not times or balances; Slither taints them because the `Policy` struct stores `activatedAt`. |
| `DisputeResolver.resolveDispute` | `unused-return` | Only the collection and token id of `mintOf` are needed. |
| `IDisputeRegistry.REVIEWER_ROLE` | `naming-convention` | Must match the registry's getter name. |

**Accepted (informational):** `missing-inheritance`: "`MintGateNFTV2` should inherit from
`IRefreshableMetadata`". True (it implements `refreshMetadata`), but its source must stay byte-identical
to the Sourcify-verified deployment, so the interface lives in `DisputeResolver.sol` only.

`DisputeResolver` calls the registry (trusted, immutable) and then, after an upheld dispute, the token's
collection (`refreshMetadata`, inside `try/catch`). The collection holds `COLLECTION_ROLE`, i.e. was vetted
by the admin; a reverting collection cannot block the resolution, and the resolver holds no funds or state
a re-entrant call could use. `DisputeReasoned` is emitted before the registry call, so the event order in
one transaction is `DisputeReasoned`, `ImageRevoked`, `DisputeResolved` (the indexer relies on it).

The v2 mint follows checks-effects-interactions: all NFT state, the `Transfer` and `VerifiedMint`
events are written first, then the registry is called (`consume`, which reverts the whole mint
if the verdict does not allow it), and the ERC-721 receiver check
(`ERC721Utils.checkOnERC721Received`) runs last. That is why v1's two reentrancy findings do not
appear for v2. Bond payouts use pull payments with the credit zeroed before the transfer.

## Slither findings: MintGateNFT.sol

| # | Detector | Impact | Status |
|---|---|---|---|
| 1 | `reentrancy-benign` in `mintVerified` | Low | Accepted (v1), fixed by design in v2 |
| 2 | `reentrancy-events` in `mintVerified` | Low | Accepted (v1), fixed by design in v2 |
| 3 | `pragma` (several Solidity version constraints) | Informational | Accepted |

**1 + 2: state write and events after `_safeMint`.** `_safeMint` calls `onERC721Received` on the
recipient when it is a contract, and `mintVerified` afterwards writes `_tokenURIs[tokenId]` and
emits `MetadataUpdate` and `VerifiedMint`. This is not exploitable:

- `mintVerified` is `onlyRole(VERIFIER_ROLE)`, so a malicious receiver cannot re-enter it unless
  it also holds the verifier role.
- Every value that gates minting (`v.tokenId`, `tokenOfImage`, `reportOfToken`, `_nextTokenId`) is
  written **before** the external call. A re-entrant mint of the same report or image would revert
  with `ReportAlreadyUsed` / `ImageAlreadyMinted`.
- The only late write is the URI of the token being minted; during the callback the receiver can at
  most observe an empty `tokenURI` for its own new token. If the callback reverts, the whole mint
  reverts (tested: `ERC721InvalidReceiver`).
- Event order inside one transaction does not change what indexers see after it is mined.

The v1 source is **not** changed: it is deployed and matched exactly on Sourcify, and any edit would
break that match. v2 (Phase 1) follows checks-effects-interactions: set the URI and emit events
**before** `_safeMint`, so Slither reports nothing here.

**3: pragma.** Our contract uses `^0.8.24` and Hardhat compiles everything with exactly 0.8.24
(`hardhat.config.js`); the other constraints come from OpenZeppelin's interface files. v2 can pin
`pragma solidity 0.8.24;`.

## Slither findings in dependencies (OpenZeppelin Contracts 5.6.1)

Excluded from the CI gate. To see them, set `"exclude_dependencies": false` in
`Blockchain/slither.config.json` and run `slither .`:

| Detector | Impact | Where | Why it is a false positive |
|---|---|---|---|
| `incorrect-exp` | High | `Math.mulDiv` (`(3 * denominator) ^ 2`) | XOR is intended: it seeds the Newton iteration for the modular inverse (documented in the source). |
| `divide-before-multiply` | Medium (x9) | `Math.mulDiv`, `Math.invMod` | Intentional 512-bit arithmetic, audited by OpenZeppelin. |
| `assembly`, `too-many-digits`, `solc-version` | Informational | various | Style notes in audited library code. |

## Off-chain findings (Phase 0)

- **Decompression-bomb guard was half as strict as configured.** `MAX_IMAGE_PIXELS` is 40 MP, but
  Pillow only *raises* above twice its limit and merely warns in between, so a 6 KB PNG that decodes
  to 50 MP (150 MB of RGB) was accepted. `preprocessing.prepare` now checks the pixel count from
  the header before decoding (`tests/test_preprocessing.py::test_decompression_bomb_rejected`).

## Backend data layer (Phase 2)

- **Stateless API.** Reports, attestations and the audit log live in Postgres; the job queue and
  the rate-limit counters in Redis (shared by every API instance). The API holds no keys except
  the verifier key it signs PASS attestations with, and sends no transactions.
- **Attestations.** Bound to the artist wallet given at upload (EIP-712 `artist`; the contract
  checks `msg.sender`), valid 24 h, random 256-bit nonce, unique per chain + registry (DB
  constraint). An unexpired attestation is reused rather than re-signed.
- **Backend transactions** (REVIEW / FAIL `submitVerdict`, `resolveReview`) run only in chain
  workers, each under a Redis lock per sender address, so concurrent workers never reuse a nonce.
  The tip is capped (`PRIORITY_FEE_GWEI`, default 0.01 gwei; see the Sepolia gas lesson).
- **Append-only audit log.** A trigger rejects `UPDATE` / `DELETE` on `audit_log`
  (`tests/test_repo.py`). The database owner can still drop the trigger: this protects against
  application bugs, not against a compromised DB superuser.
- **Indexer.** Only events `CONFIRMATIONS_<NETWORK>` blocks deep are indexed (Base Sepolia 5,
  Sepolia 3); deeper reorgs are detected by the stored block hash and rolled back 64 blocks.
  Projections (`tokens`, `disputes`) are rebuilt from `chain_events`, never edited by hand.
- **Crash recovery.** A worker killed mid-job leaves an abandoned RQ job; it is retried within
  ~4 min. RQ's built-in scheduler (a child process) outlived hard-killed workers on Windows and
  blocked all retries with its lock; the worker runs the scheduler in a daemon thread instead.

## Wallet identity (Phase 3)

- **Sign-In with Ethereum (EIP-4361)**, verified in `backend/auth.py` (own parser, no third-party
  SIWE library): domain must be in `SIWE_DOMAINS` (default: `PUBLIC_BASE_URL`'s host + its loopback
  alias), URI on that domain, version 1, a configured chain id, EIP-55 address, issued ≤ 10 min ago
  and not in the future (60 s skew), `Expiration Time` / `Not Before` honoured, EIP-191 signature
  recovers the address. Nonces come from `GET /api/auth/nonce`, live 5 min in Redis and are
  consumed with an atomic `GETDEL` only after every other check passed (a rejected message does not
  burn it; a replayed one fails). Tests: `backend/tests/test_auth.py`.
- **Session**: random 256-bit token in a `__Host-mg_session` cookie (`HttpOnly`, `Secure`,
  `SameSite=Strict`, `Path=/`, 1 h). Redis stores only the token's SHA-256, so a Redis dump cannot be
  replayed as cookies. Logout deletes the server-side record.
- **CSRF**: every state-changing endpoint with a session (`/api/analyze`, `/api/attest`,
  `/api/auth/logout`) needs the per-session `X-CSRF-Token` header, and a browser-sent `Origin` must
  be an allowed domain (also checked on `/api/auth/verify`, against login CSRF). SameSite=Strict is
  the second layer.
- **Signed submission**: the wallet signs (personal_sign) a message containing the domain, its
  address, the SHA-256 of the exact uploaded bytes and a timestamp (±10 min). The API recomputes the
  hash, requires the signer to equal the session wallet, and stores message + signature with the
  submission: anyone can later re-verify who claimed which file first (dispute evidence). The claim
  stays out of the public IPFS report.
- **Attestations** are only issued to the session wallet that is the report's artist (`403`
  otherwise); the contract checks `msg.sender` again (`ArtistMismatch`).
- **Strict CSP kept**: `script-src 'self'`, ethers v6.17.0 vendored as one ES module
  (`frontend/vendor/`, MIT licence copied next to it). `connect-src` adds only the configured
  chains' *public* RPCs (`PUBLIC_RPC_<NET>`; `RPC_URL_<NET>` may contain an API key and never
  reaches the browser) and the IPFS gateway. `img-src` allows `blob:` (local upload preview).
  The Playwright test fails on any CSP violation.
- **Provenance without trusting the backend** (`/token/<chain>/<contract>/<id>`): the browser reads
  the NFT and registry (wallet provider when it is on that chain, else the public RPC), fetches
  metadata + report, recomputes the report hash with a byte-exact port of `canonical_hash`
  (`frontend/js/canonical.js`, parity vector shared with pytest) and the pixel hash of the image.
  Reports are pinned with `pinFileToIPFS` (exact bytes); `pinJSONToIPFS` would let Pinata
  re-serialise numbers (`1.0` → `1`) and break the hash.

## Known limitations (trust model)

These are design limits, not bugs; the roadmap addresses them.

- **Smart-contract wallets cannot sign in yet.** SIWE and signed uploads accept EOA signatures
  (EIP-191) only; EIP-1271 verification (Safe, smart accounts) needs an RPC call and is not wired.
- **Provenance page without a wallet** uses the RPC URL that `/api/networks` names. A malicious
  backend could point it at a lying node; with a wallet on that chain the page uses the wallet's
  node instead, and it always shows which source it read. The registry address is read from the
  NFT contract itself and compared with the known deployment.
- **Pixel check depends on the browser decoder.** PNGs written by Pillow carry no colour profile,
  so the decode is exact in Chromium (`colorSpaceConversion: "none"`). A browser that
  colour-manages anyway would report a mismatch on that one check (a false alarm, never a false
  pass); the report-hash check does not depend on it.
- **v1 hot wallet (retired from the app in Phase 2).** v1's verifier key recorded verdicts *and*
  minted. The app now uses v2 only: the backend signs EIP-712 verdicts, the artist submits and
  mints, and a leaked key is handled by pause → role rotation → `voidVerdict` → dispute
  (playbook tested in `test/v2/MintGateRegistry.test.js`).
- **v2 signing key is still a hot key.** A leaked verifier key can sign PASS verdicts until it is
  revoked; signatures are valid for at most 30 days. Phase 7 moves the key to Azure Key Vault.
- **v2 disputes have no timeout.** A dispute nobody resolves keeps its bond locked (the registry is
  immutable; Phase 6 adds a 7-day review target and Discord alerts, docs/governance.md § 3).
- **Admin → Safe (Phase 6).** Admin, pauser and treasury move to a 2-of-3 Safe on both testnets; the
  admin transfer is two-step (1 h) and completes when a second owner signs `accept-admin`
  (docs/governance.md § 4.1). Until then the deployer is still admin.
- **Testnet verifier and reviewer are still one wallet** (`0xeed8…eD7F`, also Safe owner 1).
  Production needs separate keys; the Safe can rotate both without a redeploy.
- **Reviewers are wallets (Phase 6).** Disputes and the REVIEW queue accept SIWE sessions whose wallet
  holds `REVIEWER_ROLE` on chain (read live, cached 60 s). The old shared `REVIEWER_KEY` still works
  for the REVIEW queue (scripts) unless `REVIEWER_KEY_DISABLED=1`.
- **A pause needs two Safe owners** (owner's choice): slower than a hot pauser key, but a stolen key
  cannot freeze the registry either.
- **Detection is off-chain and probabilistic.** The contract proves which verdict and policy were
  used (report hash, policy SHA-256), not that the verdict is right. See README for error rates.

## Phase 5: model supply chain and provenance

- **No pickle at runtime.** TruFor's published checkpoint is a pickle. It is loaded once, offline, with
  `torch.load(weights_only=True)` (only `numpy.core.multiarray.scalar` and `numpy.dtype` allow-listed by
  exact name, after inspecting the pickle's globals with `pickletools`), converted to safetensors and
  SHA-256 pinned (`model/trufor/manifest.json`). Backbone vision towers and ONNX exports are pinned the same
  way (`model/backbones/<key>/manifest.json`); a mismatch refuses to load.
- **Vendored non-commercial code** (`backend/third_party/trufor/`): original notices kept (licence
  condition ii), modifications listed in `NOTICE.md`; never imported with `MINTGATE_PERMISSIVE_ONLY=1`.
- **Provenance parsing of untrusted files.** C2PA, EXIF / PNG text / XMP readers run on the raw upload
  inside the worker; every parser error is caught and reported as "no evidence", never as a verdict.
  XMP search is bounded to the first 4 MB; watermark decoding only runs on native generator sizes
  (≤ 1.6 MP), so a large upload cannot make it expensive.
- **C2PA trust.** An AI claim is accepted from any manifest whose hashes match (credential issues such as
  an expired certificate are reported separately). Positive evidence (capping the AI detector at REVIEW)
  requires a chain to a configured trust anchor (`C2PA_TRUST_ANCHORS`): a self-signed "camera" manifest
  is trivial to forge and earns nothing. A trusted capture never overrides AI evidence or copy checks.
- **Reverse image search sends the upload to a third party** (Google Vision / TinEye). It is off by default
  and only enabled by explicit configuration (`REVERSE_SEARCH` + key); results are advisory (REVIEW), never
  FAIL, and API errors are not evidence.

## Phase 6: disputes and governance

- **Pinned documents are exact bytes.** Reporter evidence, the backend's evidence and reviewer reasons are
  serialised once (`sort_keys`), pinned with `pinFileToIPFS` and hashed; the reason's SHA-256 is what the
  resolver records on chain, so a swapped reason document is detectable.
- **Uploads in disputes.** The reporter's original goes through the same decoder guards as artist
  uploads (`prepare`: size, pixel count, format) before it is stored or pinned; stored under a name derived
  from its SHA-256 (no user-controlled paths). Evidence files are served only for names matching
  `mintgate-[a-z0-9-]+.(json|png|jpg|jpeg|webp|gif)`.
- **No HTML from evidence.** Statements, URLs and claims are rendered with `textContent` (ui.js `el`);
  URLs must start with `https://`, `http://` or `ipfs://` (no `javascript:`). Discord messages disable
  all mentions (`allowed_mentions.parse = []`), so a statement cannot ping `@everyone`.
- **Reviewer authorisation is on chain.** `/api/disputes/<net>/<id>/reason` needs a SIWE session, CSRF
  and `REVIEWER_ROLE` on that chain's registry; the API only pins the reason. The transaction comes from
  the reviewer's own wallet through `DisputeResolver`, which re-checks the role: the API cannot resolve
  anything by itself.
- **Safe signatures are verified before storage.** `POST /api/governance/<net>/<hash>/signatures` recovers
  the EIP-712 SafeTx signer and stores it only for a current owner (from the deployment file); the CLI checks
  our SafeTx hash against the Safe's own `getTransactionHash` before storing a proposal. A forged or
  replayed signature would also fail in the Safe itself (`GS026`).
- **Webhook secret.** `DISCORD_WEBHOOK_URL` is read from the environment and never logged; tests and the
  E2E stack pin it empty.
