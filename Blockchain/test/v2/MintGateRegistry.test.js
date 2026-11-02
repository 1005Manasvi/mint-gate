const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const {
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
} = require("./helpers");

const SECP256K1_N = BigInt("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141");

describe("MintGateRegistry", function () {
  let ctx, registry;

  beforeEach(async function () {
    ctx = await loadFixture(deployFixture);
    registry = ctx.registry;
  });

  describe("deployment", function () {
    it("sets roles, parameters and the EIP-712 domain", async function () {
      const { admin, verifier, reviewer, pauser, treasury, nft } = ctx;
      expect(await registry.defaultAdmin()).to.equal(admin.address);
      expect(await registry.defaultAdminDelay()).to.equal(ADMIN_DELAY);
      expect(await registry.hasRole(await registry.VERIFIER_ROLE(), verifier.address)).to.be.true;
      expect(await registry.hasRole(await registry.REVIEWER_ROLE(), reviewer.address)).to.be.true;
      expect(await registry.hasRole(await registry.PAUSER_ROLE(), pauser.address)).to.be.true;
      expect(await registry.hasRole(await registry.COLLECTION_ROLE(), await nft.getAddress())).to.be.true;
      expect(await registry.treasury()).to.equal(treasury.address);
      expect(await registry.disputeBond()).to.equal(BOND);

      const d = await registry.eip712Domain();
      const expected = await domainOf(registry);
      expect(d.name).to.equal(expected.name);
      expect(d.version).to.equal(expected.version);
      expect(d.chainId).to.equal(expected.chainId);
      expect(d.verifyingContract).to.equal(expected.verifyingContract);
    });

    it("exposes the documented typehash", async function () {
      const typeString =
        "Verdict(bytes32 imageHash,bytes32 reportHash,bytes32 policyHash,uint8 score,uint8 decision," +
        "address artist,uint64 issuedAt,uint64 expiresAt,uint256 nonce)";
      expect(await registry.VERDICT_TYPEHASH()).to.equal(ethers.id(typeString));
      expect(ethers.TypedDataEncoder.from(VERDICT_TYPES).encodeType("Verdict")).to.equal(typeString);
    });

    it("supports IMintGate, AccessControl and ERC-165", async function () {
      const iface = (await ethers.getContractAt("IMintGate", ethers.ZeroAddress)).interface;
      let id = 0n;
      iface.forEachFunction((f) => (id ^= BigInt(f.selector)));
      expect(await registry.supportsInterface(ethers.toBeHex(id, 4))).to.be.true;
      expect(await registry.supportsInterface("0x7965db0b")).to.be.true; // IAccessControl
      expect(await registry.supportsInterface("0x01ffc9a7")).to.be.true; // ERC-165
      expect(await registry.supportsInterface("0xffffffff")).to.be.false;
    });

    it("rejects zero addresses and oversized bonds", async function () {
      const [a, b] = await ethers.getSigners();
      const R = await ethers.getContractFactory("MintGateRegistry");
      const Z = ethers.ZeroAddress;
      await expect(R.deploy(0, a, Z, b, b, b, 0)).to.be.revertedWithCustomError(R, "ZeroAddress");
      await expect(R.deploy(0, a, b, Z, b, b, 0)).to.be.revertedWithCustomError(R, "ZeroAddress");
      await expect(R.deploy(0, a, b, b, Z, b, 0)).to.be.revertedWithCustomError(R, "ZeroAddress");
      await expect(R.deploy(0, a, b, b, b, Z, 0)).to.be.revertedWithCustomError(R, "ZeroAddress");
      await expect(R.deploy(0, Z, b, b, b, b, 0)).to.be.revertedWithCustomError(R, "AccessControlInvalidDefaultAdmin");
      await expect(R.deploy(0, a, b, b, b, b, 2n ** 96n)).to.be.revertedWithCustomError(
        R,
        "SafeCastOverflowedUintDowncast",
      );
    });
  });

  describe("submitVerdict", function () {
    it("stores a PASS verdict submitted by anyone and makes it mintable", async function () {
      const { verifier, artist, outsider } = ctx;
      const v = await makeVerdict(artist.address);
      const sig = await sign(verifier, registry, v);
      await expect(registry.connect(outsider).submitVerdict(v, sig))
        .to.emit(registry, "VerdictSubmitted")
        .withArgs(v.reportHash, v.imageHash, artist.address, 91, Decision.PASS, v.policyHash, verifier.address);

      const r = await registry.verdictOf(v.reportHash);
      expect(r.imageHash).to.equal(v.imageHash);
      expect(r.policyHash).to.equal(v.policyHash);
      expect(r.artist).to.equal(artist.address);
      expect(r.score).to.equal(91);
      expect(r.decision).to.equal(Decision.PASS);
      expect(r.issuedAt).to.equal(v.issuedAt);
      expect(r.expiresAt).to.equal(v.expiresAt);
      expect(r.signer).to.equal(verifier.address);
      expect(await registry.totalVerdicts()).to.equal(1);
      expect(await registry.nonceUsed(verifier.address, v.nonce)).to.be.true;
      expect(await registry.isMintable(v.imageHash, artist.address)).to.deep.equal([true, v.reportHash]);
      expect(await registry.isMintable(v.imageHash, outsider.address)).to.deep.equal([false, ethers.ZeroHash]);
      expect(await registry.imageStatus(v.imageHash)).to.equal(ImageStatus.NONE);
      expect(await registry.isRevoked(v.imageHash)).to.be.false;
    });

    it("returns the signer (static call) and matches ethers' EIP-712 digest", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = await sign(ctx.verifier, registry, v);
      expect(await registry.submitVerdict.staticCall(v, sig)).to.equal(ctx.verifier.address);
      expect(await registry.hashVerdict(v)).to.equal(
        ethers.TypedDataEncoder.hash(await domainOf(registry), VERDICT_TYPES, v),
      );
    });

    it("stores REVIEW and FAIL verdicts, which are not mintable", async function () {
      const rv = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      const fv = await submit(ctx, ctx.artist.address, { decision: Decision.FAIL, score: 7 });
      expect((await registry.verdictOf(rv.reportHash)).decision).to.equal(Decision.REVIEW);
      expect((await registry.verdictOf(fv.reportHash)).decision).to.equal(Decision.FAIL);
      expect((await registry.isMintable(rv.imageHash, ctx.artist.address))[0]).to.be.false;
      expect((await registry.isMintable(fv.imageHash, ctx.artist.address))[0]).to.be.false;
      expect(await registry.totalVerdicts()).to.equal(2);
    });

    it("a later FAIL does not override an earlier PASS for the same image", async function () {
      const pass = await submit(ctx, ctx.artist.address, { tag: "same-a" });
      await submit(ctx, ctx.artist.address, {
        tag: "same-b",
        imageHash: pass.imageHash,
        decision: Decision.FAIL,
      });
      expect(await registry.isMintable(pass.imageHash, ctx.artist.address)).to.deep.equal([true, pass.reportHash]);
    });

    for (const [name, override] of [
      ["zero imageHash", { imageHash: ethers.ZeroHash }],
      ["zero reportHash", { reportHash: ethers.ZeroHash }],
      ["zero artist", { artist: ethers.ZeroAddress }],
      ["score above 100", { score: 101 }],
      ["decision NONE", { decision: Decision.NONE }],
      ["decision REVIEW_APPROVED", { decision: Decision.REVIEW_APPROVED }],
      ["decision REVIEW_REJECTED", { decision: Decision.REVIEW_REJECTED }],
      ["decision VOIDED", { decision: Decision.VOIDED }],
    ]) {
      it(`rejects ${name}`, async function () {
        const v = await makeVerdict(ctx.artist.address, override);
        await expect(registry.submitVerdict(v, await sign(ctx.verifier, registry, v))).to.be.revertedWithCustomError(
          registry,
          "InvalidVerdict",
        );
      });
    }

    it("rejects an out-of-range decision during ABI decoding", async function () {
      const v = await makeVerdict(ctx.artist.address, { decision: 7 });
      await expect(registry.submitVerdict(v, await sign(ctx.verifier, registry, v))).to.be.reverted;
    });

    it("accepts score 100 and score 0", async function () {
      await submit(ctx, ctx.artist.address, { score: 100 });
      await submit(ctx, ctx.artist.address, { score: 0, decision: Decision.FAIL });
    });

    it("rejects an expired signature (at and after expiry)", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = await sign(ctx.verifier, registry, v);
      await time.setNextBlockTimestamp(v.expiresAt);
      await expect(registry.submitVerdict(v, sig))
        .to.be.revertedWithCustomError(registry, "VerdictExpired")
        .withArgs(v.expiresAt);
      await time.increase(1000);
      await expect(registry.submitVerdict(v, sig)).to.be.revertedWithCustomError(registry, "VerdictExpired");
    });

    it("accepts a signature in the last second before expiry", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = await sign(ctx.verifier, registry, v);
      await time.setNextBlockTimestamp(v.expiresAt - 1n);
      await registry.submitVerdict(v, sig);
    });

    it("enforces the validity window (max 30 days, max 15 min clock skew)", async function () {
      const now = BigInt(await time.latest());
      const day = 86400n;
      const bad = [
        { issuedAt: now + 7200n, expiresAt: now + 7200n }, // empty window
        { issuedAt: now + 7200n, expiresAt: now + 3600n }, // inverted
        { issuedAt: now, expiresAt: now + 30n * day + 1n }, // too long
        { issuedAt: now + 3600n, expiresAt: now + 7200n }, // issued in the future
      ];
      for (const o of bad) {
        const v = await makeVerdict(ctx.artist.address, o);
        await expect(registry.submitVerdict(v, await sign(ctx.verifier, registry, v))).to.be.revertedWithCustomError(
          registry,
          "InvalidValidity",
        );
      }
      await submit(ctx, ctx.artist.address, { issuedAt: now, expiresAt: now + 30n * day });
      await submit(ctx, ctx.artist.address, { issuedAt: now + 15n * 60n, expiresAt: now + day });
    });

    it("rejects a signature from a non-verifier", async function () {
      const v = await makeVerdict(ctx.artist.address);
      await expect(registry.submitVerdict(v, await sign(ctx.outsider, registry, v)))
        .to.be.revertedWithCustomError(registry, "UnauthorizedSigner")
        .withArgs(ctx.outsider.address);
    });

    it("rejects a verdict whose fields were changed after signing", async function () {
      const v = await makeVerdict(ctx.artist.address, { decision: Decision.FAIL, score: 10 });
      const sig = await sign(ctx.verifier, registry, v);
      for (const tampered of [
        { ...v, decision: Decision.PASS },
        { ...v, score: 99 },
        { ...v, artist: ctx.outsider.address },
        { ...v, imageHash: h("other image") },
        { ...v, expiresAt: v.expiresAt + 1n },
        { ...v, nonce: v.nonce + 1n },
      ]) {
        await expect(registry.submitVerdict(tampered, sig)).to.be.revertedWithCustomError(
          registry,
          "UnauthorizedSigner",
        );
      }
    });

    it("rejects signatures for another chain or another registry", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const domain = await domainOf(registry);
      const otherChain = await sign(ctx.verifier, registry, v, { ...domain, chainId: 1n });
      const otherContract = await sign(ctx.verifier, registry, v, {
        ...domain,
        verifyingContract: ctx.outsider.address,
      });
      for (const sig of [otherChain, otherContract]) {
        await expect(registry.submitVerdict(v, sig)).to.be.revertedWithCustomError(registry, "UnauthorizedSigner");
      }
    });

    it("rejects malleable (high-s) and malformed signatures", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = ethers.Signature.from(await sign(ctx.verifier, registry, v));
      const highS = ethers.toBeHex(SECP256K1_N - BigInt(sig.s), 32);
      const malleable = ethers.concat([sig.r, highS, ethers.toBeHex(sig.v === 27 ? 28 : 27, 1)]);
      await expect(registry.submitVerdict(v, malleable)).to.be.revertedWithCustomError(
        registry,
        "ECDSAInvalidSignatureS",
      );
      await expect(registry.submitVerdict(v, "0x1234")).to.be.revertedWithCustomError(
        registry,
        "ECDSAInvalidSignatureLength",
      );
      const zero = ethers.concat([ethers.ZeroHash, ethers.ZeroHash, "0x1b"]);
      await expect(registry.submitVerdict(v, zero)).to.be.revertedWithCustomError(registry, "ECDSAInvalidSignature");
    });

    it("rejects replay of the same signed verdict", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = await sign(ctx.verifier, registry, v);
      await registry.submitVerdict(v, sig);
      await expect(registry.submitVerdict(v, sig))
        .to.be.revertedWithCustomError(registry, "NonceAlreadyUsed")
        .withArgs(ctx.verifier.address, v.nonce);
    });

    it("rejects a reused nonce and a duplicate reportHash", async function () {
      const v = await submit(ctx, ctx.artist.address);
      const sameNonce = await makeVerdict(ctx.artist.address, { nonce: v.nonce });
      await expect(
        registry.submitVerdict(sameNonce, await sign(ctx.verifier, registry, sameNonce)),
      ).to.be.revertedWithCustomError(registry, "NonceAlreadyUsed");
      const sameReport = await makeVerdict(ctx.artist.address, { reportHash: v.reportHash });
      await expect(registry.submitVerdict(sameReport, await sign(ctx.verifier, registry, sameReport)))
        .to.be.revertedWithCustomError(registry, "AlreadyRecorded")
        .withArgs(v.reportHash);
    });

    it("nonces are per signer", async function () {
      const { admin, verifier2 } = ctx;
      await registry.connect(admin).grantRole(await registry.VERIFIER_ROLE(), verifier2.address);
      const v = await submit(ctx, ctx.artist.address);
      await submit(ctx, ctx.artist.address, { nonce: v.nonce }, verifier2);
    });

    it("rotating the verifier key invalidates the old key's pending signatures", async function () {
      const { admin, verifier, verifier2 } = ctx;
      const role = await registry.VERIFIER_ROLE();
      const pending = await makeVerdict(ctx.artist.address);
      const pendingSig = await sign(verifier, registry, pending);

      await registry.connect(admin).grantRole(role, verifier2.address);
      await registry.connect(admin).revokeRole(role, verifier.address);

      await expect(registry.submitVerdict(pending, pendingSig))
        .to.be.revertedWithCustomError(registry, "UnauthorizedSigner")
        .withArgs(verifier.address);
      await submit(ctx, ctx.artist.address, {}, verifier2);
    });

    it("cancelNonce kills an issued signature", async function () {
      const v = await makeVerdict(ctx.artist.address);
      const sig = await sign(ctx.verifier, registry, v);
      await expect(registry.connect(ctx.verifier).cancelNonce(v.nonce))
        .to.emit(registry, "NonceCancelled")
        .withArgs(ctx.verifier.address, v.nonce);
      await expect(registry.submitVerdict(v, sig)).to.be.revertedWithCustomError(registry, "NonceAlreadyUsed");
      await expect(registry.connect(ctx.verifier).cancelNonce(v.nonce)).to.be.revertedWithCustomError(
        registry,
        "NonceAlreadyUsed",
      );
    });

    it("is blocked while paused", async function () {
      await registry.connect(ctx.pauser).pause();
      const v = await makeVerdict(ctx.artist.address);
      await expect(registry.submitVerdict(v, await sign(ctx.verifier, registry, v))).to.be.revertedWithCustomError(
        registry,
        "EnforcedPause",
      );
    });
  });

  describe("reviews and voiding", function () {
    it("approving a REVIEW makes it mintable", async function () {
      const v = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      await expect(registry.connect(ctx.reviewer).resolveReview(v.reportHash, true))
        .to.emit(registry, "ReviewResolved")
        .withArgs(v.reportHash, true, ctx.reviewer.address);
      expect((await registry.verdictOf(v.reportHash)).decision).to.equal(Decision.REVIEW_APPROVED);
      expect(await registry.isMintable(v.imageHash, ctx.artist.address)).to.deep.equal([true, v.reportHash]);
    });

    it("rejecting a REVIEW keeps it unmintable", async function () {
      const v = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      await registry.connect(ctx.reviewer).resolveReview(v.reportHash, false);
      expect((await registry.verdictOf(v.reportHash)).decision).to.equal(Decision.REVIEW_REJECTED);
      expect((await registry.isMintable(v.imageHash, ctx.artist.address))[0]).to.be.false;
    });

    it("only REVIEW verdicts can be resolved, only by a reviewer", async function () {
      const pass = await submit(ctx, ctx.artist.address);
      const review = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      for (const rh of [pass.reportHash, h("unknown")]) {
        await expect(registry.connect(ctx.reviewer).resolveReview(rh, true))
          .to.be.revertedWithCustomError(registry, "NotUnderReview")
          .withArgs(rh);
      }
      await expect(registry.connect(ctx.outsider).resolveReview(review.reportHash, true))
        .to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount")
        .withArgs(ctx.outsider.address, await registry.REVIEWER_ROLE());
      await registry.connect(ctx.reviewer).resolveReview(review.reportHash, true);
      await expect(
        registry.connect(ctx.reviewer).resolveReview(review.reportHash, false),
      ).to.be.revertedWithCustomError(registry, "NotUnderReview");
    });

    it("voids PASS, REVIEW and REVIEW_APPROVED verdicts that were not used", async function () {
      const pass = await submit(ctx, ctx.artist.address);
      const review = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      const approved = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      await registry.connect(ctx.reviewer).resolveReview(approved.reportHash, true);

      for (const v of [pass, review, approved]) {
        await expect(registry.connect(ctx.reviewer).voidVerdict(v.reportHash))
          .to.emit(registry, "VerdictVoided")
          .withArgs(v.reportHash, ctx.reviewer.address);
        expect((await registry.verdictOf(v.reportHash)).decision).to.equal(Decision.VOIDED);
        expect(await registry.isMintable(v.imageHash, ctx.artist.address)).to.deep.equal([false, ethers.ZeroHash]);
      }
      // a voided REVIEW cannot be approved afterwards
      await expect(registry.connect(ctx.reviewer).resolveReview(review.reportHash, true)).to.be.revertedWithCustomError(
        registry,
        "NotUnderReview",
      );
    });

    it("cannot void FAIL, REJECTED, unknown, already-voided or used verdicts", async function () {
      const fail = await submit(ctx, ctx.artist.address, { decision: Decision.FAIL });
      const rejected = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      await registry.connect(ctx.reviewer).resolveReview(rejected.reportHash, false);
      const voided = await submit(ctx, ctx.artist.address);
      await registry.connect(ctx.reviewer).voidVerdict(voided.reportHash);
      const { v: used } = await mintPass(ctx, ctx.artist);

      const cases = [
        [fail.reportHash, Decision.FAIL],
        [rejected.reportHash, Decision.REVIEW_REJECTED],
        [h("unknown"), Decision.NONE],
        [voided.reportHash, Decision.VOIDED],
        [used.reportHash, Decision.PASS],
      ];
      for (const [rh, d] of cases) {
        await expect(registry.connect(ctx.reviewer).voidVerdict(rh))
          .to.be.revertedWithCustomError(registry, "CannotVoid")
          .withArgs(rh, d);
      }
      await expect(registry.connect(ctx.outsider).voidVerdict(fail.reportHash)).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
    });

    it("voiding an older PASS keeps the newer one for the same image", async function () {
      const older = await submit(ctx, ctx.artist.address, { tag: "pair-a" });
      const newer = await submit(ctx, ctx.artist.address, { tag: "pair-b", imageHash: older.imageHash });
      await registry.connect(ctx.reviewer).voidVerdict(older.reportHash);
      expect(await registry.isMintable(older.imageHash, ctx.artist.address)).to.deep.equal([true, newer.reportHash]);
    });

    it("a PASS for an image minted by someone else can still be voided", async function () {
      const { v } = await mintPass(ctx, ctx.artist);
      const other = await submit(ctx, ctx.artist2.address, { imageHash: v.imageHash });
      await registry.connect(ctx.reviewer).voidVerdict(other.reportHash);
    });
  });

  describe("consume", function () {
    it("needs COLLECTION_ROLE", async function () {
      const v = await submit(ctx, ctx.artist.address);
      await expect(registry.connect(ctx.artist).consume(v.imageHash, ctx.artist.address, 1))
        .to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount")
        .withArgs(ctx.artist.address, await registry.COLLECTION_ROLE());
    });

    it("records the mint once and refuses a second one", async function () {
      const { admin, outsider, artist } = ctx;
      await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), outsider.address);
      const v = await submit(ctx, artist.address);
      await expect(registry.connect(outsider).consume(v.imageHash, artist.address, 42))
        .to.emit(registry, "ImageMinted")
        .withArgs(v.imageHash, v.reportHash, outsider.address, 42, artist.address);
      expect(await registry.mintOf(v.imageHash)).to.deep.equal([outsider.address, 42n, v.reportHash, 0n]);
      expect(await registry.imageStatus(v.imageHash)).to.equal(ImageStatus.MINTED);
      expect(await registry.totalMints()).to.equal(1);
      expect(await registry.isMintable(v.imageHash, artist.address)).to.deep.equal([false, v.reportHash]);
      await expect(registry.connect(outsider).consume(v.imageHash, artist.address, 43))
        .to.be.revertedWithCustomError(registry, "ImageAlreadyMinted")
        .withArgs(v.imageHash, outsider.address, 42);
    });

    it("refuses without a passing verdict for that artist", async function () {
      const { admin, outsider, artist, artist2 } = ctx;
      await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), outsider.address);
      const v = await submit(ctx, artist.address);
      await expect(registry.connect(outsider).consume(v.imageHash, artist2.address, 1))
        .to.be.revertedWithCustomError(registry, "NotMintable")
        .withArgs(v.imageHash, artist2.address);
    });

    it("is blocked while paused, and isMintable reports false", async function () {
      const { admin, outsider, artist, pauser } = ctx;
      await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), outsider.address);
      const v = await submit(ctx, artist.address);
      await registry.connect(pauser).pause();
      expect(await registry.isMintable(v.imageHash, artist.address)).to.deep.equal([false, v.reportHash]);
      await expect(registry.connect(outsider).consume(v.imageHash, artist.address, 1)).to.be.revertedWithCustomError(
        registry,
        "EnforcedPause",
      );
      await registry.connect(admin).unpause();
      expect(await registry.isMintable(v.imageHash, artist.address)).to.deep.equal([true, v.reportHash]);
    });
  });

  describe("disputes", function () {
    let minted;

    beforeEach(async function () {
      minted = await mintPass(ctx, ctx.artist);
    });

    async function open(disputant = ctx.outsider, value = BOND, uri = "ipfs://evidence") {
      return registry.connect(disputant).openDispute(minted.v.imageHash, uri, { value });
    }

    it("opens a dispute with the bond", async function () {
      await expect(open())
        .to.emit(registry, "DisputeOpened")
        .withArgs(1, minted.v.imageHash, ctx.outsider.address, BOND, "ipfs://evidence");
      const d = await registry.disputeOf(1);
      expect(d.imageHash).to.equal(minted.v.imageHash);
      expect(d.disputant).to.equal(ctx.outsider.address);
      expect(d.bond).to.equal(BOND);
      expect(d.status).to.equal(DisputeStatus.OPEN);
      expect(d.openedAt).to.equal(await time.latest());
      expect(d.resolvedAt).to.equal(0);
      expect(d.evidenceURI).to.equal("ipfs://evidence");
      expect(await registry.imageStatus(minted.v.imageHash)).to.equal(ImageStatus.DISPUTED);
      expect((await registry.mintOf(minted.v.imageHash))[3]).to.equal(1);
      expect(await ethers.provider.getBalance(await registry.getAddress())).to.equal(BOND);
      expect(await registry.totalDisputes()).to.equal(1);
    });

    it("validates image, bond, evidence and one open dispute per image", async function () {
      await expect(registry.openDispute(h("never minted"), "ipfs://e", { value: BOND }))
        .to.be.revertedWithCustomError(registry, "NotMinted")
        .withArgs(h("never minted"));
      await expect(open(ctx.outsider, BOND - 1n))
        .to.be.revertedWithCustomError(registry, "WrongBond")
        .withArgs(BOND);
      await expect(open(ctx.outsider, BOND + 1n)).to.be.revertedWithCustomError(registry, "WrongBond");
      await expect(open(ctx.outsider, BOND, "")).to.be.revertedWithCustomError(registry, "EmptyEvidence");
      await open();
      await expect(open(ctx.artist2)).to.be.revertedWithCustomError(registry, "DisputeAlreadyOpen").withArgs(1);
    });

    it("upheld: image revoked for good, bond back, artist strike", async function () {
      await open();
      await expect(registry.connect(ctx.reviewer).resolveDispute(1, true))
        .to.emit(registry, "ImageRevoked")
        .withArgs(minted.v.imageHash, ctx.artist.address, 1)
        .and.to.emit(registry, "DisputeResolved")
        .withArgs(1, minted.v.imageHash, true, ctx.reviewer.address);

      expect(await registry.isRevoked(minted.v.imageHash)).to.be.true;
      expect(await registry.imageStatus(minted.v.imageHash)).to.equal(ImageStatus.REVOKED);
      expect(await registry.strikes(ctx.artist.address)).to.equal(1);
      expect(await registry.credits(ctx.outsider.address)).to.equal(BOND);
      const d = await registry.disputeOf(1);
      expect(d.status).to.equal(DisputeStatus.UPHELD);
      expect(d.resolvedAt).to.equal(await time.latest());

      const tx = registry.connect(ctx.outsider).withdraw();
      await expect(tx).to.changeEtherBalances([ctx.outsider, registry], [BOND, -BOND]);
      await expect(tx).to.emit(registry, "Withdrawal").withArgs(ctx.outsider.address, BOND);
      expect(await registry.credits(ctx.outsider.address)).to.equal(0);

      await expect(open(ctx.artist2))
        .to.be.revertedWithCustomError(registry, "AlreadyRevoked")
        .withArgs(minted.v.imageHash);
    });

    it("rejected: bond to treasury, image back to MINTED, can be disputed again", async function () {
      await open();
      await registry.connect(ctx.reviewer).resolveDispute(1, false);
      expect((await registry.disputeOf(1)).status).to.equal(DisputeStatus.REJECTED);
      expect(await registry.imageStatus(minted.v.imageHash)).to.equal(ImageStatus.MINTED);
      expect(await registry.isRevoked(minted.v.imageHash)).to.be.false;
      expect(await registry.strikes(ctx.artist.address)).to.equal(0);
      expect(await registry.credits(ctx.treasury.address)).to.equal(BOND);
      expect(await registry.credits(ctx.outsider.address)).to.equal(0);
      await expect(registry.connect(ctx.treasury).withdraw()).to.changeEtherBalance(ctx.treasury, BOND);

      await open(ctx.artist2);
      expect((await registry.mintOf(minted.v.imageHash))[3]).to.equal(2);
    });

    it("only open disputes can be resolved, only by a reviewer", async function () {
      await expect(registry.connect(ctx.reviewer).resolveDispute(1, true))
        .to.be.revertedWithCustomError(registry, "DisputeNotOpen")
        .withArgs(1);
      await open();
      await expect(registry.connect(ctx.outsider).resolveDispute(1, true)).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
      await registry.connect(ctx.reviewer).resolveDispute(1, false);
      await expect(registry.connect(ctx.reviewer).resolveDispute(1, true)).to.be.revertedWithCustomError(
        registry,
        "DisputeNotOpen",
      );
    });

    it("snapshots the bond: a later bond change does not affect open disputes", async function () {
      await open();
      await registry.connect(ctx.admin).setDisputeBond(BOND * 5n);
      await registry.connect(ctx.reviewer).resolveDispute(1, true);
      expect(await registry.credits(ctx.outsider.address)).to.equal(BOND);
      await expect(open(ctx.artist2, BOND)).to.be.revertedWithCustomError(registry, "AlreadyRevoked");
    });

    it("works with a zero bond", async function () {
      await registry.connect(ctx.admin).setDisputeBond(0);
      await open(ctx.outsider, 0n);
      await registry.connect(ctx.reviewer).resolveDispute(1, true);
      await expect(registry.connect(ctx.outsider).withdraw()).to.be.revertedWithCustomError(
        registry,
        "NothingToWithdraw",
      );
    });

    it("works while paused (incident clean-up)", async function () {
      await registry.connect(ctx.pauser).pause();
      await open();
      await registry.connect(ctx.reviewer).resolveDispute(1, true);
      await registry.connect(ctx.outsider).withdraw();
    });

    it("a disputant that rejects ETH cannot block resolution", async function () {
      const rejecter = await (await ethers.getContractFactory("EtherRejecter")).deploy(await registry.getAddress());
      await rejecter.openDispute(minted.v.imageHash, { value: BOND });
      await registry.connect(ctx.reviewer).resolveDispute(1, true);
      expect(await registry.isRevoked(minted.v.imageHash)).to.be.true;
      await expect(rejecter.withdraw()).to.be.revertedWithCustomError(registry, "FailedCall");
      expect(await registry.credits(await rejecter.getAddress())).to.equal(BOND);
    });

    it("withdraw with no credit reverts", async function () {
      await expect(registry.connect(ctx.outsider).withdraw()).to.be.revertedWithCustomError(
        registry,
        "NothingToWithdraw",
      );
    });
  });

  describe("governance", function () {
    it("pauser pauses, only admin unpauses", async function () {
      const { pauser, admin, outsider } = ctx;
      await expect(registry.connect(outsider).pause()).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
      await expect(registry.connect(pauser).pause()).to.emit(registry, "Paused").withArgs(pauser.address);
      await expect(registry.connect(pauser).unpause()).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
      await expect(registry.connect(admin).unpause()).to.emit(registry, "Unpaused");
      expect(await registry.paused()).to.be.false;
    });

    it("admin sets bond and treasury", async function () {
      const { admin, outsider } = ctx;
      await expect(registry.connect(admin).setDisputeBond(123)).to.emit(registry, "DisputeBondChanged").withArgs(123);
      expect(await registry.disputeBond()).to.equal(123);
      await expect(registry.connect(admin).setDisputeBond(2n ** 96n)).to.be.revertedWithCustomError(
        registry,
        "SafeCastOverflowedUintDowncast",
      );
      await expect(registry.connect(admin).setTreasury(outsider.address))
        .to.emit(registry, "TreasuryChanged")
        .withArgs(outsider.address);
      expect(await registry.treasury()).to.equal(outsider.address);
      await expect(registry.connect(admin).setTreasury(ethers.ZeroAddress)).to.be.revertedWithCustomError(
        registry,
        "ZeroAddress",
      );
      for (const call of [
        () => registry.connect(outsider).setDisputeBond(1),
        () => registry.connect(outsider).setTreasury(outsider.address),
      ]) {
        await expect(call()).to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount");
      }
    });

    it("admin transfer is two-step with a delay (multisig hand-over)", async function () {
      const { admin, outsider } = ctx;
      const safe = outsider; // stands in for a Safe multisig
      await expect(
        registry.connect(admin).grantRole(await registry.DEFAULT_ADMIN_ROLE(), safe.address),
      ).to.be.revertedWithCustomError(registry, "AccessControlEnforcedDefaultAdminRules");

      await registry.connect(admin).beginDefaultAdminTransfer(safe.address);
      await expect(registry.connect(safe).acceptDefaultAdminTransfer()).to.be.revertedWithCustomError(
        registry,
        "AccessControlEnforcedDefaultAdminDelay",
      );
      await time.increase(ADMIN_DELAY + 1);
      await registry.connect(safe).acceptDefaultAdminTransfer();
      expect(await registry.defaultAdmin()).to.equal(safe.address);
      await expect(registry.connect(admin).setDisputeBond(1)).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
      await registry.connect(safe).setDisputeBond(1);
    });
  });

  describe("verifier key compromise playbook", function () {
    it("pause → rotate → void unused → dispute-revoke minted → unpause", async function () {
      const { admin, verifier, verifier2, pauser, reviewer, outsider: thief, nft } = ctx;
      // the thief signs PASS verdicts for themselves with the leaked verifier key
      const minted = await submit(ctx, thief.address, {}, verifier);
      await nft.connect(thief).mint(minted.imageHash, "ipfs://stolen", 1000);
      const unused = await submit(ctx, thief.address, {}, verifier);

      await registry.connect(pauser).pause();
      const role = await registry.VERIFIER_ROLE();
      await registry.connect(admin).revokeRole(role, verifier.address);
      await registry.connect(admin).grantRole(role, verifier2.address);
      await registry.connect(reviewer).voidVerdict(unused.reportHash);
      await registry.connect(reviewer).openDispute(minted.imageHash, "ipfs://incident", { value: BOND });
      await registry.connect(reviewer).resolveDispute(1, true);
      await registry.connect(admin).unpause();

      await expect(nft.connect(thief).mint(unused.imageHash, "ipfs://x", 0)).to.be.revertedWithCustomError(
        registry,
        "NotMintable",
      );
      expect(await nft.isRevoked(1)).to.be.true;
      await submit(ctx, ctx.artist.address, {}, verifier2); // the new key works
    });
  });
});
