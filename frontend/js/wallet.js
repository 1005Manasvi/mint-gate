// Wallet layer: Sign-In with Ethereum, chain switching, signed uploads, minting from the artist's
// wallet. Talks to an injected EIP-1193 wallet (MetaMask, Rabby, ...) through the vendored ethers v6;
// no third-party script is loaded (CSP: script-src 'self').
"use strict";

import { BrowserProvider, Contract, Interface, getAddress } from "../vendor/ethers-6.17.0.min.js";
import { sha256Hex } from "./canonical.js";

export class WalletError extends Error {}

let networks = null; // GET /api/networks?abi=1
let session = null; // GET /api/auth/me: {address, chain_id, csrf, expires_at}
let account = null; // wallet's selected account (may differ from the session wallet)
const listeners = new Set();

export const getSession = () => session;
export const getAccount = () => account;
export const hasWallet = () => Boolean(window.ethereum);
export function onChange(fn) {
  listeners.add(fn);
}
const emit = () => listeners.forEach((fn) => fn({ session, account }));

// ---------------- HTTP ----------------

/** fetch + JSON + {success:false} -> Error. Adds the CSRF header to state-changing requests. */
export async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = new Headers(opts.headers || {});
  if (method !== "GET" && session) headers.set("X-CSRF-Token", session.csrf);
  if (opts.json !== undefined) {
    headers.set("Content-Type", "application/json");
    opts = { ...opts, body: JSON.stringify(opts.json) };
  }
  const res = await fetch(path, { ...opts, method, headers, credentials: "same-origin" });
  let data;
  try {
    data = await res.json();
  } catch {
    data = { error: `HTTP ${res.status}` };
  }
  if (!res.ok || data.success === false) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ---------------- networks ----------------

export async function loadNetworks() {
  if (!networks) networks = await api("/api/networks?abi=1");
  return networks;
}

export function networkByChainId(chainId) {
  return networks?.networks.find((n) => n.chain_id === Number(chainId)) || null;
}

export function networkByName(name) {
  return networks?.networks.find((n) => n.network === name) || null;
}

function eth() {
  if (!window.ethereum)
    throw new WalletError("No wallet found. Install MetaMask (metamask.io) or another browser wallet.");
  return window.ethereum;
}

const toHex = (n) => "0x" + Number(n).toString(16);

export async function currentChainId() {
  return parseInt(await eth().request({ method: "eth_chainId" }), 16);
}

/** Switch the wallet to `net`, adding the chain first if the wallet does not know it (EIP-3085 / 3326). */
export async function ensureChain(net) {
  if ((await currentChainId()) === net.chain_id) return;
  try {
    await eth().request({ method: "wallet_switchEthereumChain", params: [{ chainId: toHex(net.chain_id) }] });
  } catch (e) {
    const code = e.code ?? e.data?.originalError?.code;
    if (code !== 4902 || !net.rpc_url) throw e;
    await eth().request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: toHex(net.chain_id),
          chainName: net.label,
          rpcUrls: [net.rpc_url],
          nativeCurrency: net.native_currency,
          ...(net.explorer ? { blockExplorerUrls: [net.explorer] } : {}),
        },
      ],
    });
  }
}

// ---------------- accounts + SIWE ----------------

export async function connect() {
  const [acc] = await eth().request({ method: "eth_requestAccounts" });
  account = acc ? getAddress(acc) : null;
  emit();
  return account;
}

/** Restore state on page load without prompting the user. */
export async function restore() {
  if (window.ethereum) {
    try {
      const [acc] = await window.ethereum.request({ method: "eth_accounts" });
      account = acc ? getAddress(acc) : null;
    } catch {
      account = null;
    }
    window.ethereum.on?.("accountsChanged", (accs) => {
      account = accs[0] ? getAddress(accs[0]) : null;
      emit();
    });
  }
  await refreshSession();
}

export async function refreshSession() {
  try {
    session = await api("/api/auth/me");
  } catch {
    session = null;
  }
  emit();
  return session;
}

/** EIP-4361 text; must match auth.SiweMessage.text() in the backend. */
export function siweMessage({ domain, address, statement, uri, chainId, nonce, issuedAt }) {
  return [
    `${domain} wants you to sign in with your Ethereum account:`,
    address,
    "",
    statement,
    "",
    `URI: ${uri}`,
    "Version: 1",
    `Chain ID: ${chainId}`,
    `Nonce: ${nonce}`,
    `Issued At: ${issuedAt}`,
  ].join("\n");
}

async function signer() {
  return new BrowserProvider(eth(), "any").getSigner();
}

export async function signIn() {
  const address = account || (await connect());
  if (!address) throw new WalletError("No account selected in the wallet.");
  let chainId = await currentChainId();
  await loadNetworks();
  if (!networkByChainId(chainId)) {
    const net = networkByName(networks.default) || networks.networks[0];
    await ensureChain(net);
    chainId = net.chain_id;
  }
  const { nonce, statement } = await api("/api/auth/nonce");
  const message = siweMessage({
    domain: location.host,
    address,
    statement,
    uri: location.origin,
    chainId,
    nonce,
    issuedAt: new Date().toISOString(),
  });
  const signature = await (await signer()).signMessage(message);
  session = await api("/api/auth/verify", { method: "POST", json: { message, signature } });
  emit();
  return session;
}

export async function signOut() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } finally {
    session = null;
    emit();
  }
}

// ---------------- signed upload ----------------

/** What the wallet signs for an upload; must match auth.claim_message() in the backend. */
export function claimMessage(domain, wallet, fileSha256, issuedAt) {
  return [
    "Mint-Gate artwork submission",
    "I submit this artwork for verification and claim to be its creator.",
    "",
    `Domain: ${domain}`,
    `Wallet: ${wallet}`,
    `File SHA-256: 0x${fileSha256}`,
    `Issued At: ${issuedAt}`,
  ].join("\n");
}

/** personal_sign over the SHA-256 of the exact file bytes; returns the extra form fields. */
export async function signUpload(file) {
  if (!session) throw new WalletError("Sign in with your wallet first.");
  const s = await signer();
  if (getAddress(await s.getAddress()) !== session.address) {
    throw new WalletError(`Your wallet switched accounts: select ${session.address} or sign in again.`);
  }
  const digest = await sha256Hex(new Uint8Array(await file.arrayBuffer()));
  const issuedAt = new Date().toISOString();
  const signature = await s.signMessage(claimMessage(location.host, session.address, digest, issuedAt));
  return { claim_issued_at: issuedAt, claim_signature: signature, file_sha256: digest };
}

// ---------------- minting ----------------

/** NFT ABI + the registry's errors: the registry's reverts bubble up through the NFT's calls. */
function mintInterface(net) {
  const seen = new Set();
  const frags = [];
  for (const f of [...net.abi.nft, ...net.abi.registry.filter((x) => x.type === "error")]) {
    const key = `${f.type}:${f.name}:${(f.inputs || []).map((i) => i.type).join(",")}`;
    if (f.type === "constructor" && seen.has("constructor")) continue;
    if (!seen.has(key)) (seen.add(key), frags.push(f));
  }
  return new Interface(frags);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Mint from the artist wallet with an attestation from POST /api/attest/<id>.
 * onStep({stage, ...}) stages: switching, estimating, wallet, pending, confirming, confirmed.
 */
export async function mint(att, royaltyBps, onStep = () => {}) {
  await loadNetworks();
  const net = networkByChainId(att.chain_id);
  if (!net) throw new WalletError(`Network ${att.network} is not configured.`);
  onStep({ stage: "switching", network: net.label });
  await ensureChain(net);
  const provider = new BrowserProvider(eth(), "any");
  const s = await provider.getSigner();
  if (getAddress(await s.getAddress()) !== getAddress(att.artist)) {
    throw new WalletError(
      `Only the artist wallet ${att.artist} can mint this artwork: switch accounts in your wallet.`,
    );
  }
  const nft = new Contract(att.nft, mintInterface(net), s);
  const args =
    att.call === "submitAndMint"
      ? [att.verdict, att.signature, att.token_uri, royaltyBps]
      : [att.image_hash, att.token_uri, royaltyBps];

  onStep({ stage: "estimating" });
  const gas = await nft[att.call].estimateGas(...args); // a revert is decoded here, before any prompt
  const fees = await provider.getFeeData();
  const price = fees.maxFeePerGas ?? fees.gasPrice;
  onStep({ stage: "wallet", gas, maxCost: price ? gas * price : null, symbol: net.native_currency.symbol });

  const tx = await nft[att.call](...args, { gasLimit: (gas * 12n) / 10n });
  onStep({ stage: "pending", hash: tx.hash });
  const receipt = await tx.wait(1);
  const log = receipt.logs
    .map((l) => {
      try {
        return l.address.toLowerCase() === att.nft.toLowerCase() ? nft.interface.parseLog(l) : null;
      } catch {
        return null;
      }
    })
    .find((l) => l?.name === "VerifiedMint");
  const tokenId = log ? log.args.tokenId.toString() : null;
  const needed = Math.max(1, net.confirmations || 1);
  let confirmations = await receipt.confirmations();
  while (confirmations < needed) {
    onStep({ stage: "confirming", hash: tx.hash, tokenId, confirmations, needed });
    await sleep(2500);
    confirmations = await receipt.confirmations();
  }
  const result = {
    stage: "confirmed",
    hash: tx.hash,
    tokenId,
    confirmations,
    needed,
    gasUsed: receipt.gasUsed,
    block: receipt.blockNumber,
    network: net.network,
    contract: att.nft,
  };
  onStep(result);
  return result;
}

// ---------------- errors ----------------

const short = (a) => (a ? `${String(a).slice(0, 8)}…${String(a).slice(-4)}` : "");

const FRIENDLY = {
  NotMintable: () =>
    "The registry has no passing verdict for this image and wallet yet. If it was just approved, wait for the review transaction and retry.",
  ImageAlreadyMinted: ([, collection, tokenId]) =>
    `This image is already minted (token #${tokenId} in ${short(collection)}). Each artwork can be minted once.`,
  VerdictExpired: () => "The attestation expired. Request a new one (button below) and mint again.",
  ArtistMismatch: ([artist]) => `This attestation belongs to ${short(artist)}: switch your wallet to that account.`,
  UnauthorizedSigner: () => "The attestation was not signed by an active Mint-Gate verifier on this chain.",
  NonceAlreadyUsed: () => "This attestation was already used. Request a new one.",
  AlreadyRecorded: () => "A verdict for this report is already on chain. Try minting again in a moment.",
  InvalidVerdict: () => "The attestation is malformed or not a passing verdict.",
  RoyaltyTooHigh: () => "Royalty is capped at 10 %.",
  EmptyURI: () => "The token metadata URI is missing.",
  EnforcedPause: () => "Mint-Gate is paused by its operators. Try again later.",
  ERC721InvalidReceiver: () => "Your wallet address cannot receive ERC-721 tokens.",
};

/** A sentence a person can act on, from a wallet / RPC / contract error. */
export function explainError(e) {
  if (e instanceof WalletError) return e.message;
  const code = e?.code ?? e?.info?.error?.code;
  if (code === "ACTION_REJECTED" || code === 4001) return "You rejected the request in your wallet.";
  if (code === 4902) return "Your wallet does not know this network; add it and retry.";
  if (code === "INSUFFICIENT_FUNDS")
    return "Not enough ETH on this network to pay for gas. Get test ETH from a faucet.";
  if (e?.revert?.name) {
    const f = FRIENDLY[e.revert.name];
    return f ? f(e.revert.args || []) : `Contract error ${e.revert.name}(${(e.revert.args || []).join(", ")})`;
  }
  if (code === "CALL_EXCEPTION") return "The contract rejected the transaction.";
  if (code === "NETWORK_ERROR" || code === "TIMEOUT")
    return "Cannot reach the blockchain node. Check your connection and retry.";
  return e?.shortMessage || e?.message || String(e);
}
