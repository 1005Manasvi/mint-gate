// Verifies the v2 contracts of a deployment on Sourcify with its v2 API
// (Hardhat's sourcify plugin still calls the removed v1 API).
// Usage: npx hardhat run scripts/verify-sourcify.js --network <sepolia|baseSepolia>
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const API = "https://sourcify.dev/server";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function verify(chainId, address, fqn, creationTx) {
  const existing = await fetch(`${API}/v2/contract/${chainId}/${address}`).then((r) => r.json());
  if (existing.match) return existing.match;

  const buildInfo = await hre.artifacts.getBuildInfo(fqn);
  const res = await fetch(`${API}/v2/verify/${chainId}/${address}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stdJsonInput: buildInfo.input,
      compilerVersion: buildInfo.solcLongVersion,
      contractIdentifier: fqn,
      creationTransactionHash: creationTx,
    }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`${fqn}: ${res.status} ${JSON.stringify(body)}`);
  for (let i = 0; i < 60; i++) {
    await sleep(5000);
    const job = await fetch(`${API}/v2/verify/${body.verificationId}`).then((r) => r.json());
    if (job.isJobCompleted) {
      if (job.error) throw new Error(`${fqn}: ${JSON.stringify(job.error)}`);
      return job.contract.match;
    }
  }
  throw new Error(`${fqn}: verification job did not finish`);
}

async function main() {
  const file = path.join(__dirname, "..", "..", "backend", "chain", "deployments", `${hre.network.name}.json`);
  const dep = JSON.parse(fs.readFileSync(file, "utf8"));
  for (const [key, fqn] of [
    ["registry", "contracts/v2/MintGateRegistry.sol:MintGateRegistry"],
    ["nft", "contracts/v2/MintGateNFTV2.sol:MintGateNFTV2"],
  ]) {
    const match = await verify(dep.chainId, dep[key].address, fqn, dep[key].deployTx);
    console.log(`${fqn} ${dep[key].address}: ${match}`);
    dep[key].sourcify = match;
  }
  fs.writeFileSync(file, JSON.stringify(dep, null, 2) + "\n");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
