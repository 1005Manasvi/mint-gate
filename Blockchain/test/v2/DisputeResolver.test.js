const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { BOND, DisputeStatus, deployFixture, mintPass, h } = require("./helpers");

describe("DisputeResolver", function () {
  async function fixture() {
    const ctx = await deployFixture();
    const R = await ethers.getContractFactory("DisputeResolver");
    const resolver = await R.deploy(await ctx.registry.getAddress());
    await ctx.registry.connect(ctx.admin).grantRole(await ctx.registry.REVIEWER_ROLE(), await resolver.getAddress());
    const { v, tokenId } = await mintPass(ctx, ctx.artist);
    await ctx.registry.connect(ctx.outsider).openDispute(v.imageHash, "ipfs://evidence", { value: BOND });
    return { ...ctx, resolver, R, v, tokenId };
  }

  const reason = h("reason document bytes");

  it("needs a registry", async function () {
    const { R } = await loadFixture(fixture);
    await expect(R.deploy(ethers.ZeroAddress)).to.be.revertedWithCustomError(R, "ZeroAddress");
  });

  it("upheld: records the reason, revokes, refunds the bond, refreshes metadata", async function () {
    const { registry, nft, resolver, reviewer, outsider, artist, v, tokenId } = await loadFixture(fixture);
    const tx = resolver.connect(reviewer).resolveDispute(1, true, reason, "ipfs://reason");
    await expect(tx)
      .to.emit(resolver, "DisputeReasoned")
      .withArgs(1, v.imageHash, reviewer.address, true, reason, "ipfs://reason");
    await expect(tx).to.emit(registry, "ImageRevoked").withArgs(v.imageHash, artist.address, 1);
    await expect(tx)
      .to.emit(registry, "DisputeResolved")
      .withArgs(1, v.imageHash, true, await resolver.getAddress());
    await expect(tx).to.emit(nft, "MetadataUpdate").withArgs(tokenId);
    expect((await registry.disputeOf(1)).status).to.equal(DisputeStatus.UPHELD);
    expect(await registry.credits(outsider.address)).to.equal(BOND);
    expect(await nft.tokenURI(tokenId)).to.equal(await nft.REVOKED_URI());
  });

  it("rejected: bond to the treasury, no metadata update", async function () {
    const { registry, nft, resolver, reviewer, treasury } = await loadFixture(fixture);
    const tx = resolver.connect(reviewer).resolveDispute(1, false, reason, "ipfs://reason");
    await expect(tx).to.emit(resolver, "DisputeReasoned");
    await expect(tx).not.to.emit(nft, "MetadataUpdate");
    expect((await registry.disputeOf(1)).status).to.equal(DisputeStatus.REJECTED);
    expect(await registry.credits(treasury.address)).to.equal(BOND);
  });

  it("only registry reviewers may call it, and only with a reason", async function () {
    const { registry, resolver, reviewer, outsider, admin } = await loadFixture(fixture);
    await expect(resolver.connect(outsider).resolveDispute(1, true, reason, "ipfs://r"))
      .to.be.revertedWithCustomError(resolver, "NotReviewer")
      .withArgs(outsider.address);
    await expect(resolver.connect(admin).resolveDispute(1, true, reason, "ipfs://r")).to.be.revertedWithCustomError(
      resolver,
      "NotReviewer",
    );
    await expect(
      resolver.connect(reviewer).resolveDispute(1, true, ethers.ZeroHash, "ipfs://r"),
    ).to.be.revertedWithCustomError(resolver, "EmptyReason");
    await expect(resolver.connect(reviewer).resolveDispute(1, true, reason, "")).to.be.revertedWithCustomError(
      resolver,
      "EmptyReason",
    );
    // revoking the reviewer's registry role also revokes its access here
    await registry.connect(admin).revokeRole(await registry.REVIEWER_ROLE(), reviewer.address);
    await expect(resolver.connect(reviewer).resolveDispute(1, true, reason, "ipfs://r")).to.be.revertedWithCustomError(
      resolver,
      "NotReviewer",
    );
  });

  it("unknown or settled disputes revert in the registry", async function () {
    const { registry, resolver, reviewer } = await loadFixture(fixture);
    await expect(resolver.connect(reviewer).resolveDispute(99, true, reason, "ipfs://r"))
      .to.be.revertedWithCustomError(registry, "DisputeNotOpen")
      .withArgs(99);
    await resolver.connect(reviewer).resolveDispute(1, false, reason, "ipfs://r");
    await expect(resolver.connect(reviewer).resolveDispute(1, true, reason, "ipfs://r")).to.be.revertedWithCustomError(
      registry,
      "DisputeNotOpen",
    );
  });

  it("without REVIEWER_ROLE on the registry the resolver itself cannot resolve", async function () {
    const { registry, resolver, reviewer, admin } = await loadFixture(fixture);
    await registry.connect(admin).revokeRole(await registry.REVIEWER_ROLE(), await resolver.getAddress());
    await expect(resolver.connect(reviewer).resolveDispute(1, true, reason, "ipfs://r")).to.be.revertedWithCustomError(
      registry,
      "AccessControlUnauthorizedAccount",
    );
  });

  it("a collection without refreshMetadata does not block an upheld dispute", async function () {
    const ctx = await loadFixture(fixture);
    const { registry, resolver, reviewer, admin, artist2, outsider } = ctx;
    const tp = await (await ethers.getContractFactory("ThirdPartyCollection")).deploy(await registry.getAddress());
    await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), await tp.getAddress());
    const { submit } = require("./helpers");
    const v = await submit(ctx, artist2.address);
    await tp.connect(artist2).mint(v.imageHash);
    await registry.connect(outsider).openDispute(v.imageHash, "ipfs://e2", { value: BOND });
    await expect(resolver.connect(reviewer).resolveDispute(2, true, reason, "ipfs://r"))
      .to.emit(registry, "ImageRevoked")
      .withArgs(v.imageHash, artist2.address, 2);
    expect(await registry.isRevoked(v.imageHash)).to.equal(true);
  });
});
