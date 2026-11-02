const { expect } = require("chai");
const { ethers } = require("hardhat");

const PASS = 1, REVIEW = 2, FAIL = 3, REVIEW_APPROVED = 4, REVIEW_REJECTED = 5;
const h = (s) => ethers.id(s); // bytes32 helper

describe("MintGateNFT", function () {
  let nft, admin, verifier, reviewer, artist, outsider;

  beforeEach(async function () {
    [admin, verifier, reviewer, artist, outsider] = await ethers.getSigners();
    const F = await ethers.getContractFactory("MintGateNFT");
    nft = await F.deploy(admin.address, verifier.address, reviewer.address);
  });

  async function record(report, image, score, decision) {
    return nft.connect(verifier).recordVerification(h(report), h(image), score, decision, h("policy"), "1.1.0");
  }

  it("records a verdict and mints a PASS artwork with provenance", async function () {
    await expect(record("r1", "img1", 92, PASS))
      .to.emit(nft, "VerificationRecorded").withArgs(h("r1"), h("img1"), 92, PASS, "1.1.0", verifier.address);

    await expect(nft.connect(verifier).mintVerified(artist.address, h("r1"), "ipfs://meta"))
      .to.emit(nft, "VerifiedMint").withArgs(1, artist.address, h("r1"), h("img1"), 92);

    expect(await nft.ownerOf(1)).to.equal(artist.address);
    expect(await nft.tokenURI(1)).to.equal("ipfs://meta");
    expect(await nft.reportOfToken(1)).to.equal(h("r1"));
    const v = await nft.getVerification(h("r1"));
    expect(v.score).to.equal(92);
    expect(v.tokenId).to.equal(1);
  });

  it("blocks minting of FAIL verdicts but keeps them on record", async function () {
    await record("r2", "img2", 12, FAIL);
    await expect(nft.connect(verifier).mintVerified(artist.address, h("r2"), "x"))
      .to.be.revertedWithCustomError(nft, "NotMintable");
    expect((await nft.getVerification(h("r2"))).decision).to.equal(FAIL);
    expect(await nft.totalVerifications()).to.equal(1);
  });

  it("requires reviewer approval for REVIEW verdicts", async function () {
    await record("r3", "img3", 61, REVIEW);
    await expect(nft.connect(verifier).mintVerified(artist.address, h("r3"), "x"))
      .to.be.revertedWithCustomError(nft, "NotMintable");
    await expect(nft.connect(outsider).resolveReview(h("r3"), true))
      .to.be.revertedWithCustomError(nft, "AccessControlUnauthorizedAccount");
    await nft.connect(reviewer).resolveReview(h("r3"), true);
    expect((await nft.getVerification(h("r3"))).decision).to.equal(REVIEW_APPROVED);
    await nft.connect(verifier).mintVerified(artist.address, h("r3"), "x");
    expect(await nft.ownerOf(1)).to.equal(artist.address);
  });

  it("rejected reviews can never be minted", async function () {
    await record("r4", "img4", 55, REVIEW);
    await nft.connect(reviewer).resolveReview(h("r4"), false);
    expect((await nft.getVerification(h("r4"))).decision).to.equal(REVIEW_REJECTED);
    await expect(nft.connect(verifier).mintVerified(artist.address, h("r4"), "x"))
      .to.be.revertedWithCustomError(nft, "NotMintable");
  });

  it("prevents minting the same image twice, even with a new report", async function () {
    await record("r5", "same", 90, PASS);
    await record("r6", "same", 91, PASS);
    await nft.connect(verifier).mintVerified(artist.address, h("r5"), "x");
    await expect(nft.connect(verifier).mintVerified(artist.address, h("r5"), "x"))
      .to.be.revertedWithCustomError(nft, "ReportAlreadyUsed");
    await expect(nft.connect(verifier).mintVerified(artist.address, h("r6"), "x"))
      .to.be.revertedWithCustomError(nft, "ImageAlreadyMinted");
  });

  it("only the verifier can record or mint, and verdicts are immutable", async function () {
    await expect(nft.connect(outsider).recordVerification(h("r7"), h("i"), 99, PASS, h("p"), "1"))
      .to.be.revertedWithCustomError(nft, "AccessControlUnauthorizedAccount");
    await record("r7", "i7", 99, PASS);
    await expect(record("r7", "i7", 10, FAIL)).to.be.revertedWithCustomError(nft, "AlreadyRecorded");
    await expect(nft.connect(outsider).mintVerified(outsider.address, h("r7"), "x"))
      .to.be.revertedWithCustomError(nft, "AccessControlUnauthorizedAccount");
  });

  it("rejects malformed verdicts", async function () {
    await expect(record("r8", "i8", 101, PASS)).to.be.revertedWithCustomError(nft, "InvalidVerdict");
    await expect(record("r9", "i9", 50, REVIEW_APPROVED)).to.be.revertedWithCustomError(nft, "InvalidVerdict");
  });
});
