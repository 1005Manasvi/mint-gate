// Mint-Gate main page: sign in with a wallet, upload (signed), see the verdict, mint from the wallet.
"use strict";

import { el, explorerLink, link, msg, short, tokenPath } from "./ui.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
const CHECK_ORDER = ["resnet", "ai_detector", "copy_move", "copy_exact", "copy_semantic", "copy_geometric"];
const CHECK_TITLE = {
  resnet: "ResNet-18",
  ai_detector: "GAN / AI detector",
  copy_move: "Copy-move",
  copy_exact: "Exact copy (PDQ)",
  copy_semantic: "Edited copy (SSCD)",
  copy_geometric: "Partial copy (LightGlue)",
};
const CHAIN_NAMES = { 1: "Ethereum", 8453: "Base" };
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
  if (name === "resnet" || name === "ai_detector") return `${(v * 100).toFixed(1)}%`;
  if (name === "copy_semantic") return v.toFixed(3);
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
    await wallet.api(`/api/review/${current.report_id}`, {
      method: "POST",
      json: { approve, reviewer_key: $("reviewerKey").value, note: $("reviewNote").value },
    });
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

// ---------------- provenance lookup ----------------
$("lookupForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const n = wallet.networkByName($("lookupNet").value);
  const id = parseInt($("tokenId").value, 10);
  const contract = $("lookupContract").value.trim() || n?.nft;
  if (!n || !(id > 0) || !/^0x[0-9a-fA-F]{40}$/.test(contract || "")) return;
  location.href = tokenPath(n.network, contract, id);
});

// Deep link: /?report=<id> opens a stored report (e.g. shared with a reviewer).
async function openFromUrl() {
  const rid = new URLSearchParams(location.search).get("report");
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
