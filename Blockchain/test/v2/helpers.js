// Shared fixture and EIP-712 signing helpers for the v2 tests.
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-network-helpers");

const Decision = { NONE: 0, PASS: 1, REVIEW: 2, FAIL: 3, REVIEW_APPROVED: 4, REVIEW_REJECTED: 5, VOIDED: 6 };
const ImageStatus = { NONE: 0, MINTED: 1, DISPUTED: 2, REVOKED: 3 };
const DisputeStatus = { NONE: 0, OPEN: 1, UPHELD: 2, REJECTED: 3 };
const BOND = ethers.parseEther("0.001");
const ADMIN_DELAY = 3600;
const h = (s) => ethers.id(s); // bytes32 helper

const VERDICT_TYPES = {
  Verdict: [
    { name: "imageHash", type: "bytes32" },
    { name: "reportHash", type: "bytes32" },
    { name: "policyHash", type: "bytes32" },
    { name: "score", type: "uint8" },
    { name: "decision", type: "uint8" },
    { name: "artist", type: "address" },
    { name: "issuedAt", type: "uint64" },
    { name: "expiresAt", type: "uint64" },
    { name: "nonce", type: "uint256" },
  ],
};

async function domainOf(registry) {
  const { chainId } = await ethers.provider.getNetwork();
  return { name: "MintGateRegistry", version: "1", chainId, verifyingContract: await registry.getAddress() };
}

let nonceCounter = 1n;

/** A valid PASS verdict for `artist`, overridable field by field. */
async function makeVerdict(artist, overrides = {}) {
  const now = BigInt(await time.latest());
  const tag = overrides.tag || `${nonceCounter}`;
  return {
    imageHash: h(`img-${tag}`),
    reportHash: h(`report-${tag}`),
    policyHash: h("policy-3.1.0"),
    score: 91,
    decision: Decision.PASS,
    artist,
    issuedAt: now,
    expiresAt: now + 3600n,
    nonce: nonceCounter++,
    ...Object.fromEntries(Object.entries(overrides).filter(([k]) => k !== "tag")),
  };
}

async function sign(signer, registry, verdict, domain) {
  return signer.signTypedData(domain || (await domainOf(registry)), VERDICT_TYPES, verdict);
}

async function deployFixture() {
  const [admin, verifier, reviewer, pauser, treasury, artist, artist2, outsider, verifier2] = await ethers.getSigners();
  const Registry = await ethers.getContractFactory("MintGateRegistry");
  const registry = await Registry.deploy(
    ADMIN_DELAY,
    admin.address,
    verifier.address,
    reviewer.address,
    pauser.address,
    treasury.address,
    BOND,
  );
  const nft = await (await ethers.getContractFactory("MintGateNFTV2")).deploy(await registry.getAddress());
  await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), await nft.getAddress());
  return { registry, nft, admin, verifier, reviewer, pauser, treasury, artist, artist2, outsider, verifier2 };
}

/** Sign + submit a verdict; returns the verdict. */
async function submit(ctx, artist, overrides = {}, signer = ctx.verifier) {
  const v = await makeVerdict(artist, overrides);
  await ctx.registry.submitVerdict(v, await sign(signer, ctx.registry, v));
  return v;
}

/** Submit a PASS verdict and mint it from the artist's wallet; returns { v, tokenId }. */
async function mintPass(ctx, artistSigner, overrides = {}) {
  const v = await submit(ctx, artistSigner.address, overrides);
  await ctx.nft.connect(artistSigner).mint(v.imageHash, "ipfs://meta", 500);
  return { v, tokenId: await ctx.nft.tokenOfImage(v.imageHash) };
}

module.exports = {
  Decision,
  ImageStatus,
  DisputeStatus,
  BOND,
  ADMIN_DELAY,
  VERDICT_TYPES,
  h,
  domainOf,
  makeVerdict,
  sign,
  deployFixture,
  submit,
  mintPass,
};
