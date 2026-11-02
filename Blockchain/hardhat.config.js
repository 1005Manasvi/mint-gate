require("dotenv").config();
require("@nomicfoundation/hardhat-toolbox");

// Secrets come from Blockchain/.env (git-ignored). See .env.example.
const {
  SEPOLIA_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com", // free public RPC
  DEPLOYER_PRIVATE_KEY,
  ETHERSCAN_API_KEY = "",
} = process.env;

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
            accounts: [DEPLOYER_PRIVATE_KEY.startsWith("0x") ? DEPLOYER_PRIVATE_KEY : `0x${DEPLOYER_PRIVATE_KEY}`],
            chainId: 11155111,
          },
        }
      : {}),
  },
  // `npx hardhat verify --network sepolia <address> <admin> <verifier> <reviewer>`
  etherscan: { apiKey: ETHERSCAN_API_KEY },
  sourcify: { enabled: true },
};
