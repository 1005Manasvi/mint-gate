// Public explorer: per-chain statistics, the on-chain policy history (verified in the browser) and
// governance (Safe owners sign pending transactions with their wallet; anyone executes).
"use strict";

import { BrowserProvider, Contract } from "../vendor/ethers-6.17.0.min.js";
import { sha256Hex } from "./canonical.js";
import { gatewayUrl, readProvider } from "./provenance.js";
import { describeEvent, el, explorerLink, formatEth, link, msg, short, when } from "./ui.js";
import * as wallet from "./wallet.js";

const $ = (id) => document.getElementById(id);
let gateway = "https://ipfs.io/ipfs/";
let nets = {}; // name -> /api/networks entry (with ABIs)
const gw = (u) => gatewayUrl(u, gateway);
const DECISION_CLASS = { PASS: "t-pass", REVIEW: "t-review", FAIL: "t-fail" };

function stat(label, value, cls = "") {
  return el(
    "div",
    { className: "stat" },
    el("span", { className: `stat-num ${cls}` }, String(value)),
    el("span", { className: "stat-label" }, label),
  );
}

async function loadStats() {
  const data = await wallet.api("/api/explorer/stats");
  const cards = Object.entries(data.networks).map(([name, s]) => {
    const net = nets[name] || s;
    return el(
      "article",
      { className: "card" },
      el("h3", {}, s.label, el("small", { className: "muted" }, ` · indexed to block ${s.indexed_block ?? "–"}`)),
      el("h4", {}, "Verdicts recorded"),
      el(
        "div",
        { className: "stats" },
        ...["PASS", "REVIEW", "FAIL"].map((d) => stat(d, s.verdicts[d], DECISION_CLASS[d])),
        stat("reviews approved", s.reviews.approved),
        stat("reviews rejected", s.reviews.rejected),
        stat("voided", s.voided),
      ),
      el("h4", {}, "Tokens and disputes"),
      el(
        "div",
        { className: "stats" },
        stat("mints", s.mints),
        stat("flagged now", s.tokens.flagged, "t-review"),
        stat("revoked", s.revocations, "t-fail"),
        stat("disputes open", s.disputes.open, "t-review"),
        stat("upheld", s.disputes.upheld, "t-fail"),
        stat("rejected", s.disputes.rejected),
        stat("bonds locked (ETH)", formatEth(s.bonds_locked_wei)),
      ),
      el("h4", {}, "Recent activity"),
      s.recent.length
        ? el(
            "ol",
            { className: "timeline" },
            ...s.recent.map((e) =>
              el(
                "li",
                { className: "tl" },
                el("time", { className: "tl-time" }, when(e.time) || `block ${e.block}`),
                el(
                  "span",
                  { className: "tl-text" },
                  ...describeEvent(e, net, gw),
                  " · ",
                  explorerLink(net, "tx", e.tx),
                ),
              ),
            ),
          )
        : el("p", { className: "hint" }, "No events yet."),
    );
  });
  $("statsBox").replaceChildren(...cards);
}

async function verifyPolicy(p, out) {
  out.replaceChildren("downloading…");
  try {
    const res = await fetch(p.uri_http);
    const bytes = new Uint8Array(await res.arrayBuffer());
    const got = await sha256Hex(bytes);
    out.replaceChildren(
      got === p.sha256
        ? el("span", { className: "t-pass" }, "✓ file matches the on-chain hash")
        : el("span", { className: "t-fail" }, `✕ file hashes to ${short(got)}`),
    );
  } catch (e) {
    out.replaceChildren(el("span", { className: "t-review" }, `could not download (${e.message})`));
  }
}

const TIMELOCK_ABI = [
  "function execute(address target, uint256 value, bytes payload, bytes32 predecessor, bytes32 salt) payable",
];

async function executeOp(net, timelock, op, out) {
  try {
    await wallet.connect();
    await wallet.ensureChain(net);
    const signer = await new BrowserProvider(window.ethereum, "any").getSigner();
    const tl = new Contract(timelock, TIMELOCK_ABI, signer);
    const tx = await tl.execute(op.target, BigInt(op.value || 0), op.data, op.predecessor, op.salt);
    msg(out, "warn", "Pending… ", explorerLink(net, "tx", tx.hash));
    await tx.wait(1);
    msg(out, "ok", "Executed. ", explorerLink(net, "tx", tx.hash));
  } catch (e) {
    msg(out, "err", wallet.explainError(e));
  }
}

async function loadPolicies() {
  const data = await wallet.api("/api/explorer/policies");
  const blocks = Object.entries(data.networks).map(([name, p]) => {
    const net = nets[name] || {};
    if (!p.policy_registry)
      return el(
        "article",
        { className: "card" },
        el("h3", {}, net.label || name),
        el("p", { className: "hint" }, "No PolicyRegistry deployed on this chain yet."),
      );
    const rows = p.history
      .slice()
      .reverse()
      .map((h) => {
        const out = el("span", {});
        const b = el("button", { className: "btn btn-ghost btn-small" }, "Verify");
        b.addEventListener("click", () => verifyPolicy(h, out));
        return el(
          "tr",
          {},
          el("td", {}, String(h.index)),
          el("td", {}, h.version),
          el("td", { className: "mono" }, short(h.sha256, 12)),
          el("td", {}, when(h.time)),
          el("td", {}, link("file", h.uri_http), " · ", explorerLink(net, "tx", h.tx)),
          el("td", {}, b, " ", out),
        );
      });
    const ops = p.operations.map((op) => {
      const out = el("div", { className: "txout" });
      const ready = op.status === "pending" && Date.now() / 1000 >= op.ready_at;
      const d = op.decoded;
      const what =
        d?.function === "setPolicy"
          ? `setPolicy ${d.version} (${short(d.sha256, 10)}) · ${d.uri}`
          : `call to ${short(op.target, 8)}`;
      const exec = el("button", { className: "btn btn-primary btn-small" }, "Execute now");
      exec.hidden = !ready;
      exec.addEventListener("click", () => executeOp(net, p.timelock, op, out));
      return el(
        "li",
        {},
        el("b", {}, op.status),
        ` · ${what} · scheduled ${when(op.scheduled_at)}`,
        op.status === "pending" ? ` · executable after ${when(op.ready_at)}` : "",
        " ",
        exec,
        out,
      );
    });
    const sp = p.server_policy;
    return el(
      "article",
      { className: "card" },
      el("h3", {}, net.label || name),
      el(
        "p",
        { className: "hint" },
        "PolicyRegistry ",
        explorerLink(net, "address", p.policy_registry),
        " · owned by timelock ",
        explorerLink(net, "address", p.timelock),
      ),
      el(
        "p",
        { className: sp.announced ? "t-pass" : "t-fail" },
        sp.announced
          ? `✓ This server signs verdicts with policy ${sp.version}, which is announced on chain.`
          : `✕ This server's policy ${sp.version} (${short(sp.sha256, 10)}) is NOT announced on this chain.`,
      ),
      el(
        "div",
        { className: "table-wrap" },
        el(
          "table",
          { className: "table" },
          el(
            "thead",
            {},
            el("tr", {}, ...["#", "Version", "SHA-256", "Announced", "Links", "Check"].map((h) => el("th", {}, h))),
          ),
          el("tbody", {}, ...rows),
        ),
      ),
      el("h4", {}, "Timelocked changes"),
      ops.length ? el("ul", { className: "mrow-list" }, ...ops) : el("p", { className: "hint" }, "None scheduled."),
    );
  });
  $("polBox").replaceChildren(...blocks);
}

async function signProposal(net, p, out) {
  try {
    const account = await wallet.connect();
    const owners = (p.owners || []).map((o) => o.toLowerCase());
    if (owners.length && !owners.includes(account.toLowerCase()))
      throw new wallet.WalletError(
        `${short(account, 8)} is not an owner of this Safe: switch accounts in your wallet.`,
      );
    const { signature } = await wallet.signSafeTx(net, p.typed_data);
    const res = await wallet.api(`/api/governance/${net.network}/${p.safe_tx_hash}/signatures`, {
      method: "POST",
      json: { signature },
    });
    msg(out, "ok", `Signature stored (${res.proposal.signers.length} of ${res.proposal.threshold}).`);
    setTimeout(loadGovernance, 800);
  } catch (e) {
    msg(out, "err", wallet.explainError(e));
  }
}

async function execProposal(net, p, out) {
  try {
    await wallet.connect();
    const r = await wallet.execSafeTx(net, p, (s) => msg(out, "warn", `${s.stage}…`));
    msg(out, "ok", "Executed. ", explorerLink(net, "tx", r.hash), " The indexer marks it in a few blocks.");
  } catch (e) {
    msg(out, "err", wallet.explainError(e));
  }
}

async function safeNonce(net, safe) {
  try {
    const { provider } = await readProvider(net);
    return await new Contract(safe, ["function nonce() view returns (uint256)"], provider).nonce();
  } catch {
    return null;
  }
}

async function loadGovernance() {
  const data = await wallet.api("/api/explorer/governance");
  const blocks = [];
  for (const [name, g] of Object.entries(data.networks)) {
    const net = nets[name] || g;
    const gov = g.governance;
    if (!gov) {
      blocks.push(
        el(
          "article",
          { className: "card" },
          el("h3", {}, g.label),
          el("p", { className: "hint" }, "Governance contracts not deployed on this chain yet."),
        ),
      );
      continue;
    }
    const nonce = await safeNonce(net, gov.safe);
    const holders = Object.entries(g.role_holders).map(([role, who]) =>
      el(
        "li",
        {},
        el("b", {}, role),
        ": ",
        ...who.flatMap((a, i) => [
          i ? ", " : "",
          explorerLink(net, "address", a, a === gov.safe ? "Safe" : short(a, 8)),
        ]),
      ),
    );
    const props = g.proposals
      .slice()
      .reverse()
      .map((p) => {
        const out = el("div", { className: "txout" });
        const sign = el("button", { className: "btn btn-ghost btn-small" }, "Sign as owner");
        const exec = el("button", { className: "btn btn-primary btn-small" }, "Execute");
        const pending = p.status === "pending";
        sign.hidden = !pending;
        exec.hidden = !(pending && p.ready && (nonce === null || BigInt(p.tx.nonce) === nonce));
        sign.addEventListener("click", () => signProposal(net, { ...p, owners: gov.owners }, out));
        exec.addEventListener("click", () => execProposal(net, p, out));
        return el(
          "li",
          { className: "proposal" },
          el(
            "div",
            {},
            el("b", {}, `#${p.tx.nonce} ${p.kind}`),
            ` · ${p.status}`,
            p.executed_tx ? [" · ", explorerLink(net, "tx", p.executed_tx)] : "",
          ),
          el("p", { className: "hint" }, p.description),
          el(
            "p",
            { className: "hint" },
            `Signatures ${p.signers.length} / ${p.threshold}: `,
            ...p.signers.flatMap((s, i) => [i ? ", " : "", short(s, 8)]),
          ),
          el("p", { className: "mono small" }, `safeTxHash ${p.safe_tx_hash}`),
          el("div", { className: "row row-tight" }, sign, exec),
          out,
        );
      });
    blocks.push(
      el(
        "article",
        { className: "card" },
        el("h3", {}, g.label),
        el(
          "dl",
          { className: "kv" },
          el("dt", {}, "Safe"),
          el(
            "dd",
            {},
            explorerLink(net, "address", gov.safe, gov.safe),
            ` · ${gov.threshold} of ${gov.owners.length}`,
            nonce !== null ? ` · nonce ${nonce}` : "",
          ),
          el("dt", {}, "Owners"),
          el("dd", {}, ...gov.owners.flatMap((o, i) => [i ? ", " : "", explorerLink(net, "address", o, short(o, 8))])),
          el("dt", {}, "Timelock"),
          el(
            "dd",
            {},
            explorerLink(net, "address", gov.timelock, gov.timelock),
            ` · delay ${Number(gov.timelock_delay) / 60} min`,
          ),
          el("dt", {}, "Dispute resolver"),
          el("dd", {}, explorerLink(net, "address", gov.dispute_resolver, gov.dispute_resolver)),
        ),
        el("h4", {}, "Role holders (from RoleGranted / RoleRevoked events)"),
        holders.length
          ? el("ul", { className: "mrow-list" }, ...holders)
          : el("p", { className: "hint" }, "No role events indexed (re-index from the deploy block)."),
        el("h4", {}, "Safe transactions"),
        props.length ? el("ul", { className: "mrow-list" }, ...props) : el("p", { className: "hint" }, "No proposals."),
        el("h4", {}, "History"),
        el(
          "ol",
          { className: "timeline" },
          ...g.history
            .slice(-15)
            .reverse()
            .map((e) =>
              el(
                "li",
                { className: "tl" },
                el("time", { className: "tl-time" }, when(e.time) || `block ${e.block}`),
                el(
                  "span",
                  { className: "tl-text" },
                  ...describeEvent(e, net, gw),
                  " · ",
                  explorerLink(net, "tx", e.tx),
                ),
              ),
            ),
        ),
      ),
    );
  }
  $("govBox").replaceChildren(...blocks);
}

async function main() {
  try {
    const [n, status] = await Promise.all([wallet.loadNetworks(), wallet.api("/api/status").catch(() => ({}))]);
    if (status.ipfs_gateway) gateway = status.ipfs_gateway;
    nets = Object.fromEntries(n.networks.map((x) => [x.network, x]));
  } catch (e) {
    msg($("globalMsg"), "err", `Mint-Gate is unreachable (${e.message}).`);
    return;
  }
  wallet.restore().catch(() => {});
  $("walletBtn").addEventListener("click", async () => {
    try {
      const a = await wallet.connect();
      $("walletBtn").textContent = short(a, 6);
    } catch (e) {
      msg($("globalMsg"), "err", wallet.explainError(e));
    }
  });
  for (const [fn, box] of [
    [loadStats, "statsBox"],
    [loadPolicies, "polBox"],
    [loadGovernance, "govBox"],
  ]) {
    fn().catch((e) => msg($(box), "err", `Could not load: ${e.message}`));
  }
}

main();
