# Safe{Core} smart account v1.4.1: compiled artifacts

ABI + creation / runtime bytecode of `Safe`, `SafeL2`, `SafeProxy`, `SafeProxyFactory` and
`CompatibilityFallbackHandler`, copied from the npm package `@safe-global/safe-contracts@1.4.1`
(`build/artifacts/`). Licence: LGPL-3.0 (`LICENSE`), © Safe Ecosystem Foundation.

Why vendored: the npm package has a peer dependency on ethers 5, which conflicts with this
project's ethers 6. Only the artifacts are needed: tests and the local deploy deploy them on a
Hardhat chain; on Sepolia / Base Sepolia the canonical deployments are used.

Checked on 2026-11-11: the `deployedBytecode` of each file is byte-identical to the code at the
canonical addresses on Ethereum Sepolia and Base Sepolia (`scripts/lib/safe.js` re-checks it
before every testnet deployment):

| Contract | Address (same on every chain) |
|---|---|
| Safe (L1 singleton) | `0x41675C099F32341bf84BFc5382aF534df5C7461a` |
| SafeL2 | `0x29fcB43b46531BcA003ddC8FCB67FFE91900C762` |
| SafeProxyFactory | `0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67` |
| CompatibilityFallbackHandler | `0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99` |
