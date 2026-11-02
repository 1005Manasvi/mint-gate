// Mint-Gate frontend. All server data is rendered with textContent / element
// properties (never innerHTML), so report contents cannot inject markup.
"use strict";

const $ = (id) => document.getElementById(id);
const CHECK_ORDER = ["resnet", "ai_detector", "copy_move", "phash", "clip", "orb"];
const CHECK_TITLE = {
  resnet: "ResNet-18",
  ai_detector: "GAN / AI detector",
  copy_move: "Copy-move",
  phash: "Perceptual hash",
  clip: "CLIP similarity",
  orb: "ORB matching",
};
const RISK_COLOR = (r, advisory) =>
  advisory && r >= 0.5 ? "#6366f1" : r >= 1 ? "var(--fail)" : r >= 0.5 ? "var(--review)" : "var(--pass)";

let selectedFile = null;
let current = null; // current report
let chainInfo = {}; // from /api/status
let ipfsGateway = "https://ipfs.io/ipfs/";

const short = (h, n = 10) => (h ? `${h.slice(0, n)}…${h.slice(-6)}` : "–");

function link(text, href) {
  return el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
}

function httpUri(uri) {
  return uri && uri.startsWith("ipfs://") ? ipfsGateway + uri.slice(7) : uri;
}

/** Etherscan link when the chain has an explorer (Sepolia), plain text otherwise. */
function explorer(kind, value, text = short(value)) {
  const base = chainInfo.explorer;
  if (!base || !value) return el("span", { className: "mono" }, text);
  const path = { tx: `tx/${value}`, address: `address/${value}`, token: `nft/${chainInfo.contract}/${value}` }[kind];
  return link(text, `${base}/${path}`);
}

function el(tag, props = {}, ...children) {
  const n = Object.assign(document.createElement(tag), props);
  for (const c of children) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data;
  try {
    data = await res.json();
  } catch {
    data = { error: `HTTP ${res.status}` };
  }
  if (!res.ok || data.success === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function msg(target, kind, ...parts) {
  target.replaceChildren(el("div", { className: kind }, ...parts));
}

// ---------------- wallet (MetaMask via window.ethereum, no third-party script) ----------------
async function connectWallet() {
  if (!window.ethereum) {
    alert("No wallet found. Install MetaMask (metamask.io) or paste an address manually.");
    return;
  }
  try {
    const [account] = await window.ethereum.request({ method: "eth_requestAccounts" });
    if (chainInfo.chain_id) {
      const want = "0x" + chainInfo.chain_id.toString(16);
      const have = await window.ethereum.request({ method: "eth_chainId" });
      if (have !== want) {
        await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: want }] });
      }
    }
    setAccount(account);
  } catch (e) {
    alert(`Wallet connection failed: ${e.message || e}`);
  }
}

function setAccount(account) {
  const btn = $("walletBtn");
  if (!account) {
    btn.textContent = "Connect wallet";
    btn.classList.remove("connected");
    return;
  }
  btn.textContent = short(account, 6);
  btn.title = account;
  btn.classList.add("connected");
  $("recipient").value = account;
}

$("walletBtn").addEventListener("click", connectWallet);
if (window.ethereum) {
  window.ethereum.on?.("accountsChanged", (accs) => setAccount(accs[0]));
  window.ethereum
    .request({ method: "eth_accounts" })
    .then((accs) => setAccount(accs[0]))
    .catch(() => {});
}

// ---------------- tabs ----------------
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${t.dataset.view}`));
    if (t.dataset.view === "queue") loadQueue();
    if (t.dataset.view === "about") loadStatus(true);
  }),
);

// ---------------- status ----------------
async function loadStatus(dump = false) {
  try {
    const s = await api("/api/status");
    const c = s.chain;
    if (s.ipfs_gateway) ipfsGateway = s.ipfs_gateway;
    const pill = $("chainPill");
    if (c.connected) {
      chainInfo = c;
      pill.className = "pill pill-ok";
      pill.textContent = `${c.network} · block ${c.block} · ${c.total_minted} minted`;
      pill.title = `MintGateNFT ${c.contract}`;
      $("footContract").replaceChildren("contract ", explorer("address", c.contract));
    } else {
      pill.className = "pill pill-bad";
      pill.textContent = "chain offline";
      pill.title = c.error || "";
    }
    if (dump) $("statusDump").textContent = JSON.stringify(s, null, 2);
  } catch (e) {
    $("chainPill").textContent = "backend offline";
  }
}

// ---------------- upload ----------------
function setFile(file) {
  if (!file) return;
  selectedFile = file;
  $("preview").src = URL.createObjectURL(file);
  $("preview").hidden = false;
  $("dropText").hidden = true;
  $("analyzeBtn").disabled = false;
}

$("fileInput").addEventListener("change", (e) => setFile(e.target.files[0]));
const dz = $("dropzone");
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
  $("analyzeBtn").disabled = true;
  $("results").hidden = true;
  resetPipeline();
});

async function loadSamples() {
  try {
    const names = await (await fetch("/api/samples")).json();
    $("samples").replaceChildren(
      ...names.map((n) => {
        const b = el(
          "button",
          { className: "sample", title: n },
          el("img", { src: `/api/samples/${encodeURIComponent(n)}`, alt: n }),
          el("span", {}, n.replace(/\.(png|jpg)$/i, "").replace(/_/g, " ")),
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
    else if (k === "chain") li.className = report.chain?.anchored ? "s-DONE" : "s-REVIEW";
    else li.className = "s-DONE";
  }
}

// ---------------- analyze ----------------
$("analyzeBtn").addEventListener("click", async () => {
  if (!selectedFile) return;
  $("analyzeBtn").disabled = true;
  $("analyzeBtn").textContent = "Analyzing…";
  animatePipeline();
  try {
    const fd = new FormData();
    fd.append("image", selectedFile);
    const { report } = await api("/api/analyze", { method: "POST", body: fd });
    finishPipeline(report);
    render(report);
    loadStatus();
  } catch (e) {
    clearInterval(animTimer);
    resetPipeline();
    alert(`Analysis failed: ${e.message}`);
  } finally {
    $("analyzeBtn").disabled = false;
    $("analyzeBtn").textContent = "Analyze artwork";
  }
});

function fmtValue(name, v) {
  if (name === "resnet" || name === "ai_detector") return `${(v * 100).toFixed(1)}%`;
  if (name === "clip") return v.toFixed(3);
  if (name === "copy_move") return `${Number(v).toFixed(2)}×`;
  return String(v);
}

function renderChecks(checks) {
  $("checks").replaceChildren(
    ...CHECK_ORDER.map((k) => {
      const c = checks[k];
      const bar = el("div", { className: "riskbar" }, el("div"));
      bar.firstChild.style.width = `${Math.max(3, c.risk * 100)}%`;
      bar.firstChild.style.background = RISK_COLOR(c.risk, c.advisory);
      const extra = [];
      if (c.nearest) extra.push(`nearest: ${c.nearest}`);
      if (c.shift) extra.push(`shift (${c.shift.join(", ")}), ${c.votes} block pairs`);
      if (c.uploaded_phash) extra.push(`pHash ${c.uploaded_phash}`);
      return el(
        "div",
        { className: "check" },
        el(
          "div",
          { className: "check-head" },
          el("span", { className: "check-name" }, CHECK_TITLE[k]),
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
  const status = r.status || r.decision;
  $("verdict").className = `card verdict ${status}`;
  $("decision").textContent = status.replace("_", " ");
  const color =
    status === "REVIEW"
      ? "var(--review)"
      : ["FAIL", "REVIEW_REJECTED"].includes(status)
        ? "var(--fail)"
        : "var(--pass)";
  $("decision").style.color = color;
  $("scoreVal").textContent = r.score;
  $("ringFg").style.stroke = color;
  $("ringFg").style.strokeDashoffset = 326.7 * (1 - r.score / 100);
  $("reasons").replaceChildren(...r.reasons.map((x) => el("li", {}, x)));
  $("metaLine").textContent = `report ${r.report_id} · ${r.timing_ms} ms · policy ${r.policy_version}`;
}

function renderSimilar(list) {
  $("similar").replaceChildren(
    ...list.map((s) =>
      el(
        "div",
        { className: "sim" },
        el("img", {
          src: `/api/reference/${s.filename.split("/").map(encodeURIComponent).join("/")}`,
          alt: "",
          title: s.filename,
        }),
        el("b", {}, `CLIP ${s.clip_similarity.toFixed(3)}`),
        `pHash ${s.phash_distance} · ORB ${s.orb_inliers}`,
      ),
    ),
  );
}

function renderEvidence(ev) {
  $("cmHeat").src = `data:image/png;base64,${ev.copy_move_heatmap}`;
  $("orbFig").hidden = !ev.orb_matches;
  if (ev.orb_matches) $("orbImg").src = `data:image/png;base64,${ev.orb_matches}`;
}

function renderProvenance(r) {
  const span = (...parts) => el("span", {}, ...parts);
  const rows = [
    ["Report hash", r.report_hash],
    ["Image SHA-256", r.image_sha256],
    ["Policy", `${r.policy_version} · ${short(r.policy_sha256)}`],
    [
      "Anchored",
      r.chain?.anchored
        ? span("tx ", explorer("tx", r.chain.tx_hash), ` · block ${r.chain.block}`)
        : `no (${r.chain?.error || "pending"})`,
    ],
  ];
  if (r.review)
    rows.push([
      "Review",
      span(r.review.approved ? "approved" : "rejected", " · tx ", explorer("tx", r.review.tx_hash)),
    ]);
  if (r.mint) {
    rows.push([
      "Token",
      span(
        explorer("token", String(r.mint.token_id), `#${r.mint.token_id}`),
        " → ",
        explorer("address", r.mint.recipient),
        " · tx ",
        explorer("tx", r.mint.tx_hash),
      ),
    ]);
    rows.push(["Token URI", link(short(r.mint.token_uri, 22), httpUri(r.mint.token_uri))]);
    if (r.mint.links)
      rows.push([
        "IPFS",
        span(
          link("image", httpUri(r.mint.links.image)),
          " · ",
          link("verification report", httpUri(r.mint.links.report)),
        ),
      ]);
  }
  $("prov").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

function renderActions(r) {
  const status = r.status;
  const mintable = status === "PASS" || status === "REVIEW_APPROVED";
  $("reviewBox").hidden = status !== "REVIEW";
  $("mintBox").hidden = status === "REVIEW";
  $("mintBtn").disabled = !mintable;
  $("mintHint").textContent =
    {
      PASS: "Verified — minting allowed.",
      REVIEW_APPROVED: "Approved by a human reviewer — minting allowed.",
      FAIL: "Blocked by Mint-Gate policy. The failed verdict is recorded on-chain.",
      REVIEW_REJECTED: "Rejected by reviewer. Minting blocked.",
      MINTED: "Already minted.",
    }[status] || "";
  $("txOut").replaceChildren();
  if (r.mint) {
    msg(
      $("txOut"),
      "ok",
      "Minted token ",
      explorer("token", String(r.mint.token_id), `#${r.mint.token_id}`),
      " · tx ",
      explorer("tx", r.mint.tx_hash),
      ` · block ${r.mint.block}`,
    );
  }
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
  $("verifyOut").replaceChildren();
  $("verdict").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------- mint / review / verify ----------------
$("mintBtn").addEventListener("click", async () => {
  if (!current) return;
  $("mintBtn").disabled = true;
  msg($("txOut"), "warn", "Sending mint transaction…");
  try {
    await api(`/api/mint/${current.report_id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipient: $("recipient").value.trim() }),
    });
    render(await api(`/api/report/${current.report_id}`));
    loadStatus();
  } catch (e) {
    msg($("txOut"), "err", e.message);
    $("mintBtn").disabled = false;
  }
});

async function resolve(approve) {
  try {
    await api(`/api/review/${current.report_id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve, reviewer_key: $("reviewerKey").value, note: $("reviewNote").value }),
    });
    render(await api(`/api/report/${current.report_id}`));
  } catch (e) {
    msg($("txOut"), "err", e.message);
  }
}
$("approveBtn").addEventListener("click", () => resolve(true));
$("rejectBtn").addEventListener("click", () => resolve(false));

$("verifyBtn").addEventListener("click", async () => {
  if (!current) return;
  try {
    const v = await api(`/api/verify/${current.report_id}`);
    const good = v.report_untampered && v.on_chain_matches;
    msg(
      $("verifyOut"),
      good ? "ok" : "err",
      good ? "✓ Report is untampered and matches the on-chain record. " : "✕ Mismatch between report and chain! ",
      el("br"),
      `on-chain: ${v.on_chain.decision}, score ${v.on_chain.score}, policy ${v.on_chain.policy_version}`,
      el("br"),
      `recorded at ${new Date(v.on_chain.timestamp * 1000).toLocaleString()} by `,
      el("code", {}, v.on_chain.verifier),
    );
  } catch (e) {
    msg($("verifyOut"), "err", e.message);
  }
});

// ---------------- review queue ----------------
async function loadQueue() {
  const rows = await (await fetch("/api/reports?status=REVIEW")).json();
  if (!rows.length) {
    $("queue").replaceChildren(el("p", { className: "hint" }, "Nothing waiting for review."));
    return;
  }
  $("queue").replaceChildren(
    ...rows.map((r) => {
      const open = el("button", { className: "btn btn-ghost" }, "Open");
      open.addEventListener("click", async () => {
        document.querySelector('.tab[data-view="verify"]').click();
        const rep = await api(`/api/report/${r.report_id}`);
        $("preview").src = `/api/upload/${r.report_id}.png`;
        $("preview").hidden = false;
        $("dropText").hidden = true;
        finishPipeline(rep);
        render(rep);
      });
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

// ---------------- token lookup ----------------
$("lookupBtn").addEventListener("click", async () => {
  const id = parseInt($("tokenId").value, 10);
  if (!(id > 0)) return;
  try {
    const t = await api(`/api/token/${id}`);
    const v = t.verification;
    $("lookupOut").replaceChildren(
      el(
        "dl",
        { className: "kv", style: "margin-top:16px" },
        ...[
          ["Token", explorer("token", String(t.token_id), `#${t.token_id}`)],
          ["Owner", explorer("address", t.owner, t.owner)],
          ["Token URI", link(t.token_uri, t.token_uri_http)],
          ["Report hash", t.report_hash],
          ["Image SHA-256", v.image_sha256],
          ["Decision", v.decision],
          ["Score", v.score],
          ["Policy", v.policy_version],
          ["Verified at", new Date(v.timestamp * 1000).toLocaleString()],
          ["Verifier", v.verifier],
        ].flatMap(([k, val]) => [el("dt", {}, k), el("dd", {}, val)]),
      ),
    );
  } catch (e) {
    msg($("lookupOut"), "err", e.message);
  }
});

// Deep link: /?report=<id> opens a stored report (e.g. shared with a reviewer).
async function openFromUrl() {
  const rid = new URLSearchParams(location.search).get("report");
  if (!rid || !/^[0-9a-f]{32}$/.test(rid)) return;
  try {
    const rep = await api(`/api/report/${rid}`);
    $("preview").src = `/api/upload/${rid}.png`;
    $("preview").hidden = false;
    $("dropText").hidden = true;
    finishPipeline(rep);
    render(rep);
  } catch (e) {
    alert(`Could not open report: ${e.message}`);
  }
}

loadStatus();
loadSamples();
openFromUrl();
setInterval(loadStatus, 15000);
