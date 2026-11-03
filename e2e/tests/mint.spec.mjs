// Artist journey in a real browser: SIWE sign-in -> signed upload -> PASS -> mint paid by the artist's
// wallet (with confirmations) -> My artworks -> public provenance page verified against the chain.
// The "wallet" is an EIP-1193 shim that forwards to the local Hardhat node (its accounts are unlocked).
import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { RPC_PORT } from "../playwright.config.mjs";

const RPC = `http://127.0.0.1:${RPC_PORT}`;
const ARTIST = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"; // Hardhat account #1 (#0 is the verifier)
const OTHER = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"; // Hardhat account #2
const PHOTO = fileURLToPath(new URL("../../backend/tests/fixtures/photo.png", import.meta.url));

let rpcId = 0;
async function rpc(method, params = []) {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method, params }),
  });
  return res.json();
}

async function installWallet(page, account) {
  await page.exposeFunction("__mgWalletRpc", async (method, params) => {
    if (method === "eth_requestAccounts" || method === "eth_accounts") return { result: [account] };
    if (method === "wallet_switchEthereumChain" || method === "wallet_addEthereumChain") return { result: null };
    return rpc(method, params);
  });
  await page.addInitScript(() => {
    window.ethereum = {
      isMetaMask: true,
      async request({ method, params }) {
        const r = await window.__mgWalletRpc(method, params || []);
        if (r.error) throw Object.assign(new Error(r.error.message), { code: r.error.code, data: r.error.data });
        return r.result;
      },
      on() {},
      removeListener() {},
    };
  });
}

function watchCsp(page) {
  const violations = [];
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text());
  });
  page.on("pageerror", (e) => violations.push(`page error: ${e.message}`));
  return violations;
}

test("no session: submitting and attesting are refused", async ({ request }) => {
  expect((await request.post("/api/analyze")).status()).toBe(401);
  expect((await request.post(`/api/attest/${"0".repeat(32)}`, { data: {} })).status()).toBe(401);
  expect((await request.get("/api/me/artworks")).status()).toBe(401);
});

test("artist signs in, uploads, mints from their wallet, and the token verifies", async ({ page, browser }) => {
  const violations = watchCsp(page);
  await installWallet(page, ARTIST);
  const before = BigInt((await rpc("eth_getBalance", [ARTIST, "latest"])).result);

  await page.goto("/");
  await expect(page.locator("#walletBtn")).toHaveText("Sign in");
  await page.locator("#walletBtn").click();
  await expect(page.locator("#identity")).toContainText("Signed in as");
  await expect(page.locator("#walletBtn")).toHaveText(/^0x7099/);

  await page.locator("#fileInput").setInputFiles(PHOTO);
  await page.locator("#analyzeBtn").click();
  await expect(page.locator("#decision")).toHaveText("PASS", { timeout: 60_000 });
  await expect(page.locator("#prov")).toContainText("signed by the artist");
  const reportId = (await page.locator("#metaLine").textContent()).match(/report ([0-9a-f]{32})/)[1];

  // Another wallet opens the same report: it cannot get the attestation, so it cannot mint.
  const ctx = await browser.newContext();
  const other = await ctx.newPage();
  await installWallet(other, OTHER);
  await other.goto(`/?report=${reportId}`);
  await other.locator("#walletBtn").click();
  await expect(other.locator("#identity")).toContainText("Signed in as");
  await expect(other.locator("#mintBtn")).toBeDisabled();
  await expect(other.locator("#mintHint")).toContainText("Only the artist wallet");
  const csrf = (await (await other.request.get("/api/auth/me")).json()).csrf;
  const stolen = await other.request.post(`/api/attest/${reportId}`, { data: {}, headers: { "X-CSRF-Token": csrf } });
  expect(stolen.status()).toBe(403);
  await ctx.close();

  await page.locator("#royalty").fill("7.5");
  await page.locator("#mintBtn").click();
  await expect(page.locator("#txOut")).toContainText("Minted token #1", { timeout: 90_000 });
  await expect(page.locator('#mintSteps li[data-stage="confirming"]')).toHaveClass("done");
  await expect(page.locator('#mintSteps li[data-stage="confirming"] .step-extra')).toHaveText(/^[2-9]\d* \/ 2$/);

  const after = BigInt((await rpc("eth_getBalance", [ARTIST, "latest"])).result);
  expect(after).toBeLessThan(before); // the artist paid the gas

  await page.locator("#tab-mine").click();
  await expect(async () => {
    await page.locator("#mineRefresh").click();
    await expect(page.locator(".mrow .badge")).toHaveText("minted", { timeout: 2_000 });
  }).toPass({ timeout: 60_000 });
  await expect(page.locator(".mrow")).toContainText("Token #1");

  await page.locator(".mrow a", { hasText: "Token #1" }).click();
  await expect(page).toHaveURL(/\/token\/localhost\/0x[0-9a-fA-F]{40}\/1$/);
  await expect(page.locator("#tokenTitle")).toHaveText("Token #1");
  await expect(page.locator("#verdictBadge")).toHaveText("PASS");
  await page.locator("#verifyBtn").click();
  await expect(page.locator("#verifySummary")).toContainText("Provenance verified independently", { timeout: 60_000 });
  await expect(page.locator("#checkList .chk-true")).toHaveCount(9);
  await expect(page.locator("#checkList")).toContainText("recomputed in your browser");

  expect(violations).toEqual([]);
});
