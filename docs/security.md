# Security review

Static analysis, test coverage and known limitations of Mint-Gate. Updated every time a contract
changes. Last review: **2026-11-02 (Phase 0)**, contract `MintGateNFT` v1
(Sepolia `0xae989b374DF85a942b4caE07940713Ff89418F32`).

## Tools and how to reproduce

| Check | Tool | Command (from `Blockchain/`) | Result |
|---|---|---|---|
| Unit tests | Hardhat 2.29, chai | `npx hardhat test` | 13 passing |
| Coverage | solidity-coverage 0.8.17 | `npm run coverage` (fails below 95 %) | 100 % statements, branches, functions, lines |
| Static analysis | Slither 0.11.6 | `slither . --fail-medium` (config: `slither.config.json`) | 0 high, 0 medium, 2 low, 1 informational |

All three run in CI (`.github/workflows/ci.yml`). Slither analyses our own contracts only
(`exclude_dependencies`); OpenZeppelin findings are listed separately below.

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

## Known limitations (trust model)

These are design limits of v1, not bugs; the roadmap addresses them.

- **One hot wallet.** The backend's verifier key records verdicts *and* mints. If it leaks, an
  attacker can record PASS verdicts for anything. Phase 1 moves to EIP-712 verdicts signed by the
  verifier and submitted by the artist, plus pause and key rotation.
- **Admin is an EOA.** `DEFAULT_ADMIN_ROLE` sits on the deployer wallet; Phase 6 moves it to a Safe multisig.
- **The reviewer key is a shared secret** in the API (`REVIEWER_KEY`), compared in constant time
  and rate-limited. Phase 3 replaces it with wallet signatures from REVIEWER_ROLE holders.
- **Detection is off-chain and probabilistic.** The contract proves which verdict and policy were
  used (report hash, policy SHA-256), not that the verdict is right. See README for error rates.
