// cd e2e; npm ci; npx playwright install chromium; npx playwright test
// Needs: docker compose up -d (Postgres + Redis), compiled contracts (cd Blockchain; npx hardhat compile).
// PW_CHANNEL=chrome (or msedge) uses an installed browser instead of Playwright's Chromium.
import { defineConfig } from "@playwright/test";

const python =
  process.env.E2E_PYTHON || (process.platform === "win32" ? "..\\venv\\Scripts\\python.exe" : "../venv/bin/python");
export const RPC_PORT = 8547;
export const API_PORT = 5057;

export default defineConfig({
  testDir: "tests",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${API_PORT}`,
    channel: process.env.PW_CHANNEL || undefined,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `npx hardhat node --port ${RPC_PORT}`,
      cwd: "../Blockchain",
      port: RPC_PORT,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `${python} stack.py`,
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: "pipe",
      env: { E2E_RPC_PORT: String(RPC_PORT), E2E_API_PORT: String(API_PORT) },
    },
  ],
});
