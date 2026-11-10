// Mint-Gate main page: sign in with a wallet, upload (signed), see the verdict, mint from the wallet.
"use strict";

import { el, explorerLink, formatEth as fmtEth, link, markSteps, msg, short, tokenPath, when } from "./ui.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const CHECK_ORDER = [
  "provenance",
  "ai_detector",
  "manipulation",
  "copy_move",
  "copy_exact",
  "copy_semantic",
  "copy_member",
  "copy_geometric",
];
const CHECK_TITLE = {
  provenance: "Provenance (C2PA, watermark, metadata)",
  ai_detector: "AI-image detector",
  manipulation: "Manipulation (TruFor)",
  copy_move: "Copy-move",
  copy_exact: "Exact copy (PDQ)",
  copy_semantic: "Edited copy (SSCD)",
  copy_member: "Re-mint of a collection NFT (SSCD)",
  copy_geometric: "Partial copy (LightGlue)",
};
const CHAIN_NAMES = { 1: "Ethereum", 8453: "Base" };
// provenance check values (detection/provenance.py)
const PROVENANCE_TEXT = { 1: "AI evidence", 0.6: "advisory", 0: "none", "-1": "trusted capture" };
const RISK_COLOR = (r, advisory) =>
  advisory && r >= 0.5 ? "#6366f1" : r >= 1 ? "var(--fail)" : r >= 0.5 ? "var(--review)" : "var(--pass)";
const STATUS_TEXT = {
  queued: "queued",
  processing: "analyzing",
  PASS: "PASS",
  REVIEW: "in review",
  FAIL: "FAIL",
  REVIEW_APPROVED: "approved",
  REVIEW_REJECTED: "rejected",
  MINTED: "minted",
  invalid: "invalid image",
  error: "error",
};

let selectedFile = null;
let current = null; // current report
let status = {}; // /api/status
let ipfsGateway = "https://ipfs.io/ipfs/";
let netName = null; // network selected for minting

const store = {
  get: (k) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k, v) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* private mode: not remembered */
    }
  },
};

const selectedNet = () => wallet.networkByName(netName);
const httpUri = (uri) => (uri && uri.startsWith("ipfs://") ? ipfsGateway + uri.slice(7) : uri);
const explorer = (kind, value, text) => explorerLink(selectedNet(), kind, value, text);

function notify(kind, text) {
  const box = msg($("globalMsg"), kind, text, " ");
  const close = el("button", { className: "btn-link", "aria-label": "Dismiss" }, "×");
  close.addEventListener("click", () => $("globalMsg").replaceChildren());
  box.append(close);
}

// ---------------- tabs (WAI-ARIA tabs pattern: arrow keys move between tabs) ----------------
const tabs = [...document.querySelectorAll(".tab")];

function openTab(view, focus = false) {
  for (const t of tabs) {
    const on = t.dataset.view === view;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  }
  for (const v of document.querySelectorAll(".view")) {
    const on = v.id === `view-${view}`;
    v.classList.toggle("active", on);
    v.hidden = !on;
  }
  if (view === "queue") loadQueue();
  if (view === "about") loadStatus(true);
  if (view === "mine") loadMine();
  if (view === "disputes") loadDisputes();
}

tabs.forEach((t, i) => {
  t.addEventListener("click", () => openTab(t.dataset.view));
  t.addEventListener("keydown", (e) => {
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    openTab(tabs[(next + tabs.length) % tabs.length].dataset.view, true);
  });
});

// ---------------- wallet + session ----------------
function renderIdentity() {
  const s = wallet.getSession();
  const acc = wallet.getAccount();
  const btn = $("walletBtn");
  btn.classList.toggle("connected", Boolean(s));
  $("signOutBtn").hidden = !s;
  const id = $("identity");
  if (!wallet.hasWallet()) {
    btn.textContent = "Get a wallet";
    id.replaceChildren(
      "No browser wallet detected. Install ",
      link("MetaMask", "https://metamask.io/download/"),
      " (or another EIP-1193 wallet) to submit and mint.",
    );
  } else if (!acc && !s) {
    btn.textContent = "Connect wallet";
    id.replaceChildren("Connect and sign in with your wallet to submit artwork.");
  } else if (!s || (acc && acc !== s.address)) {
    btn.textContent = "Sign in";
    id.replaceChildren(
      acc && s ? `Your wallet switched to ${short(acc, 6)}: ` : `Wallet ${short(acc, 6)} connected. `,
      "Sign in (a free signature, no transaction) to submit artwork.",
    );
  } else {
    btn.textContent = short(s.address, 6);
    btn.title = `Signed in as ${s.address}`;
    id.replaceChildren(
      el("span", { className: "dot dot-ok", "aria-hidden": "true" }),
      " Signed in as ",
      el("b", { className: "mono" }, short(s.address, 8)),
      ". Uploads and mints use this wallet.",
    );
  }
  updateAnalyzeButton();
  if (current) renderActions(current);
}

function signedIn() {
  const s = wallet.getSession();
  const acc = wallet.getAccount();
  return Boolean(s && (!acc || acc === s.address));
}

function updateAnalyzeButton() {
  const b = $("analyzeBtn");
  if (b.dataset.busy) return;
  b.disabled = !selectedFile || !wallet.hasWallet();
  b.textContent = signedIn() ? "Sign & analyze" : "Sign in & analyze";
}

async function ensureSignedIn() {
  if (!signedIn()) await wallet.signIn();
}

$("walletBtn").addEventListener("click", async () => {
  if (!wallet.hasWallet()) {
    window.open("https://metamask.io/download/", "_blank", "noopener");
    return;
  }
  if (signedIn()) {
    openTab("mine", true);
    return;
  }
  try {
    await wallet.signIn();
    notify("ok", "Signed in. Your uploads are now signed by this wallet.");
  } catch (e) {
    notify("err", `Sign-in failed: ${wallet.explainError(e)}`);
  }
});

$("signOutBtn").addEventListener("click", async () => {
  await wallet.signOut().catch(() => {});
  if (!$("view-mine").hidden) loadMine();
});

wallet.onChange(renderIdentity);

// ---------------- networks ----------------
async function initNetworks() {
  let nets;
  try {
    nets = await wallet.loadNetworks();
  } catch {
    return;
  }
  const remembered = store.get("mintgate.network");
  netName = nets.networks.some((n) => n.network === remembered) ? remembered : nets.default;
  for (const sel of [$("netSelect"), $("lookupNet")]) {
    sel.replaceChildren(...nets.networks.map((n) => el("option", { value: n.network }, n.label)));
    sel.value = netName;
  }
  if (!nets.networks.length) $("netSelect").replaceChildren(el("option", {}, "no network"));
}

$("netSelect").addEventListener("change", async (e) => {
  netName = e.target.value;
  $("lookupNet").value = netName;
  store.set("mintgate.network", netName);
  loadStatus();
  if (wallet.getAccount()) {
    try {
      await wallet.ensureChain(selectedNet());
    } catch (err) {
      notify("warn", `Could not switch your wallet: ${wallet.explainError(err)}`);
    }
  }
});

// ---------------- status ----------------
async function loadStatus(dump = false) {
  try {
    status = await wallet.api("/api/status");
    if (status.ipfs_gateway) ipfsGateway = status.ipfs_gateway;
    const n = status.networks?.[netName || status.chain?.network];
    const pill = $("chainPill");
    if (n && n.indexed_block !== null) {
      pill.className = "pill pill-ok";
      pill.textContent = `${n.network} · block ${n.indexed_block} · ${n.tokens} minted`;
      pill.title = `MintGateNFTV2 ${n.nft}`;
      $("footContract").replaceChildren("collection ", explorer("address", n.nft));
    } else {
      pill.className = "pill pill-bad";
      pill.textContent = n ? `${n.network}: not indexed` : "chain offline";
      pill.title = "The chain indexer has not processed this network yet";
    }
    if (dump) $("statusDump").textContent = JSON.stringify(status, null, 2);
  } catch {
    $("chainPill").className = "pill pill-bad";
    $("chainPill").textContent = "backend offline";
  }
}

// ---------------- upload ----------------
function setFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    notify("err", "Please choose an image file (PNG, JPG, WEBP).");
    return;
  }
  selectedFile = file;
  $("preview").src = URL.createObjectURL(file);
  $("preview").hidden = false;
  $("dropText").hidden = true;
  updateAnalyzeButton();
}

$("fileInput").addEventListener("change", (e) => setFile(e.target.files[0]));
const dz = $("dropzone");
dz.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("fileInput").click();
  }
});
["dragenter", "dragover"].forEach((ev) =>
  dz.addEventListener(ev, (e) => {
    e.preventDefault();
    dz.classList.add("drag");
  }),
);
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
  }),
);
dz.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

$("clearBtn").addEventListener("click", () => {
  selectedFile = null;
  current = null;
  $("fileInput").value = "";
  $("preview").hidden = true;
  $("dropText").hidden = false;
  $("results").hidden = true;
  updateAnalyzeButton();
  resetPipeline();
});

async function loadSamples() {
  try {
    const names = await (await fetch("/api/samples")).json();
    $("samples").replaceChildren(
      ...names.map((n) => {
        const label = n.replace(/\.(png|jpg)$/i, "").replace(/_/g, " ");
        const b = el(
          "button",
          { className: "sample", title: n, "aria-label": `Use example: ${label}` },
          el("img", { src: `/api/samples/${encodeURIComponent(n)}`, alt: "" }),
          el("span", {}, label),
        );
        b.addEventListener("click", async () => {
          const blob = await (await fetch(`/api/samples/${encodeURIComponent(n)}`)).blob();
          setFile(new File([blob], n, { type: blob.type }));
        });
        return b;
      }),
    );
  } catch {
    /* samples are optional */
  }
}

// ---------------- pipeline animation ----------------
const steps = () => [...document.querySelectorAll("#pipeline li")];
function resetPipeline() {
  steps().forEach((li) => (li.className = ""));
}

let animTimer = null;
function animatePipeline() {
  resetPipeline();
  let i = 0;
  const s = steps();
  s[0].className = "running";
  animTimer = setInterval(() => {
    if (i < s.length - 1) {
      s[i].className = "s-DONE";
      s[++i].className = "running";
    }
  }, 350);
}

function finishPipeline(report) {
  clearInterval(animTimer);
  for (const li of steps()) {
    const k = li.dataset.step;
    if (report.checks[k]) li.className = `s-${report.checks[k].status}`;
    else if (k === "policy") li.className = `s-${report.decision}`;
    else li.className = "s-DONE";
  }
}

// ---------------- analyze (sign -> submit -> job id -> poll) ----------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForJob(statusUrl) {
  for (let i = 0; i < 600; i++) {
    const job = await wallet.api(statusUrl);
    if (job.status === "done") return job.report;
    if (job.status === "error") throw new Error(job.error || "analysis failed");
    setAnalyzeLabel(job.status === "queued" ? "Queued…" : "Analyzing…");
    await sleep(i < 10 ? 500 : 1500);
  }
  throw new Error("analysis is taking too long: find it later under My artworks");
}

function setAnalyzeLabel(text) {
  $("analyzeBtn").textContent = text;
  $("pipelineStatus").textContent = text;
}

$("analyzeBtn").addEventListener("click", async () => {
  if (!selectedFile) return;
  const b = $("analyzeBtn");
  b.disabled = true;
  b.dataset.busy = "1";
  try {
    setAnalyzeLabel("Sign in…");
    await ensureSignedIn();
    setAnalyzeLabel("Sign the upload in your wallet…");
    const claim = await wallet.signUpload(selectedFile);
    setAnalyzeLabel("Uploading…");
    animatePipeline();
    const fd = new FormData();
    fd.append("image", selectedFile);
    fd.append("claim_issued_at", claim.claim_issued_at);
    fd.append("claim_signature", claim.claim_signature);
    const job = await wallet.api("/api/analyze", { method: "POST", body: fd });
    const report = await waitForJob(job.status_url);
    finishPipeline(report);
    render(report);
    $("pipelineStatus").textContent = `Analysis finished: ${report.status}`;
    loadStatus();
  } catch (e) {
    clearInterval(animTimer);
    resetPipeline();
    if (e.status === 401) await wallet.refreshSession();
    notify("err", `Analysis failed: ${wallet.explainError(e)}`);
  } finally {
    delete b.dataset.busy;
    updateAnalyzeButton();
  }
});

// ---------------- report rendering ----------------
function fmtValue(name, v) {
  if (name === "ai_detector" || name === "manipulation") return `${(v * 100).toFixed(1)}%`;
  if (name === "provenance") return PROVENANCE_TEXT[v] ?? String(v);
  if (name === "copy_semantic" || name === "copy_member") return v.toFixed(3);
  if (name === "copy_exact") return `${v} bits`;
  if (name === "copy_geometric") return `${v} inliers`;
  if (name === "copy_move") return `${Number(v).toFixed(2)}×`;
  return String(v);
}

function renderChecks(checks) {
  $("checks").replaceChildren(
    ...CHECK_ORDER.filter((k) => checks[k]).map((k) => {
      const c = checks[k];
      const bar = el("div", { className: "riskbar", role: "presentation" }, el("div"));
      bar.firstChild.style.width = `${Math.max(3, c.risk * 100)}%`;
      bar.firstChild.style.background = RISK_COLOR(c.risk, c.advisory);
      const extra = [];
      if (c.nearest) extra.push(`nearest: ${c.nearest}`);
      if (c.shift) extra.push(`shift (${c.shift.join(", ")}), ${c.votes} block pairs`);
      if (c.uploaded_pdq) extra.push(`PDQ ${c.uploaded_pdq.slice(0, 16)}…`);
      if (k === "provenance" && c.reason) extra.push(c.reason);
      if (c.capped_by) extra.push(`capped at REVIEW by trusted ${c.capped_by} evidence`);
      return el(
        "div",
        { className: "check" },
        el(
          "div",
          { className: "check-head" },
          el("h3", { className: "check-name" }, CHECK_TITLE[k]),
          el(
            "span",
            {},
            ...(c.advisory
              ? [
                  el(
                    "span",
                    { className: "badge advisory", title: "affects the score only; cannot block minting on its own" },
                    "advisory",
                  ),
                  " ",
                ]
              : []),
            el("span", { className: `badge ${c.status}` }, c.status),
          ),
        ),
        el("div", { className: "check-label" }, c.label),
        el("div", { className: "check-value" }, fmtValue(k, c.value)),
        bar,
        el(
          "div",
          { className: "riskmeta" },
          el("span", {}, `risk ${(c.risk * 100).toFixed(0)}%`),
          el("span", {}, `review ${fmtValue(k, c.review_at)} · fail ${fmtValue(k, c.fail_at)}`),
        ),
        el("div", { className: "check-extra" }, extra.join(" · ")),
      );
    }),
  );
}

function renderVerdict(r) {
  const st = r.status || r.decision;
  $("verdict").className = `card verdict ${st}`;
  $("decision").textContent = st.replace("_", " ");
  const color =
    st === "REVIEW" ? "var(--review)" : ["FAIL", "REVIEW_REJECTED"].includes(st) ? "var(--fail)" : "var(--pass)";
  $("decision").style.color = color;
  $("scoreVal").textContent = r.score;
  $("ringFg").style.stroke = color;
  $("ringFg").style.strokeDashoffset = 326.7 * (1 - r.score / 100);
  $("reasons").replaceChildren(...r.reasons.map((x) => el("li", {}, x)));
  $("metaLine").textContent = `report ${r.report_id} · ${r.timing_ms} ms · policy ${r.policy_version}`;
}

function renderSimilar(list) {
  if (!list.length) {
    $("similar").replaceChildren(el("p", { className: "hint" }, "No similar protected works found."));
    return;
  }
  $("similar").replaceChildren(
    ...list.map((s) => {
      const label = s.contract
        ? `${CHAIN_NAMES[s.chain_id] || `chain ${s.chain_id}`} · ${short(s.contract)} #${s.token_id}`
        : s.reference;
      return el(
        "div",
        { className: "sim" },
        el("img", {
          src: `/api/reference/${s.image.split("/").map(encodeURIComponent).join("/")}`,
          alt: `Similar work ${label}`,
          title: label,
        }),
        el("b", {}, `copy score ${s.copy_score.toFixed(3)}`),
        `${s.inliers} inliers${s.collection_member ? " · collection" : ""}`,
        el("small", {}, label),
      );
    }),
  );
}

function renderEvidence(ev) {
  $("cmHeat").src = `data:image/png;base64,${ev.copy_move_heatmap}`;
  $("tfFig").hidden = !ev.manipulation_heatmap;
  if (ev.manipulation_heatmap) $("tfImg").src = `data:image/png;base64,${ev.manipulation_heatmap}`;
  $("matchFig").hidden = !ev.copy_matches;
  if (ev.copy_matches) $("matchImg").src = `data:image/png;base64,${ev.copy_matches}`;
}

function renderProvenance(r) {
  const span = (...parts) => el("span", {}, ...parts);
  const onChain = (r.attestations || []).find((a) => a.submitted_tx);
  const rows = [
    ["Report hash", r.report_hash],
    ["Image SHA-256", r.image_sha256],
    ["Policy", `${r.policy_version} · ${short(r.policy_sha256)}`],
    ["Artist", r.artist ? explorer("address", r.artist, r.artist) : "–"],
    ["Signed upload", r.claim ? `file SHA-256 ${short(r.claim.file_sha256)}, signed by the artist` : "–"],
    [
      "Verdict on chain",
      onChain
        ? span("tx ", explorer("tx", onChain.submitted_tx))
        : r.status === "PASS"
          ? "submitted by the artist when minting"
          : "pending",
    ],
  ];
  if (r.review)
    rows.push([
      "Review",
      span(
        r.review.approved ? "approved" : "rejected",
        ...(r.review.tx_hash ? [" · tx ", explorer("tx", r.review.tx_hash)] : [" · on-chain update pending"]),
      ),
    ]);
  if (r.publication) {
    rows.push(["Token URI", link(short(r.publication.token_uri, 22), httpUri(r.publication.token_uri))]);
    rows.push([
      "Files",
      span(
        link("image", httpUri(r.publication.image)),
        " · ",
        link("verification report", httpUri(r.publication.report)),
      ),
    ]);
  }
  if (r.legacy_v1?.mint) rows.push(["v1 token", `#${r.legacy_v1.mint.token_id} (MintGateNFT v1)`]);
  $("prov").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

function renderActions(r) {
  const st = r.status;
  const mintable = st === "PASS" || st === "REVIEW_APPROVED";
  const mine = signedIn() && wallet.getSession().address === r.artist;
  $("reviewBox").hidden = st !== "REVIEW";
  $("mintBox").hidden = st === "REVIEW";
  $("mintBtn").disabled = !mintable || !mine || $("mintBtn").dataset.busy === "1";
  $("mintHint").textContent =
    mintable && !mine
      ? `Only the artist wallet ${short(r.artist, 8)} can mint this artwork: sign in with it.`
      : {
          PASS: "Verified: minting allowed.",
          REVIEW_APPROVED: "Approved by a human reviewer: minting allowed.",
          FAIL: "Blocked by Mint-Gate policy. The failed verdict is recorded on-chain.",
          REVIEW_REJECTED: "Rejected by reviewer. Minting blocked.",
          MINTED: "Already minted (seen on chain by the indexer).",
        }[st] || "";
}

function render(r) {
  current = r;
  $("results").hidden = false;
  renderVerdict(r);
  renderChecks(r.checks);
  renderSimilar(r.similar_nfts || []);
  if (r.evidence) renderEvidence(r.evidence);
  renderProvenance(r);
  renderActions(r);
  $("txOut").replaceChildren();
  $("mintSteps").hidden = true;
  $("rawCall").hidden = true;
  $("verifyOut").replaceChildren();
  $("verdict").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $("verdict").focus({ preventScroll: true });
}

// ---------------- mint from the artist wallet ----------------
const STAGES = ["attest", "switching", "estimating", "wallet", "pending", "confirming"];

function markStage(stage, extra = "", failed = false) {
  const idx = STAGES.indexOf(stage === "confirmed" ? "confirming" : stage);
  for (const li of $("mintSteps").children) {
    const i = STAGES.indexOf(li.dataset.stage);
    li.className = i < idx || stage === "confirmed" ? "done" : i === idx ? (failed ? "failed" : "active") : "";
    li.querySelector(".step-extra")?.remove();
    if (i === idx && extra) li.append(el("span", { className: "step-extra" }, extra));
    if (i === idx) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  }
}

function showRawCall(a, royaltyBps) {
  const args =
    a.call === "submitAndMint"
      ? { verdict: a.verdict, signature: a.signature, uri: a.token_uri, royaltyBps }
      : { imageHash: a.image_hash, uri: a.token_uri, royaltyBps };
  $("rawCallData").textContent = JSON.stringify(
    { network: a.network, chainId: a.chain_id, contract: a.nft, method: a.call, args },
    null,
    2,
  );
  $("rawCall").hidden = false;
}

$("mintBtn").addEventListener("click", async () => {
  if (!current) return;
  const btn = $("mintBtn");
  const royalty = Number($("royalty").value);
  if (!(royalty >= 0 && royalty <= 10)) {
    msg($("txOut"), "err", "Royalty must be between 0 and 10 %.");
    $("royalty").focus();
    return;
  }
  const royaltyBps = Math.round(royalty * 100);
  btn.disabled = true;
  btn.dataset.busy = "1";
  $("mintSteps").hidden = false;
  let stage = "attest";
  markStage(stage);
  msg($("txOut"), "warn", "Preparing the attestation…");
  try {
    await ensureSignedIn();
    const a = await wallet.api(`/api/attest/${current.report_id}`, { method: "POST", json: { network: netName } });
    showRawCall(a, royaltyBps);
    if (!a.ready) {
      markStage("attest", "waiting for the review transaction", true);
      msg($("txOut"), "warn", "The reviewer's approval is still being recorded on chain. Try again in a minute.");
      return;
    }
    const net = wallet.networkByChainId(a.chain_id);
    const result = await wallet.mint(a, royaltyBps, (s) => {
      stage = s.stage;
      const sym = s.symbol || "ETH";
      if (s.stage === "switching") markStage(stage, s.network);
      else if (s.stage === "wallet") {
        const cost = s.maxCost !== null ? ` · at most ${formatEth(s.maxCost)} ${sym}` : "";
        markStage(stage, `${s.gas.toLocaleString()} gas${cost}`);
        msg(
          $("txOut"),
          "warn",
          "Confirm the transaction in your wallet. You pay the gas; Mint-Gate never holds your keys.",
        );
      } else if (s.stage === "pending") {
        markStage(stage, "");
        msg($("txOut"), "warn", "Transaction sent: ", explorerLink(net, "tx", s.hash), ". Waiting to be mined…");
      } else if (s.stage === "confirming") {
        markStage(stage, `${s.confirmations} / ${s.needed}`);
      } else markStage(stage);
    });
    markStage("confirmed", `${result.confirmations} / ${result.needed}`);
    const page = tokenPath(result.network, result.contract, result.tokenId);
    msg(
      $("txOut"),
      "ok",
      `Minted token #${result.tokenId} in block ${result.block} (${result.gasUsed.toLocaleString()} gas, paid by you). `,
      explorerLink(net, "tx", result.hash, "transaction"),
      " · ",
      el("a", { href: page }, "public provenance page"),
    );
    current.status = "MINTED";
    renderActions(current);
  } catch (e) {
    markStage(stage, "", true);
    if (e.status === 401) await wallet.refreshSession();
    msg($("txOut"), "err", wallet.explainError(e));
  } finally {
    delete btn.dataset.busy;
    if (current) renderActions(current);
  }
});

function formatEth(wei) {
  const eth = Number(wei) / 1e18;
  return eth < 0.0001 ? eth.toExponential(2) : eth.toFixed(6);
}

// ---------------- review / verify ----------------
async function resolve(approve) {
  try {
    const json = { approve, note: $("reviewNote").value };
    if ($("reviewerKey").value) json.reviewer_key = $("reviewerKey").value; // else: the reviewer wallet session
    await wallet.api(`/api/review/${current.report_id}`, { method: "POST", json });
    render(await wallet.api(`/api/report/${current.report_id}`));
  } catch (e) {
    msg($("txOut"), "err", e.message);
  }
}
$("approveBtn").addEventListener("click", () => resolve(true));
$("rejectBtn").addEventListener("click", () => resolve(false));

$("verifyBtn").addEventListener("click", async () => {
  if (!current) return;
  msg($("verifyOut"), "warn", "Checking…");
  try {
    const v = await wallet.api(`/api/verify/${current.report_id}`);
    const good = v.report_untampered && v.on_chain_matches;
    msg(
      $("verifyOut"),
      good ? "ok" : "err",
      good ? "✓ Report is untampered and matches the on-chain record. " : "✕ Mismatch between report and chain! ",
      el("br"),
      `on-chain (${v.network}): ${v.on_chain.decision}, score ${v.on_chain.score}`,
      el("br"),
      `issued ${new Date(v.on_chain.issued_at * 1000).toLocaleString()}, signed by `,
      el("code", {}, v.on_chain.signer),
    );
  } catch (e) {
    msg($("verifyOut"), "err", e.message);
  }
});

async function openReport(rid) {
  const rep = await wallet.api(`/api/report/${rid}`);
  openTab("verify");
  $("preview").src = `/api/upload/${rid}.png`;
  $("preview").hidden = false;
  $("dropText").hidden = true;
  finishPipeline(rep);
  render(rep);
}

// ---------------- my artworks ----------------
async function loadMine() {
  const box = $("mine");
  if (!wallet.getSession()) {
    const b = el(
      "button",
      { className: "btn btn-primary" },
      wallet.hasWallet() ? "Sign in with wallet" : "Get a wallet",
    );
    b.addEventListener("click", () => $("walletBtn").click());
    box.replaceChildren(
      el("div", { className: "empty" }, el("p", {}, "Sign in to see the artworks your wallet submitted."), b),
    );
    return;
  }
  box.replaceChildren(el("p", { className: "hint" }, "Loading…"));
  let data;
  try {
    data = await wallet.api("/api/me/artworks");
  } catch (e) {
    if (e.status === 401) {
      await wallet.refreshSession();
      return loadMine();
    }
    msg(box, "err", `Could not load your artworks: ${e.message}`);
    return;
  }
  if (!data.artworks.length) {
    const b = el("button", { className: "btn btn-primary" }, "Upload your first artwork");
    b.addEventListener("click", () => openTab("verify", true));
    box.replaceChildren(el("div", { className: "empty" }, el("p", {}, "No submissions from this wallet yet."), b));
    return;
  }
  box.replaceChildren(...data.artworks.map(artworkRow));
}

function artworkRow(a) {
  const done = a.report_hash !== null;
  const net = (name) => wallet.networkByName(name);
  const tokens = a.tokens.map((t) =>
    el(
      "li",
      {},
      el("a", { href: tokenPath(t.network || String(t.chain_id), t.contract, t.token_id) }, `Token #${t.token_id}`),
      ` on ${net(t.network)?.label || t.chain_id} · ${t.status}`,
      t.owner && t.owner !== a.artist ? ` · now owned by ${short(t.owner, 6)}` : "",
    ),
  );
  const disputes = a.disputes.map((d) =>
    el(
      "li",
      { className: d.status === "open" ? "t-review" : d.status === "upheld" ? "t-fail" : "" },
      `Dispute #${d.dispute_id}: ${d.status} `,
      explorerLink(net(d.network), "tx", d.resolved_tx || d.opened_tx),
    ),
  );
  const open = el("button", { className: "btn btn-ghost btn-small" }, "Open report");
  open.disabled = !done;
  open.addEventListener("click", () => openReport(a.submission_id).catch((e) => notify("err", e.message)));
  return el(
    "article",
    { className: "mrow" },
    done
      ? el("img", { src: `/api/upload/${a.submission_id}.png`, alt: "", loading: "lazy" })
      : el("div", { className: "thumb-empty" }),
    el(
      "div",
      { className: "mrow-body" },
      el("h3", {}, a.filename || a.submission_id),
      el(
        "p",
        { className: "hint" },
        new Date(a.created_at * 1000).toLocaleString(),
        a.score !== null ? ` · score ${a.score}` : "",
        a.signed ? " · signed upload" : "",
        a.error ? ` · ${a.error}` : "",
      ),
      tokens.length || disputes.length ? el("ul", { className: "mrow-list" }, ...tokens, ...disputes) : "",
    ),
    el("span", { className: `badge ${a.status}` }, STATUS_TEXT[a.status] || a.status),
    open,
  );
}

$("mineRefresh").addEventListener("click", loadMine);

// ---------------- review queue ----------------
async function loadQueue() {
  let rows;
  try {
    rows = await wallet.api("/api/reports?status=REVIEW");
  } catch (e) {
    msg($("queue"), "err", `Could not load the queue: ${e.message}`);
    return;
  }
  if (!rows.length) {
    $("queue").replaceChildren(el("p", { className: "empty" }, "Nothing waiting for review."));
    return;
  }
  $("queue").replaceChildren(
    ...rows.map((r) => {
      const open = el("button", { className: "btn btn-ghost" }, "Open");
      open.addEventListener("click", () => openReport(r.report_id).catch((e) => notify("err", e.message)));
      return el(
        "div",
        { className: "qrow" },
        el("img", { src: `/api/upload/${r.report_id}.png`, alt: "" }),
        el("div", {}, r.filename || r.report_id, el("small", {}, r.reasons.join("; "))),
        el("span", { className: "badge REVIEW" }, `score ${r.score}`),
        open,
      );
    }),
  );
}

// ---------------- disputes: reviewer dashboard (Phase 6) ----------------
let roles = null; // {network: {reviewer: bool|null}} for the session wallet
let shownDispute = null; // the dispute in the detail view
const gw = (u) => httpUri(u);
const RESOLVE_STAGES = ["pin", "switching", "estimating", "wallet", "pending", "confirming"];

async function loadRoles() {
  roles = null;
  if (!wallet.getSession()) return;
  try {
    roles = (await wallet.api("/api/auth/roles")).roles;
  } catch {
    roles = null;
  }
}

const reviewerOn = (network) => Boolean(roles?.[network]?.reviewer);

async function loadDisputes() {
  await loadRoles();
  const nets = Object.entries(roles || {})
    .filter(([, r]) => r.reviewer)
    .map(([n]) => n);
  $("reviewerState").textContent = !wallet.getSession()
    ? "Anyone can read disputes and their evidence. Sign in with a wallet that holds REVIEWER_ROLE to decide."
    : nets.length
      ? `Signed in as a reviewer on ${nets.map((n) => wallet.networkByName(n)?.label || n).join(", ")}.`
      : `${short(wallet.getSession().address, 8)} holds no REVIEWER_ROLE: read-only.`;
  const status = $("disputeFilter").value;
  let data;
  try {
    data = await wallet.api(`/api/disputes${status ? `?status=${status}` : ""}`);
  } catch (e) {
    msg($("disputes"), "err", `Could not load disputes: ${e.message}`);
    return;
  }
  if (!data.disputes.length) {
    const text = status === "open" ? "No open disputes." : "No disputes.";
    $("disputes").replaceChildren(el("p", { className: "empty" }, text));
    return;
  }
  $("disputes").replaceChildren(
    ...data.disputes.map((d) => {
      const open = el("button", { className: "btn btn-ghost" }, "Open");
      open.addEventListener("click", () =>
        openDisputeDetail(d.network, d.dispute_id).catch((e) => notify("err", e.message)),
      );
      const label = wallet.networkByName(d.network)?.label || d.network;
      return el(
        "div",
        { className: "qrow" },
        el("div", { className: "thumb-empty" }),
        el(
          "div",
          {},
          `#${d.dispute_id} on ${label}`,
          el("small", {}, `opened ${when(d.opened_at)} by ${short(d.disputant, 8)} · bond ${fmtEth(d.bond)} ETH`),
        ),
        el("span", { className: `badge dispute-${d.status}` }, d.status),
        open,
      );
    }),
  );
}
$("disputeFilter").addEventListener("change", loadDisputes);
$("disputeRefresh").addEventListener("click", loadDisputes);

const kv = (rows) => rows.filter(Boolean).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v ?? "–")]);

function matchCard(m) {
  const indexed = m.indexed_before_upload
    ? "indexed before the disputed upload"
    : m.indexed_at
      ? `indexed ${when(m.indexed_at)}`
      : "";
  return el(
    "div",
    { className: "sim" },
    el("img", { src: `/api/reference/${m.image || m.reference}`, alt: "", loading: "lazy" }),
    el(
      "div",
      {},
      el("b", {}, `copy score ${m.copy_score}`),
      el("small", {}, m.contract ? `${short(m.contract, 8)} #${m.token_id}` : m.reference),
      el("small", {}, indexed),
      m.source_url ? link("source", m.source_url) : "",
    ),
  );
}

function renderBackendEvidence(be, net) {
  if (!be)
    return [
      el("p", { className: "hint" }, "Not computed yet (the analysis worker runs it once the dispute is indexed)."),
    ];
  const out = [];
  const cs = be.copy_search || {};
  if (!cs.available) out.push(el("p", { className: "hint" }, `Copy search: ${cs.why || "unavailable"}`));
  else {
    out.push(el("h4", {}, "Most similar indexed works (the disputed token itself excluded)"));
    out.push(
      cs.matches.length
        ? el("div", { className: "similar" }, ...cs.matches.map(matchCard))
        : el("p", { className: "hint" }, "No similar indexed work."),
    );
  }
  const subs = be.earliest_submissions || [];
  out.push(el("h4", {}, "Earliest signed submissions of these pixels"));
  out.push(
    subs.length
      ? el(
          "ul",
          { className: "mrow-list" },
          ...subs.map((x) =>
            el(
              "li",
              {},
              `${when(x.created_at)} · `,
              explorerLink(net, "address", x.wallet, short(x.wallet, 8)),
              x.signed ? " · signed claim" : " · unsigned",
              ` · ${x.status}`,
            ),
          ),
        )
      : el("p", { className: "hint" }, "None in this Mint-Gate instance."),
  );
  if (be.earliest_is_token_artist === false)
    out.push(el("p", { className: "t-fail" }, "The earliest submission is NOT from the token's artist."));
  if (be.original) {
    const o = be.original;
    const earlier = (o.earliest_submissions || []).length
      ? when(o.earliest_submissions[0].created_at)
      : "never uploaded here";
    out.push(el("h4", {}, "Reporter's original vs the disputed image"));
    out.push(
      el(
        "dl",
        { className: "kv" },
        ...kv([
          ["Identical pixels", o.identical_pixels === undefined ? "–" : o.identical_pixels ? "yes" : "no"],
          ["SSCD cosine", o.sscd_cosine],
          ["PDQ distance (bits of 256)", o.pdq_bits],
          ["Original submitted here", earlier],
          o.error ? ["Error", o.error] : null,
        ]),
      ),
    );
  }
  const rs = be.reverse_search || {};
  out.push(el("h4", {}, "Reverse image search"));
  out.push(
    el(
      "p",
      { className: "hint" },
      rs.configured === false
        ? "Not configured on this server (REVERSE_SEARCH)."
        : rs.error
          ? `${rs.provider}: ${rs.error}`
          : `${rs.provider}: ${rs.full_matches} full matches${rs.earliest ? `, earliest ${rs.earliest.crawl_date}` : ""}`,
    ),
  );
  for (const pg of rs.pages || []) out.push(el("div", {}, link(pg.title || pg.url, pg.url)));
  return out;
}

async function openDisputeDetail(network, id) {
  const d = await wallet.api(`/api/disputes/${network}/${id}`);
  shownDispute = d;
  const net = wallet.networkByName(network);
  $("disputeDetail").hidden = false;
  $("ddTitle").textContent = `Dispute #${d.dispute_id} on ${net?.label || network}: ${d.status}`;
  const r = d.report;
  $("ddImage").hidden = !r;
  if (r) $("ddImage").src = r.image_url;
  $("ddTokenCap").replaceChildren(
    d.token
      ? el(
          "a",
          { href: tokenPath(network, d.token.contract, d.token.token_id) },
          `Token #${d.token.token_id}: provenance page`,
        )
      : "Token not indexed",
  );
  $("ddToken").replaceChildren(
    ...kv([
      ["Artist", r?.artist ? explorerLink(net, "address", r.artist, short(r.artist, 8)) : "–"],
      ["Verdict at mint", r ? `${r.decision}, score ${r.score} (policy ${r.policy_version})` : "–"],
      ["Uploaded", r ? when(r.created_at) : "–"],
      ["Token status", d.token?.status],
      ["Report", r ? link("full verification report", `/?report=${r.report_id}`) : "–"],
    ]),
  );
  const ev = d.reporter_evidence;
  $("ddOrigFig").hidden = !d.original_url;
  if (d.original_url) $("ddOriginal").src = d.original_url;
  $("ddReporter").replaceChildren(
    ...kv([
      ["Reporter", explorerLink(net, "address", d.disputant, short(d.disputant, 8))],
      ["Opened", `${when(d.opened_at)} · bond ${fmtEth(d.bond)} ETH`],
      [
        "Statement",
        ev ? el("p", { className: "prewrap" }, ev.statement) : "(evidence not submitted through Mint-Gate)",
      ],
      ev?.urls?.length ? ["Links", el("ul", {}, ...ev.urls.map((u) => el("li", {}, link(u, gw(u)))))] : null,
      ev?.first_published ? ["First published", ev.first_published] : null,
      ev?.prior_claims?.length
        ? ["Earlier claims", el("ul", {}, ...ev.prior_claims.map((c) => el("li", { className: "mono" }, c)))]
        : null,
      ["Pinned evidence", link(short(d.evidence_uri, 22), d.evidence_http)],
    ]),
  );
  $("ddBackend").replaceChildren(...renderBackendEvidence(d.backend_evidence, net));
  if (d.backend_evidence_uri)
    $("ddBackend").append(
      el("p", { className: "hint" }, link("pinned copy of this evidence", gw(d.backend_evidence_uri))),
    );

  await loadRoles();
  const canDecide = d.status === "open" && reviewerOn(network) && Boolean(d.resolver);
  $("ddRequeue").hidden = !(reviewerOn(network) && !d.backend_evidence);
  $("decisionForm").hidden = !canDecide;
  $("ddOut").replaceChildren();
  let body;
  if (d.status !== "open")
    body = el(
      "p",
      {},
      `${d.status === "upheld" ? "Upheld" : "Rejected"} ${when(d.resolved_at)} by `,
      explorerLink(net, "address", d.reviewer, short(d.reviewer, 8)),
      " · ",
      d.reason_http ? link("written reason", d.reason_http) : "no reason recorded on chain",
      " · ",
      explorerLink(net, "tx", d.resolved_tx),
    );
  else if (canDecide)
    body = el(
      "p",
      { className: "hint" },
      "Read both sides, then decide. Your wallet signs the transaction: the decision is attributable to you.",
    );
  else
    body = el(
      "p",
      { className: "hint" },
      d.resolver
        ? "Only wallets with REVIEWER_ROLE on this chain can decide."
        : "No DisputeResolver is deployed on this chain yet.",
    );
  $("ddDecideBody").replaceChildren(body);
  $("disputeDetail").scrollIntoView({
    behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  });
}

$("ddRequeue").addEventListener("click", async () => {
  const d = shownDispute;
  try {
    await wallet.api(`/api/disputes/${d.network}/${d.dispute_id}/backend-evidence`, { method: "POST" });
    msg($("ddOut"), "ok", "Queued. Reload the dispute in a minute.");
  } catch (e) {
    msg($("ddOut"), "err", e.message);
  }
});

$("decisionForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = shownDispute;
  const outcome = new FormData($("decisionForm")).get("outcome");
  if (!outcome) {
    msg($("ddOut"), "err", "Choose uphold or reject.");
    return;
  }
  const steps = $("ddSteps");
  const net = wallet.networkByName(d.network);
  let stage = "pin";
  const step = (st, extra = "", failed = false) => markSteps(steps, RESOLVE_STAGES, st, extra, failed);
  steps.hidden = false;
  $("ddResolveBtn").disabled = true;
  try {
    step("pin");
    const info = await wallet.api(`/api/disputes/${d.network}/${d.dispute_id}/reason`, {
      method: "POST",
      json: { upheld: outcome === "upheld", reason: $("ddReason").value },
    });
    step("pin", link("pinned", gw(info.reason_uri)));
    const out = await wallet.resolveDispute(info, (s) => {
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
      $("ddOut"),
      "ok",
      `Dispute ${outcome}. `,
      explorerLink(net, "tx", out.hash),
      " The indexer updates the token in a few blocks.",
    );
    setTimeout(() => openDisputeDetail(d.network, d.dispute_id).catch(() => {}), 6000);
  } catch (err) {
    step(stage, "", true);
    msg($("ddOut"), "err", wallet.explainError(err));
  } finally {
    $("ddResolveBtn").disabled = false;
  }
});

// ---------------- provenance lookup ----------------
$("lookupForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const n = wallet.networkByName($("lookupNet").value);
  const id = parseInt($("tokenId").value, 10);
  const contract = $("lookupContract").value.trim() || n?.nft;
  if (!n || !(id > 0) || !/^0x[0-9a-fA-F]{40}$/.test(contract || "")) return;
  location.href = tokenPath(n.network, contract, id);
});

// Deep links: /?report=<id> opens a stored report (e.g. shared with a reviewer);
// /?view=disputes&dispute=<network>:<id> opens a dispute (Discord alerts link there).
async function openFromUrl() {
  const q = new URLSearchParams(location.search);
  if (q.get("view") === "disputes") {
    openTab("disputes");
    const m = /^([A-Za-z0-9]+):(\d+)$/.exec(q.get("dispute") || "");
    if (m) openDisputeDetail(m[1], m[2]).catch((e) => notify("err", e.message));
    return;
  }
  const rid = q.get("report");
  if (!rid || !/^[0-9a-f]{32}$/.test(rid)) return;
  try {
    await openReport(rid);
  } catch (e) {
    notify("err", `Could not open report: ${e.message}`);
  }
}

await initNetworks();
renderIdentity();
wallet.restore().catch(() => {});
loadStatus();
loadSamples();
openFromUrl();
setInterval(loadStatus, 15000);
