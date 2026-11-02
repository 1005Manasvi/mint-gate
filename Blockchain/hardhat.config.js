require("dotenv").config();
require("@nomicfoundation/hardhat-toolbox");

// Secrets come from Blockchain/.env (git-ignored). See .env.example.
const {
  SEPOLIA_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com", // free public RPC
  BASE_SEPOLIA_RPC_URL = "https://sepolia.base.org",
  DEPLOYER_PRIVATE_KEY,
  ETHERSCAN_API_KEY = "",
  REPORT_GAS,
} = process.env;
const deployer = DEPLOYER_PRIVATE_KEY?.startsWith("0x") ? DEPLOYER_PRIVATE_KEY : `0x${DEPLOYER_PRIVATE_KEY}`;

module.exports = {
  solidity: {
    version: "0.8.24",
    // OpenZeppelin 5.4+ uses the MCOPY opcode, introduced in the Cancun upgrade.
    settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "cancun" },
  },
  networks: {
    localhost: { url: "http://127.0.0.1:8545" },
    ...(DEPLOYER_PRIVATE_KEY
      ? {
          sepolia: {
            url: SEPOLIA_RPC_URL || "https://ethereum-sepolia-rpc.publicnode.com",
            accounts: [deployer],
            chainId: 11155111,
          },
          baseSepolia: {
            url: BASE_SEPOLIA_RPC_URL || "https://sepolia.base.org",
            accounts: [deployer],
            chainId: 84532,
          },
        }
      : {}),
  },
  // `npx hardhat verify --network sepolia <address> <admin> <verifier> <reviewer>`
  etherscan: { apiKey: ETHERSCAN_API_KEY },
  sourcify: { enabled: true },
  // `REPORT_GAS=true npx hardhat test` writes the table that docs/contracts.md quotes.
  gasReporter: {
    enabled: Boolean(REPORT_GAS),
    outputFile: "gas-report.txt",
    noColors: true,
    excludeContracts: ["mocks/"],
  },
};
