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
  for (const c of children) n.append(c instanceof Node ? c : document.createTextNode(String(c ?? "")));
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
