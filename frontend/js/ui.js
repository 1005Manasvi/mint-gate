// DOM helpers. All server and chain data is rendered with textContent / properties (never
// innerHTML), so report contents cannot inject markup.
"use strict";

/** el("a", {href, "aria-label": ...}, ...children): hyphenated keys and role become attributes. */
export function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.includes("-") || k === "role") n.setAttribute(k, v);
    else n[k] = v;
  }
  for (const c of children.flat(Infinity)) n.append(c instanceof Node ? c : document.createTextNode(String(c ?? "")));
  return n;
}

export const short = (h, n = 10) => (h ? `${h.slice(0, n)}…${h.slice(-6)}` : "–");

export function link(text, href) {
  return el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
}

/** Block-explorer link when the network has one, plain monospace text otherwise. */
export function explorerLink(net, kind, value, text = short(value)) {
  if (!value) return "–";
  if (!net?.explorer) return el("span", { className: "mono" }, text);
  const path = { tx: `tx/${value}`, address: `address/${value}`, block: `block/${value}` }[kind];
  return link(text, `${net.explorer}/${path}`);
}

/** Replace target's content with one status box. kind: ok | warn | err */
export function msg(target, kind, ...parts) {
  const box = el("div", { className: kind, role: kind === "err" ? "alert" : "status" }, ...parts);
  target.replaceChildren(box);
  return box;
}

export function tokenPath(network, contract, tokenId) {
  return `/token/${encodeURIComponent(network)}/${contract}/${tokenId}`;
}

/** Mark progress on an <ol class="steps"> whose items carry data-stage, in the order of `stages`. */
export function markSteps(list, stages, stage, extra = "", failed = false) {
  const idx = stages.indexOf(stage === "confirmed" ? stages[stages.length - 1] : stage);
  for (const li of list.children) {
    const i = stages.indexOf(li.dataset.stage);
    li.className = i < idx || stage === "confirmed" ? "done" : i === idx ? (failed ? "failed" : "active") : "";
    li.querySelector(".step-extra")?.remove();
    if (i === idx && extra) li.append(el("span", { className: "step-extra" }, extra));
    if (i === idx) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  }
}

export const formatEth = (wei) => {
  const eth = Number(wei) / 1e18;
  return eth === 0 ? "0" : eth < 0.0001 ? eth.toExponential(2) : String(+eth.toFixed(6));
};

export const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "");

const DECISIONS = { 1: "PASS", 2: "REVIEW", 3: "FAIL" };
const ZERO = "0x0000000000000000000000000000000000000000";

/** One sentence (DOM nodes) for an indexed chain event; `gateway(uri)` -> http URL. */
export function describeEvent(e, net, gateway = (u) => u) {
  const a = e.args || {};
  const addr = (x) => explorerLink(net, "address", x, short(x, 8));
  const doc = (text, uri) => (uri ? link(text, gateway(uri)) : "");
  switch (e.event) {
    case "VerdictSubmitted":
      return [`Verdict ${DECISIONS[a.decision] || a.decision} (score ${a.score}) recorded for artist `, addr(a.artist)];
    case "ReviewResolved":
      return [`Human review: ${a.approved ? "approved" : "rejected"} by `, addr(a.reviewer)];
    case "VerdictVoided":
      return ["Verdict voided by ", addr(a.reviewer)];
    case "ImageMinted":
      return [`Minted as token #${a.tokenId} by `, addr(a.artist)];
    case "Transfer":
      return a.from === ZERO ? ["Token created"] : ["Transferred from ", addr(a.from), " to ", addr(a.to)];
    case "DisputeOpened":
      return [
        `Dispute #${a.disputeId} opened by `,
        addr(a.disputant),
        ` (bond ${formatEth(a.bond)} ETH) · `,
        doc("evidence", a.evidenceURI),
      ];
    case "DisputeReasoned":
      return [
        `Reviewer `,
        addr(a.reviewer),
        ` ${a.upheld ? "upheld" : "rejected"} dispute #${a.disputeId} · `,
        doc("written reason", a.reasonURI),
      ];
    case "DisputeResolved":
      return [
        `Dispute #${a.disputeId} ${a.upheld ? "upheld: bond returned to the reporter" : "rejected: bond forfeited"}`,
      ];
    case "ImageRevoked":
      return ["Artwork revoked (metadata now says revoked, royalties 0)"];
    case "PolicySet":
      return [`Policy ${a.version} announced · `, doc("policy JSON", a.uri)];
    case "CallScheduled":
      return [`Timelocked call scheduled (executable after ${Number(a.delay) / 60} min)`];
    case "ExecutionSuccess":
      return ["Safe transaction executed"];
    case "RoleGranted":
    case "RoleRevoked":
      return [
        `${e.event === "RoleGranted" ? "Granted" : "Revoked"} ${e.role_name || "role"} ${e.event === "RoleGranted" ? "to" : "from"} `,
        addr(a.account),
      ];
    case "Paused":
      return ["Registry paused by ", addr(a.account)];
    case "Unpaused":
      return ["Registry unpaused by ", addr(a.account)];
    default:
      return [e.event];
  }
}
