# Security review

Static analysis, test coverage and known limitations of Mint-Gate. Updated every time a contract
changes. Last review: **2026-11-02 (Phase 1)**: `MintGateNFT` v1 (Ethereum Sepolia
`0xae989b374DF85a942b4caE07940713Ff89418F32`) and contracts v2 (`MintGateRegistry`, `MintGateNFTV2`,
`MintGated`, `IMintGate`; addresses in [contracts.md](contracts.md#10-deployments)). The v2 design,
roles and threat model are in [contracts.md](contracts.md).

## Tools and how to reproduce

| Check | Tool | Command (from `Blockchain/`) | Result |
|---|---|---|---|
| Unit tests | Hardhat 2.29, chai | `npx hardhat test` | 91 passing (13 v1, 78 v2) |
| Coverage | solidity-coverage 0.8.17 | `npm run coverage` (fails below 95 %) | 100 % statements, branches, functions, lines |
| Fuzz + invariants | Foundry (forge 1.8.7) | `forge test` (CI: `FOUNDRY_PROFILE=ci`) | 11 fuzz properties, 4 invariants, all passing |
| Static analysis | Slither 0.11.6 | `slither . --fail-medium` (config: `slither.config.json`) | v2: no findings. v1: 0 high, 0 medium, 2 low |

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

## Known limitations (trust model)

These are design limits, not bugs; the roadmap addresses them.

- **Artist wallet is not proven yet (Phase 2).** The upload names the artist's address; the
  attestation can only be used by that wallet, but nothing proves the uploader controls it, so
  someone could claim another wallet's address first. Phase 3 adds SIWE + a signed submission.
- **v1 hot wallet (retired from the app in Phase 2).** v1's verifier key recorded verdicts *and*
  minted. The app now uses v2 only: the backend signs EIP-712 verdicts, the artist submits and
  mints, and a leaked key is handled by pause → role rotation → `voidVerdict` → dispute
  (playbook tested in `test/v2/MintGateRegistry.test.js`).
- **v2 signing key is still a hot key.** A leaked verifier key can sign PASS verdicts until it is
  revoked; signatures are valid for at most 30 days. Phase 7 moves the key to Azure Key Vault.
- **v2 disputes have no timeout.** A dispute nobody resolves keeps its bond locked (Phase 6).
- **Admin is an EOA.** `DEFAULT_ADMIN_ROLE` sits on the deployer wallet; v2 uses a two-step admin
  transfer with a delay, and Phase 6 moves admin to a Safe multisig.
- **Testnet roles are all one wallet** (`0xeed8…eD7F`: admin, verifier, reviewer, pauser, treasury).
  Production needs separate keys.
- **The reviewer key is a shared secret** in the API (`REVIEWER_KEY`), compared in constant time
  and rate-limited. Phase 3 replaces it with wallet signatures from REVIEWER_ROLE holders.
- **Detection is off-chain and probabilistic.** The contract proves which verdict and policy were
  used (report hash, policy SHA-256), not that the verdict is right. See README for error rates.
