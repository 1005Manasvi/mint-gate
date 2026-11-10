// Phase 6 governance next to an existing v2 deployment (backend/chain/deployments/<network>.json):
//   * Safe v1.4.1 multisig (SAFE_OWNERS, SAFE_THRESHOLD; canonical factory on testnets, deployed locally)
//   * MintGateTimelock (TIMELOCK_DELAY s; proposer = the Safe, executor = anyone, no admin)
//   * PolicyRegistry owned by the timelock, seeded with backend/chain/policies.json
//   * DisputeResolver (reasoned dispute resolutions), granted REVIEWER_ROLE while the deployer is admin
// Writes a "governance" block into the deployment file. Re-running skips what exists.
// The role hand-over to the Safe is a separate step: scripts/handover.js.
// Usage: npx hardhat run scripts/deploy-governance.js --network <localhost|sepolia|baseSepolia>
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const hre = require("hardhat");
const S = require("./lib/safe");

const ROOT = path.join(__dirname, "..", "..");
const env = (k, d) => process.env[k] || d;

async function feeOverrides(ethers, network) {
  if (network.name === "hardhat" || network.name === "localhost") return {};
  const tip = ethers.parseUnits(env("PRIORITY_FEE_GWEI", "0.01"), "gwei");
  const { baseFeePerGas } = await ethers.provider.getBlock("latest");
  return { maxPriorityFeePerGas: tip, maxFeePerGas: baseFeePerGas * 2n + tip };
}

/** Policies to announce: backend/chain/policies.json (pinned by `python -m governance policies`);
 *  on a local chain, the policy files themselves with local URIs. */
function policies(local) {
  const file = path.join(ROOT, "backend", "chain", "policies.json");
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, "utf8")).policies;
  if (!local) throw new Error("backend/chain/policies.json missing: run `python -m governance policies` first");
  return ["policy.json", "policy.permissive.json"].map((name) => {
    const raw = fs.readFileSync(path.join(ROOT, "backend", name));
    return {
      version: JSON.parse(raw).version,
      sha256: crypto.createHash("sha256").update(raw).digest("hex"),
      uri: `http://127.0.0.1:5000/static-policy/${name}`,
    };
  });
}

async function main() {
  const { ethers, network, artifacts } = hre;
  const [deployer] = await ethers.getSigners();
  const local = network.name === "hardhat" || network.name === "localhost";
  const file = path.join(ROOT, "backend", "chain", "deployments", `${network.name}.json`);
  if (!fs.existsSync(file)) throw new Error(`${file} missing: deploy v2 first (scripts/deploy-v2.js)`);
  const dep = JSON.parse(fs.readFileSync(file, "utf8"));
  const gov = dep.governance || {};
  const { chainId } = await ethers.provider.getNetwork();
  const tx = await feeOverrides(ethers, network);
  const signers = await ethers.getSigners();
  const owners = env(
    "SAFE_OWNERS",
    local
      ? signers
          .slice(0, 3)
          .map((s) => s.address)
          .join(",")
      : "",
  )
    .split(",")
    .map((a) => ethers.getAddress(a.trim()))
    .filter(Boolean);
  const threshold = Number(env("SAFE_THRESHOLD", "2"));
  const delay = Number(env("TIMELOCK_DELAY", "3600"));
  if (owners.length < threshold || threshold < 2)
    throw new Error("need SAFE_OWNERS with at least SAFE_THRESHOLD (>= 2)");
  console.log(`governance on ${network.name} (chainId ${chainId}) from ${deployer.address}`);

  const record = async (name, address, receipt, extra = {}) => ({
    address,
    deployBlock: receipt ? receipt.blockNumber : null,
    deployTx: receipt ? receipt.hash : null,
    abi: (await artifacts.readArtifact(name)).abi,
    ...extra,
  });
  const deploy = async (name, ...args) => {
    const c = await (await ethers.getContractFactory(name)).deploy(...args, tx);
    const receipt = await c.deploymentTransaction().wait();
    const address = await c.getAddress();
    console.log(`${name.padEnd(17)} ${address} (block ${receipt.blockNumber})`);
    return record(name, address, receipt);
  };

  // 1. Safe
  if (!gov.safe) {
    const addrs = local ? await S.deploySingletons(deployer) : await S.canonical(ethers.provider);
    const l2 = S.L2_CHAINS.has(Number(chainId));
    const salt = BigInt(env("SAFE_SALT", "6")); // Phase 6
    const { address, receipt } = await S.createSafe(deployer, addrs, owners, threshold, {
      saltNonce: salt,
      l2,
      overrides: tx,
    });
    const where = receipt ? `(block ${receipt.blockNumber})` : "(existed)";
    console.log(`Safe ${threshold}-of-${owners.length}       ${address} ${where}`);
    gov.safe = {
      address,
      version: "1.4.1",
      singleton: l2 ? addrs.SafeL2 : addrs.Safe,
      factory: addrs.SafeProxyFactory,
      owners,
      threshold,
      deployBlock: receipt ? receipt.blockNumber : null,
      deployTx: receipt ? receipt.hash : null,
      abi: S.artifact(l2 ? "SafeL2" : "Safe").abi,
    };
  }
  const safe = gov.safe.address;

  // 2. Timelock (proposer + canceller = Safe, executor = anyone)
  if (!gov.timelock) {
    gov.timelock = { ...(await deploy("MintGateTimelock", delay, [safe], [ethers.ZeroAddress])), minDelay: delay };
  }
  // 3. PolicyRegistry owned by the timelock
  if (!gov.policyRegistry) {
    const ps = policies(local);
    gov.policyRegistry = await deploy(
      "PolicyRegistry",
      gov.timelock.address,
      ps.map((p) => "0x" + p.sha256),
      ps.map((p) => p.version),
      ps.map((p) => p.uri),
    );
  }
  // 4. DisputeResolver + its REVIEWER_ROLE (while the deployer is still admin)
  if (!gov.disputeResolver) gov.disputeResolver = await deploy("DisputeResolver", dep.registry.address);
  const registry = await ethers.getContractAt("MintGateRegistry", dep.registry.address);
  const reviewerRole = await registry.REVIEWER_ROLE();
  if (!(await registry.hasRole(reviewerRole, gov.disputeResolver.address))) {
    if ((await registry.defaultAdmin()) !== deployer.address)
      throw new Error("the deployer is no longer admin: grant REVIEWER_ROLE to the resolver through the Safe");
    const r = await (await registry.grantRole(reviewerRole, gov.disputeResolver.address, tx)).wait();
    console.log(`REVIEWER_ROLE granted to the resolver (tx ${r.hash})`);
  }

  dep.governance = gov;
  if (network.name === "hardhat") return;
  fs.writeFileSync(file, JSON.stringify(dep, null, 2) + "\n");
  console.log(`updated ${path.relative(process.cwd(), file)}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
