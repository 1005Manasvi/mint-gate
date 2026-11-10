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

test.describe.configure({ mode: "serial" });

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

// ---- Phase 6: dispute -> reasoned resolution -> revoked token -> bond back; Safe signs on /explorer ----
const REVIEWER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"; // Hardhat #0: holds REVIEWER_ROLE in the stack
const OWNER_B = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"; // Hardhat #3: Safe owners are #0, #2, #3

test("a reporter disputes token #1, a reviewer upholds it with a reason, the bond comes back", async ({ browser }) => {
  const reporterCtx = await browser.newContext();
  const page = await reporterCtx.newPage();
  const violations = watchCsp(page);
  await installWallet(page, OTHER);
  await page.goto("/");
  const nft = (await (await page.request.get("/api/networks")).json()).networks[0].nft;

  await page.goto(`/token/localhost/${nft}/1`);
  await expect(page.locator("#timeline")).toContainText("Minted as token #1");
  await page.locator("#reportBox summary").click();
  await expect(page.locator("#bondAmount")).toHaveText("0.001 ETH");
  await page.locator("#dStatement").fill("I painted this in 2019; the minter copied it from my portfolio.");
  await page.locator("#dUrls").fill("https://example.org/portfolio/painting");
  await page.locator("#dOriginal").setInputFiles(PHOTO);
  await page.locator("#disputeBtn").click();
  await expect(page.locator("#disputeOut")).toContainText("Dispute #1 opened", { timeout: 90_000 });
  await expect(async () => {
    await page.reload();
    await expect(page.locator("#disputeList")).toContainText("#1: open", { timeout: 2_000 });
  }).toPass({ timeout: 60_000 });

  // the reviewer: side-by-side evidence, then a written reason resolved from their own wallet
  const revCtx = await browser.newContext();
  const rev = await revCtx.newPage();
  const revViolations = watchCsp(rev);
  await installWallet(rev, REVIEWER);
  await rev.goto("/?view=disputes&dispute=localhost:1");
  await rev.locator("#walletBtn").click();
  await expect(rev.locator("#identity")).toContainText("Signed in as");
  await expect(async () => {
    await rev.goto("/?view=disputes&dispute=localhost:1");
    await expect(rev.locator("#ddBackend")).toContainText("Earliest signed submissions", { timeout: 3_000 });
  }).toPass({ timeout: 60_000 });
  await expect(rev.locator("#ddReporter")).toContainText("I painted this in 2019");
  await expect(rev.locator("#ddBackend")).toContainText("Identical pixels");
  await expect(rev.locator("#decisionForm")).toBeVisible();
  await rev.locator('input[name="outcome"][value="upheld"]').check();
  await rev.locator("#ddReason").fill("The reporter's original is pixel-identical and was published before the mint.");
  await rev.locator("#ddResolveBtn").click();
  await expect(rev.locator("#ddOut")).toContainText("Dispute upheld", { timeout: 90_000 });
  await expect(async () => {
    await rev.goto("/?view=disputes&dispute=localhost:1");
    await expect(rev.locator("#ddDecideBody")).toContainText("written reason", { timeout: 3_000 });
  }).toPass({ timeout: 60_000 });

  // the token is revoked everywhere; the reporter withdraws the returned bond
  await page.reload();
  await expect(page.locator("#revokedBanner")).toBeVisible();
  await expect(page.locator("#timeline")).toContainText("written reason");
  await expect(page.locator("#timeline")).toContainText("Artwork revoked");
  await expect(page.locator("#bondBox")).toBeVisible();
  await page.locator("#withdrawBtn").click();
  await expect(page.locator("#bondText")).toContainText("Withdrawn", { timeout: 60_000 });

  // explorer: statistics + the policy announced on chain, verified in the browser
  await page.goto("/explorer");
  await expect(page.locator("#statsBox")).toContainText("upheld");
  await expect(page.locator("#statsBox")).toContainText("Artwork revoked");
  await expect(page.locator("#polBox")).toContainText("is announced on chain");
  await page.locator("#polBox button", { hasText: "Verify" }).first().click();
  await expect(page.locator("#polBox")).toContainText("file matches the on-chain hash");

  expect([...violations, ...revViolations]).toEqual([]);
  await reporterCtx.close();
  await revCtx.close();
});

test("two Safe owners sign the admin hand-over on /explorer, a third party executes it", async ({ browser }) => {
  const sign = async (account) => {
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    await installWallet(p, account);
    await p.goto("/explorer");
    await p.locator("#govBox button", { hasText: "Sign as owner" }).click();
    await expect(p.locator("#govBox")).toContainText("Signature stored");
    return ctx;
  };
  const a = await sign(REVIEWER); // owner #0
  // a non-owner cannot sign
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await installWallet(p, ARTIST);
  await p.goto("/explorer");
  await p.locator("#govBox button", { hasText: "Sign as owner" }).click();
  await expect(p.locator("#govBox")).toContainText("is not an owner");
  const b = await sign(OWNER_B);
  // anyone executes (here the artist's wallet pays the gas)
  await p.reload();
  await p.locator("#govBox button", { hasText: "Execute" }).click();
  await expect(p.locator("#govBox")).toContainText("Executed", { timeout: 60_000 });
  await expect(async () => {
    await p.reload();
    await expect(p.locator("#govBox")).toContainText("accept-admin · executed", { timeout: 3_000 });
  }).toPass({ timeout: 60_000 });
  const gov = (await (await p.request.get("/api/networks")).json()).networks[0].governance;
  const admin = await rpc("eth_call", [
    { to: (await (await p.request.get("/api/networks")).json()).networks[0].registry, data: "0x84ef8ffc" },
    "latest",
  ]);
  expect(admin.result.slice(-40).toLowerCase()).toBe(gov.safe.slice(2).toLowerCase()); // defaultAdmin() == Safe
  for (const c of [a, b, ctx]) await c.close();
});
