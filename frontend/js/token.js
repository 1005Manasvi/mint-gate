// Public provenance page: /token/<chain>/<contract>/<id>
"use strict";

import { describeEvent, el, explorerLink, formatEth, link, markSteps, msg, short, when } from "./ui.js";
import { gatewayUrl, readProvider, verifyToken } from "./provenance.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const [, , chainRef, contract, tokenId] = location.pathname.split("/").map(decodeURIComponent);
let net = null;
let token = null; // indexer view of this token
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
  token = t;
  loadTimeline();
  wallet
    .restore()
    .then(refreshCredits)
    .catch(() => {});
  const v = t.verification || {};
  $("revokedBanner").hidden = t.status !== "revoked";
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

// ---------------- timeline + disputes (Phase 6) ----------------

const gw = (u) => gatewayUrl(u, gateway);
const DISPUTE_STAGES = ["signin", "pin", "switching", "estimating", "wallet", "pending", "confirming"];

async function loadTimeline() {
  let tl;
  try {
    tl = await json(`/api/token/${net.network}/${contract}/${tokenId}/timeline`);
  } catch (e) {
    $("timeline").replaceChildren(el("li", { className: "hint" }, `Timeline unavailable: ${e.message}`));
    return;
  }
  $("timeline").replaceChildren(
    ...tl.events.map((e) =>
      el(
        "li",
        { className: `tl tl-${e.event}` },
        el("time", { className: "tl-time" }, when(e.time) || `block ${e.block}`),
        el("span", { className: "tl-text" }, ...describeEvent(e, net, gw), " · ", explorerLink(net, "tx", e.tx)),
      ),
    ),
  );
  const ds = Object.entries(tl.disputes);
  $("disputeList").replaceChildren(
    ds.length
      ? el(
          "ul",
          { className: "mrow-list" },
          ...ds.map(([id, d]) =>
            el(
              "li",
              { className: d.status === "open" ? "t-review" : d.status === "upheld" ? "t-fail" : "" },
              `#${id}: ${d.status} · `,
              link("reporter evidence", d.evidence_http),
              d.backend_evidence_uri ? [" · ", link("Mint-Gate evidence", gw(d.backend_evidence_uri))] : "",
              d.reason_http ? [" · ", link("reviewer's reason", d.reason_http)] : "",
            ),
          ),
        )
      : el("p", { className: "hint" }, "No disputes."),
  );
  const open = tl.status === "flagged" || tl.status === "revoked";
  $("reportBox").hidden = open;
  $("bondAmount").textContent = net.dispute_bond ? `${formatEth(net.dispute_bond)} ETH` : "the registry's bond";
  refreshCredits();
}

async function refreshCredits() {
  const who = wallet.getAccount();
  if (!who) return;
  try {
    const credit = await wallet.creditsOf(net, who, (await readProvider(net)).provider);
    $("bondBox").hidden = credit === 0n;
    $("bondText").textContent = `${short(who, 8)} can withdraw ${formatEth(credit)} ETH of returned dispute bonds.`;
  } catch {
    $("bondBox").hidden = true;
  }
}

$("withdrawBtn").addEventListener("click", async () => {
  const btn = $("withdrawBtn");
  btn.disabled = true;
  try {
    await wallet.loadNetworks();
    const out = await wallet.withdrawBond(wallet.networkByName(net.network) || net, (s) =>
      msg($("bondText"), "warn", `Withdrawal: ${s.stage}…`),
    );
    msg($("bondText"), "ok", "Withdrawn. ", explorerLink(net, "tx", out.hash));
  } catch (e) {
    msg($("bondText"), "err", wallet.explainError(e));
  } finally {
    btn.disabled = false;
  }
});

$("disputeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("disputeBtn");
  const steps = $("disputeSteps");
  const step = (stage, extra = "", failed = false) => markSteps(steps, DISPUTE_STAGES, stage, extra, failed);
  let stage = "signin";
  btn.disabled = true;
  steps.hidden = false;
  $("disputeOut").replaceChildren();
  try {
    step("signin");
    if (!wallet.getSession()) await wallet.signIn();
    stage = "pin";
    step("pin");
    const form = new FormData();
    form.set("network", net.network);
    form.set("contract", contract);
    form.set("token_id", tokenId);
    form.set("statement", $("dStatement").value);
    form.set("urls", $("dUrls").value);
    form.set("first_published", $("dFirst").value);
    form.set("prior_claims", $("dClaims").value);
    if ($("dOriginal").files[0]) form.set("original", $("dOriginal").files[0]);
    const info = await wallet.api("/api/disputes/evidence", { method: "POST", body: form });
    step("pin", link("pinned", gw(info.evidence_uri)));
    const out = await wallet.openDispute(info, (s) => {
      stage = s.stage;
      const extra =
        s.stage === "pending"
          ? explorerLink(net, "tx", s.hash)
          : s.stage === "confirming"
            ? `${s.confirmations} / ${s.needed}`
            : "";
      step(s.stage, extra);
    });
    msg(
      $("disputeOut"),
      "ok",
      `Dispute #${out.disputeId ?? "?"} opened. Reviewers are notified; the token is flagged until they decide. `,
      explorerLink(net, "tx", out.hash),
    );
    $("disputeForm").reset();
    setTimeout(loadTimeline, 4000); // the indexer needs a few blocks
  } catch (err) {
    step(stage, "", true);
    msg($("disputeOut"), "err", wallet.explainError(err));
  } finally {
    btn.disabled = false;
  }
});
