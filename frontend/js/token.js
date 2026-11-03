// Public provenance page: /token/<chain>/<contract>/<id>
"use strict";

import { el, explorerLink, link, short } from "./ui.js";
import { gatewayUrl, verifyToken } from "./provenance.js";

const $ = (id) => document.getElementById(id);
const [, , chainRef, contract, tokenId] = location.pathname.split("/").map(decodeURIComponent);
let net = null;
let gateway = "https://ipfs.io/ipfs/";

function fail(text) {
  $("loadState").className = "card state state-err";
  $("loadState").setAttribute("role", "alert");
  $("loadState").textContent = text;
}

async function json(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false)
    throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
  return data;
}

async function load() {
  if (!/^0x[0-9a-fA-F]{40}$/.test(contract || "") || !/^\d+$/.test(tokenId || "")) {
    fail("This link is not a valid token address: expected /token/<network>/<contract>/<token id>.");
    return;
  }
  try {
    const [nets, status] = await Promise.all([json("/api/networks"), json("/api/status").catch(() => ({}))]);
    if (status.ipfs_gateway) gateway = status.ipfs_gateway;
    net = nets.networks.find((n) => n.network === chainRef || String(n.chain_id) === chainRef);
  } catch (e) {
    fail(`Mint-Gate is unreachable (${e.message}).`);
    return;
  }
  if (!net) {
    fail(`Unknown network "${chainRef}".`);
    return;
  }
  document.title = `Token #${tokenId} · ${net.label} · Mint-Gate`;
  $("tokenTitle").textContent = `Token #${tokenId}`;
  $("tokenSub").replaceChildren(net.label, " · ", explorerLink(net, "address", contract, contract));
  $("loadState").hidden = true;
  $("tokenView").hidden = false;

  let t = null;
  try {
    t = await json(`/api/token/${net.network}/${contract}/${tokenId}`);
  } catch (e) {
    const why = e.status === 404 ? "not indexed by Mint-Gate (yet)" : `index unavailable: ${e.message}`;
    $("facts").replaceChildren(el("dt", {}, "Indexer"), el("dd", {}, why));
    $("tokenImgCap").textContent = "Run the verification to read the token straight from the chain.";
    return;
  }
  const v = t.verification || {};
  $("verdictBadge").className = `badge ${v.decision || ""}`;
  $("verdictBadge").textContent = t.status === "revoked" ? "REVOKED" : v.decision || "unknown";
  $("verdictScore").textContent = v.score ?? "–";
  const rows = [
    ["Status", t.status],
    ["Owner", explorerLink(net, "address", t.owner, t.owner)],
    ["Artist", explorerLink(net, "address", t.artist, t.artist)],
    ["Policy", v.policy_version || "–"],
    ["Report hash", t.report_hash],
    ["Image SHA-256", t.image_sha256],
    ["Mint tx", explorerLink(net, "tx", t.minted_tx)],
    ["Registry", explorerLink(net, "address", t.registry, t.registry)],
  ];
  if (t.token_uri) rows.push(["Token URI", link(short(t.token_uri, 24), t.token_uri_http)]);
  $("facts").replaceChildren(...rows.flatMap(([k, val]) => [el("dt", {}, k), el("dd", {}, val)]));
  if (t.report_id) {
    $("tokenImg").src = `/api/upload/${t.report_id}.png`;
    $("tokenImgCap").replaceChildren(link("Full verification report", `/?report=${t.report_id}`));
  }
}

const ICON = { true: "✓", false: "✕", null: "?" };

$("verifyBtn").addEventListener("click", async () => {
  const btn = $("verifyBtn");
  btn.disabled = true;
  btn.textContent = "Verifying…";
  $("checkList").replaceChildren();
  $("verifySummary").textContent = "Reading the chain…";
  try {
    const out = await verifyToken({ net, contract, tokenId, gateway, knownRegistry: net.registry }, (r) => {
      const li = el(
        "li",
        { className: `chk chk-${r.ok}` },
        el("span", { className: "chk-icon", "aria-hidden": "true" }, ICON[r.ok]),
        el(
          "div",
          {},
          el("b", {}, r.title),
          el(
            "span",
            { className: "sr-only" },
            r.ok === true ? " passed" : r.ok === false ? " failed" : " not verified",
          ),
          el("pre", { className: "chk-detail mono" }, r.detail),
        ),
      );
      $("checkList").append(li);
    });
    if (out.facts.metadata?.image && !$("tokenImg").src) {
      $("tokenImg").src = gatewayUrl(out.facts.metadata.image, gateway);
    }
    $("verifySummary").textContent = out.complete
      ? `✓ Provenance verified independently (chain read via ${out.facts.source}).`
      : out.ok
        ? "No contradiction found, but some checks could not be completed (see ?)."
        : "✕ Verification FAILED: this token's data does not match the chain.";
    $("verifySummary").className = `hint ${out.complete ? "t-pass" : out.ok ? "t-review" : "t-fail"}`;
  } catch (e) {
    $("verifySummary").textContent = `Could not verify: ${e.shortMessage || e.message}`;
    $("verifySummary").className = "hint t-fail";
  } finally {
    btn.disabled = false;
    btn.textContent = "Verify integrity";
  }
});

load();
