// Hands the registry over to the governance Safe (run after deploy-governance.js, by the current admin):
//   PAUSER_ROLE -> Safe (and revoked from every other holder listed in the deployment file),
//   treasury -> Safe, beginDefaultAdminTransfer(Safe).
// The Safe then needs a 2-of-3 transaction `acceptDefaultAdminTransfer()` once the admin delay has
// passed (python -m governance propose <network> accept-admin; owners sign on /governance).
// Usage: npx hardhat run scripts/handover.js --network <localhost|sepolia|baseSepolia>
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const { ethers, network } = hre;
  const [deployer] = await ethers.getSigners();
  const file = path.join(__dirname, "..", "..", "backend", "chain", "deployments", `${network.name}.json`);
  const dep = JSON.parse(fs.readFileSync(file, "utf8"));
  if (!dep.governance?.safe) throw new Error("no governance block: run scripts/deploy-governance.js first");
  const safe = dep.governance.safe.address;
  const registry = await ethers.getContractAt("MintGateRegistry", dep.registry.address, deployer);

  const tx = {};
  if (network.name !== "hardhat" && network.name !== "localhost") {
    const tip = ethers.parseUnits(process.env.PRIORITY_FEE_GWEI || "0.01", "gwei");
    const { baseFeePerGas } = await ethers.provider.getBlock("latest");
    Object.assign(tx, { maxPriorityFeePerGas: tip, maxFeePerGas: baseFeePerGas * 2n + tip });
  }
  const send = async (label, p) => console.log(`${label}: tx ${(await (await p).wait()).hash}`);

  if ((await registry.defaultAdmin()) === safe) {
    console.log("the Safe is already admin: nothing to do");
    return;
  }
  if ((await registry.defaultAdmin()) !== deployer.address) throw new Error("run this with the current admin key");

  const pauser = await registry.PAUSER_ROLE();
  if (!(await registry.hasRole(pauser, safe))) await send("PAUSER_ROLE -> Safe", registry.grantRole(pauser, safe, tx));
  for (const old of new Set([dep.roles?.pauser, deployer.address].filter(Boolean))) {
    if (old !== safe && (await registry.hasRole(pauser, old)))
      await send(`PAUSER_ROLE revoked from ${old}`, registry.revokeRole(pauser, old, tx));
  }
  if ((await registry.treasury()) !== safe) await send("treasury -> Safe", registry.setTreasury(safe, tx));

  const [pending, schedule] = await registry.pendingDefaultAdmin();
  if (pending !== safe) await send("beginDefaultAdminTransfer(Safe)", registry.beginDefaultAdminTransfer(safe, tx));
  const [, at] = await registry.pendingDefaultAdmin();
  console.log(
    `admin transfer to the Safe can be accepted after ${new Date(Number(at || schedule) * 1000).toISOString()}`,
  );

  dep.roles = { ...dep.roles, pauser: safe, treasury: safe, pendingAdmin: safe, adminAcceptableAfter: Number(at) };
  fs.writeFileSync(file, JSON.stringify(dep, null, 2) + "\n");
  console.log(`updated ${path.relative(process.cwd(), file)}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
