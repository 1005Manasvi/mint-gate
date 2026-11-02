// Deploys MintGateRegistry + MintGateNFTV2, grants the NFT COLLECTION_ROLE and writes
// backend/chain/deployments/<network>.json (addresses, blocks, roles, ABIs).
// Usage: npx hardhat run scripts/deploy-v2.js --network <localhost|sepolia|baseSepolia>
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const { ethers, network, artifacts } = hre;
  const [deployer] = await ethers.getSigners();
  // Testnet: the deployer holds every role. Production: separate keys, admin -> Safe (Phase 6).
  const env = (k, d) => process.env[k] || d;
  const roles = {
    admin: deployer.address,
    verifier: env("VERIFIER_ADDRESS", deployer.address),
    reviewer: env("REVIEWER_ADDRESS", deployer.address),
    pauser: env("PAUSER_ADDRESS", deployer.address),
    treasury: env("TREASURY_ADDRESS", deployer.address),
  };
  const adminDelay = Number(env("ADMIN_DELAY", "3600")); // seconds; mainnet would use >= 2 days
  const disputeBond = ethers.parseEther(env("DISPUTE_BOND_ETH", "0.001"));
  const { chainId } = await ethers.provider.getNetwork();
  console.log(`deploying v2 to ${network.name} (chainId ${chainId}) from ${deployer.address}`);

  // ethers defaults to a 1 gwei tip. On Sepolia (base fee ~0) that tip was ~100 % of the cost of
  // the first v2 deployment (0.035 ETH), so cap it: PRIORITY_FEE_GWEI (default 0.01).
  const tx = {};
  if (network.name !== "hardhat" && network.name !== "localhost") {
    const tip = ethers.parseUnits(env("PRIORITY_FEE_GWEI", "0.01"), "gwei");
    const { baseFeePerGas } = await ethers.provider.getBlock("latest");
    Object.assign(tx, { maxPriorityFeePerGas: tip, maxFeePerGas: baseFeePerGas * 2n + tip });
  }

  const Registry = await ethers.getContractFactory("MintGateRegistry");
  const registry = await Registry.deploy(
    adminDelay,
    roles.admin,
    roles.verifier,
    roles.reviewer,
    roles.pauser,
    roles.treasury,
    disputeBond,
    tx,
  );
  const registryReceipt = await registry.deploymentTransaction().wait();
  const registryAddress = await registry.getAddress();
  console.log(`MintGateRegistry ${registryAddress} (block ${registryReceipt.blockNumber})`);

  const nft = await (await ethers.getContractFactory("MintGateNFTV2")).deploy(registryAddress, tx);
  const nftReceipt = await nft.deploymentTransaction().wait();
  const nftAddress = await nft.getAddress();
  console.log(`MintGateNFTV2    ${nftAddress} (block ${nftReceipt.blockNumber})`);

  const grant = await (await registry.grantRole(await registry.COLLECTION_ROLE(), nftAddress, tx)).wait();
  console.log(`COLLECTION_ROLE granted to the NFT (tx ${grant.hash})`);

  const contract = async (name, address, receipt) => ({
    address,
    deployBlock: receipt.blockNumber,
    deployTx: receipt.hash,
    abi: (await artifacts.readArtifact(name)).abi,
  });
  const out = {
    version: 2,
    network: network.name,
    chainId: Number(chainId),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    roles,
    adminDelay,
    disputeBond: disputeBond.toString(),
    eip712: { name: "MintGateRegistry", version: "1" },
    registry: await contract("MintGateRegistry", registryAddress, registryReceipt),
    nft: await contract("MintGateNFTV2", nftAddress, nftReceipt),
  };
  if (network.name === "hardhat") return; // in-process chain: nothing to record
  const file = path.join(__dirname, "..", "..", "backend", "chain", "deployments", `${network.name}.json`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(out, null, 2) + "\n");
  console.log(`written ${path.relative(process.cwd(), file)}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
