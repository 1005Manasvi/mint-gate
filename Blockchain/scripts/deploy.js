// Deploys MintGateNFT and writes address + ABI to backend/chain/deployment.json,
// which the Flask backend reads.  Usage:  npx hardhat run scripts/deploy.js --network localhost
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  // Demo: the deployer is admin, verifier (Mint-Gate backend) and reviewer.
  // In production these would be three separate keys / a multisig.
  const verifier = process.env.VERIFIER_ADDRESS || deployer.address;
  const reviewer = process.env.REVIEWER_ADDRESS || deployer.address;

  const Factory = await hre.ethers.getContractFactory("MintGateNFT");
  const contract = await Factory.deploy(deployer.address, verifier, reviewer);
  await contract.waitForDeployment();
  const address = await contract.getAddress();
  const receipt = await contract.deploymentTransaction().wait();
  const { chainId } = await hre.ethers.provider.getNetwork();

  const artifact = await hre.artifacts.readArtifact("MintGateNFT");
  const out = path.join(__dirname, "..", "..", "backend", "chain", "deployment.json");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(
    out,
    JSON.stringify(
      {
        network: hre.network.name,
        chainId: Number(chainId),
        address,
        deployBlock: receipt.blockNumber,
        deployTx: receipt.hash,
        deployer: deployer.address,
        verifier,
        reviewer,
        abi: artifact.abi,
      },
      null,
      2,
    ),
  );

  console.log(`MintGateNFT deployed to ${address} on ${hre.network.name} (chainId ${chainId})`);
  console.log(`deployment written to ${out}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
