// Provenance check that does not trust the Mint-Gate backend: every fact comes from the chain (the
// visitor's wallet, or the chain's public RPC) and from the content-addressed files, and the report
// hash is recomputed here. The backend only says which RPC to use when no wallet is present.
"use strict";

import { BrowserProvider, Contract, JsonRpcProvider, getAddress } from "../vendor/ethers-6.17.0.min.js";
import { pixelHash, reportHash } from "./canonical.js";

// Minimal ABIs written out here on purpose (not fetched from the backend).
const NFT_ABI = [
  "function ownerOf(uint256) view returns (address)",
  "function originalTokenURI(uint256) view returns (string)",
  "function imageOfToken(uint256) view returns (bytes32)",
  "function mintGate() view returns (address)",
];
const REGISTRY_ABI = [
  "function mintOf(bytes32) view returns (address collection, uint256 tokenId, bytes32 reportHash, uint256 activeDispute)",
  "function verdictOf(bytes32) view returns (tuple(bytes32 imageHash, bytes32 policyHash, address artist, uint8 score, uint8 decision, uint64 issuedAt, address signer, uint64 expiresAt))",
  "function imageStatus(bytes32) view returns (uint8)",
  "function VERIFIER_ROLE() view returns (bytes32)",
  "function hasRole(bytes32, address) view returns (bool)",
];
export const DECISIONS = ["NONE", "PASS", "REVIEW", "FAIL", "REVIEW_APPROVED", "REVIEW_REJECTED", "VOIDED"];
export const IMAGE_STATUS = ["NONE", "MINTED", "DISPUTED", "REVOKED"];

const hex = (b32) => b32.slice(2).toLowerCase();

/** The visitor's wallet if it is on the right chain, the chain's public RPC otherwise. */
export async function readProvider(net) {
  if (window.ethereum) {
    try {
      const id = parseInt(await window.ethereum.request({ method: "eth_chainId" }), 16);
      if (id === net.chain_id) return { provider: new BrowserProvider(window.ethereum), source: "your wallet" };
    } catch {
      /* fall back to the public RPC */
    }
  }
  if (!net.rpc_url) throw new Error(`no public RPC for ${net.label}: connect a wallet on that chain`);
  const provider = new JsonRpcProvider(net.rpc_url, net.chain_id, { staticNetwork: true });
  return { provider, source: new URL(net.rpc_url).host };
}

export function gatewayUrl(uri, gateway) {
  return uri.startsWith("ipfs://") ? gateway + uri.slice(7) : uri;
}

async function fetchText(url) {
  const res = await fetch(url, { credentials: "omit" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return res.text();
}

/** Decode an image exactly (no colour management) and hash its pixels like the backend. */
async function imagePixelHash(url) {
  const blob = await (await fetch(url, { credentials: "omit" })).blob();
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  const canvas = new OffscreenCanvas(bmp.width, bmp.height);
  const ctx = canvas.getContext("2d", { colorSpace: "srgb" });
  ctx.drawImage(bmp, 0, 0);
  const { data } = ctx.getImageData(0, 0, bmp.width, bmp.height);
  return pixelHash(bmp.width, bmp.height, data);
}

/**
 * Runs every check; calls onCheck({id, ok, title, detail}) as results arrive (ok: true / false /
 * null = could not check). Returns the summary {ok, facts}.
 */
export async function verifyToken({ net, contract, tokenId, gateway, knownRegistry }, onCheck) {
  const facts = {};
  const results = [];
  const check = (id, ok, title, detail = "") => {
    const r = { id, ok, title, detail };
    results.push(r);
    onCheck(r);
    return ok;
  };

  const { provider, source } = await readProvider(net);
  facts.source = source;
  const nft = new Contract(contract, NFT_ABI, provider);

  let owner;
  try {
    owner = await nft.ownerOf(tokenId);
  } catch {
    check("exists", false, "Token exists on chain", `no token #${tokenId} at ${contract} on ${net.label}`);
    return { ok: false, facts, results };
  }
  facts.owner = owner;
  check("exists", true, "Token exists on chain", `owned by ${owner} (read via ${source})`);

  const [registryAddr, image, uri] = await Promise.all([
    nft.mintGate(),
    nft.imageOfToken(tokenId),
    nft.originalTokenURI(tokenId),
  ]);
  facts.registry = registryAddr;
  facts.imageHash = hex(image);
  facts.tokenUri = uri;
  const official = knownRegistry && getAddress(knownRegistry) === getAddress(registryAddr);
  check(
    "registry",
    official ? true : null,
    "Collection is gated by the Mint-Gate registry",
    official
      ? `registry ${registryAddr}`
      : `this collection uses registry ${registryAddr}, which is not the Mint-Gate deployment on ${net.label}`,
  );

  const registry = new Contract(registryAddr, REGISTRY_ABI, provider);
  const m = await registry.mintOf(image);
  facts.reportHash = hex(m.reportHash);
  check(
    "consumed",
    getAddress(m.collection) === getAddress(contract) && m.tokenId === BigInt(tokenId),
    "Registry recorded this exact mint",
    `image ${facts.imageHash.slice(0, 16)}… → token #${m.tokenId} of ${m.collection}`,
  );

  const v = await registry.verdictOf(m.reportHash);
  const decision = DECISIONS[Number(v.decision)];
  facts.verdict = {
    decision,
    score: Number(v.score),
    policyHash: hex(v.policyHash),
    artist: v.artist,
    signer: v.signer,
    issuedAt: Number(v.issuedAt),
  };
  check(
    "verdict",
    decision === "PASS" || decision === "REVIEW_APPROVED",
    "A passing verdict is on chain",
    `${decision}, score ${v.score}/100, artist ${v.artist}`,
  );
  const role = await registry.VERIFIER_ROLE();
  const signerActive = await registry.hasRole(role, v.signer);
  check(
    "signer",
    signerActive ? true : null,
    "Verdict was signed by a Mint-Gate verifier",
    signerActive ? `signer ${v.signer} holds VERIFIER_ROLE` : `signer ${v.signer} no longer holds VERIFIER_ROLE`,
  );
  const status = IMAGE_STATUS[Number(await registry.imageStatus(image))];
  facts.status = status;
  check(
    "status",
    status === "MINTED" ? true : status === "DISPUTED" ? null : false,
    "No upheld dispute against the artwork",
    { MINTED: "active", DISPUTED: "a dispute is open", REVOKED: "revoked: a reviewer upheld a dispute" }[status] ||
      status,
  );

  // ---- off-chain files, verified against the on-chain hashes ----
  let meta;
  try {
    meta = JSON.parse(await fetchText(gatewayUrl(uri, gateway)));
    facts.metadata = meta;
  } catch (e) {
    check("report", null, "Verification report matches the on-chain hash", `could not load the metadata: ${e.message}`);
    return summary();
  }
  const reportUri = meta?.mint_gate?.verification_report;
  facts.reportUri = reportUri;
  if (!reportUri) {
    check("report", null, "Verification report matches the on-chain hash", "the metadata names no report");
    return summary();
  }
  let reportText;
  try {
    reportText = await fetchText(gatewayUrl(reportUri, gateway));
  } catch (e) {
    check("report", null, "Verification report matches the on-chain hash", `could not load the report: ${e.message}`);
    return summary();
  }
  const recomputed = await reportHash(reportText);
  facts.recomputedHash = recomputed;
  check(
    "report",
    recomputed === facts.reportHash,
    "Verification report matches the on-chain hash",
    `recomputed in your browser: ${recomputed}\non chain: ${facts.reportHash}`,
  );
  const report = JSON.parse(reportText);
  facts.report = report;
  check(
    "fields",
    report.image_sha256 === facts.imageHash &&
      report.policy_sha256 === facts.verdict.policyHash &&
      report.score === facts.verdict.score,
    "Report agrees with the verdict (image, policy, score)",
    `policy ${report.policy_version} · ${String(report.policy_sha256).slice(0, 16)}…`,
  );

  if (meta.image) {
    try {
      const px = await imagePixelHash(gatewayUrl(meta.image, gateway));
      check(
        "pixels",
        px === facts.imageHash,
        "Artwork file is the verified image",
        px === facts.imageHash ? "pixel hash recomputed in your browser" : `pixel hash ${px.slice(0, 16)}… differs`,
      );
    } catch (e) {
      check("pixels", null, "Artwork file is the verified image", `could not decode the image: ${e.message}`);
    }
  }
  return summary();

  function summary() {
    return { ok: results.every((r) => r.ok !== false), complete: results.every((r) => r.ok === true), facts, results };
  }
}
