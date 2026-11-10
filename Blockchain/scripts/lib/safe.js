// Safe v1.4.1 helpers (vendored artifacts in vendor/safe-1.4.1): deploy the singletons on a local
// chain, check the canonical deployments on testnets, create a Safe, hash / sign / execute SafeTxs.
const fs = require("fs");
const path = require("path");
const { ethers } = require("ethers");

const VENDOR = path.join(__dirname, "..", "..", "vendor", "safe-1.4.1");
const CANONICAL = {
  Safe: "0x41675C099F32341bf84BFc5382aF534df5C7461a",
  SafeL2: "0x29fcB43b46531BcA003ddC8FCB67FFE91900C762",
  SafeProxyFactory: "0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67",
  CompatibilityFallbackHandler: "0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99",
};
const L2_CHAINS = new Set([84532, 8453, 10, 11155420, 42161, 421614]); // SafeL2 on rollups (events for indexers)
const ZERO = ethers.ZeroAddress;

const SAFE_TX_TYPES = {
  SafeTx: [
    { name: "to", type: "address" },
    { name: "value", type: "uint256" },
    { name: "data", type: "bytes" },
    { name: "operation", type: "uint8" },
    { name: "safeTxGas", type: "uint256" },
    { name: "baseGas", type: "uint256" },
    { name: "gasPrice", type: "uint256" },
    { name: "gasToken", type: "address" },
    { name: "refundReceiver", type: "address" },
    { name: "nonce", type: "uint256" },
  ],
};

function artifact(name) {
  return JSON.parse(fs.readFileSync(path.join(VENDOR, `${name}.json`), "utf8"));
}

/** Deploy singleton, proxy factory and fallback handler (local chains only). */
async function deploySingletons(signer, overrides = {}) {
  const out = {};
  for (const name of ["Safe", "SafeL2", "SafeProxyFactory", "CompatibilityFallbackHandler"]) {
    const a = artifact(name);
    const c = await new ethers.ContractFactory(a.abi, a.bytecode, signer).deploy(overrides);
    await c.waitForDeployment();
    out[name] = await c.getAddress();
  }
  return out;
}

/** The canonical addresses, after checking that each holds exactly the vendored runtime code. */
async function canonical(provider) {
  for (const [name, address] of Object.entries(CANONICAL)) {
    const code = await provider.getCode(address);
    if (code.toLowerCase() !== artifact(name).deployedBytecode.toLowerCase())
      throw new Error(`${name} at ${address}: code differs from Safe v1.4.1 (or is missing)`);
  }
  return { ...CANONICAL };
}

/** Create a Safe proxy (owners, threshold) through the factory; returns its address. */
async function createSafe(signer, addrs, owners, threshold, { saltNonce = 0n, l2 = false, overrides = {} } = {}) {
  const singleton = l2 ? addrs.SafeL2 : addrs.Safe;
  const safeIface = new ethers.Interface(artifact("Safe").abi);
  const setup = safeIface.encodeFunctionData("setup", [
    owners,
    threshold,
    ZERO,
    "0x",
    addrs.CompatibilityFallbackHandler,
    ZERO,
    0,
    ZERO,
  ]);
  const factory = new ethers.Contract(addrs.SafeProxyFactory, artifact("SafeProxyFactory").abi, signer);
  const predicted = await factory.createProxyWithNonce.staticCall(singleton, setup, saltNonce);
  if ((await signer.provider.getCode(predicted)) !== "0x") return { address: predicted, receipt: null, existed: true };
  const receipt = await (await factory.createProxyWithNonce(singleton, setup, saltNonce, overrides)).wait();
  return { address: predicted, receipt, existed: false };
}

function safeContract(address, runner) {
  return new ethers.Contract(address, artifact("Safe").abi, runner);
}

/** A SafeTx with Safe's defaults (no refund, no gas token). */
function safeTx(to, data, nonce, { value = 0n, operation = 0 } = {}) {
  return {
    to,
    value,
    data,
    operation,
    safeTxGas: 0n,
    baseGas: 0n,
    gasPrice: 0n,
    gasToken: ZERO,
    refundReceiver: ZERO,
    nonce: BigInt(nonce),
  };
}

const domain = (chainId, safeAddress) => ({ chainId, verifyingContract: safeAddress });

function hashSafeTx(chainId, safeAddress, tx) {
  return ethers.TypedDataEncoder.hash(domain(chainId, safeAddress), SAFE_TX_TYPES, tx);
}

/** EIP-712 signature of an owner (what a wallet's eth_signTypedData_v4 returns). */
async function signSafeTx(signer, chainId, safeAddress, tx) {
  return {
    signer: await signer.getAddress(),
    data: await signer.signTypedData(domain(chainId, safeAddress), SAFE_TX_TYPES, tx),
  };
}

/** Safe expects signatures concatenated in ascending signer order. */
function packSignatures(sigs) {
  const sorted = [...sigs].sort((a, b) => (BigInt(a.signer) < BigInt(b.signer) ? -1 : 1));
  return ethers.concat(sorted.map((s) => s.data));
}

async function execSafeTx(safe, tx, sigs, overrides = {}) {
  return safe.execTransaction(
    tx.to,
    tx.value,
    tx.data,
    tx.operation,
    tx.safeTxGas,
    tx.baseGas,
    tx.gasPrice,
    tx.gasToken,
    tx.refundReceiver,
    packSignatures(sigs),
    overrides,
  );
}

module.exports = {
  CANONICAL,
  L2_CHAINS,
  SAFE_TX_TYPES,
  artifact,
  deploySingletons,
  canonical,
  createSafe,
  safeContract,
  safeTx,
  hashSafeTx,
  signSafeTx,
  packSignatures,
  execSafeTx,
};
