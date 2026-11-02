const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const { Decision, BOND, h, makeVerdict, sign, deployFixture, submit, mintPass } = require("./helpers");

describe("MintGateNFTV2", function () {
  let ctx, registry, nft;

  beforeEach(async function () {
    ctx = await loadFixture(deployFixture);
    ({ registry, nft } = ctx);
  });

  describe("deployment", function () {
    it("points at the registry and has no owner", async function () {
      expect(await nft.mintGate()).to.equal(await registry.getAddress());
      expect(await nft.name()).to.equal("MintGate Verified Art v2");
      expect(await nft.symbol()).to.equal("MGVA2");
      expect(await nft.totalMinted()).to.equal(0);
      expect(nft.interface.getFunction("owner", [])).to.be.null;
    });

    it("refuses a registry that is not an IMintGate", async function () {
      const F = await ethers.getContractFactory("MintGateNFTV2");
      await expect(F.deploy(ctx.outsider.address))
        .to.be.revertedWithCustomError(F, "InvalidMintGate")
        .withArgs(ctx.outsider.address);
      await expect(F.deploy(await nft.getAddress())).to.be.revertedWithCustomError(F, "InvalidMintGate");
    });

    it("supports ERC-721, ERC-2981, ERC-4906 and ERC-165", async function () {
      for (const id of ["0x80ac58cd", "0x5b5e139f", "0x2a55205a", "0x49064906", "0x01ffc9a7"]) {
        expect(await nft.supportsInterface(id), id).to.be.true;
      }
      expect(await nft.supportsInterface("0xffffffff")).to.be.false;
    });
  });

  describe("artist self-mint", function () {
    it("mints with a verdict already on chain", async function () {
      const { artist } = ctx;
      const v = await submit(ctx, artist.address);
      await expect(nft.connect(artist).mint(v.imageHash, "ipfs://meta", 750))
        .to.emit(nft, "Transfer")
        .withArgs(ethers.ZeroAddress, artist.address, 1)
        .and.to.emit(nft, "VerifiedMint")
        .withArgs(1, artist.address, v.imageHash, 750)
        .and.to.emit(registry, "ImageMinted")
        .withArgs(v.imageHash, v.reportHash, await nft.getAddress(), 1, artist.address);

      expect(await nft.ownerOf(1)).to.equal(artist.address);
      expect(await nft.tokenURI(1)).to.equal("ipfs://meta");
      expect(await nft.originalTokenURI(1)).to.equal("ipfs://meta");
      expect(await nft.imageOfToken(1)).to.equal(v.imageHash);
      expect(await nft.tokenOfImage(v.imageHash)).to.equal(1);
      expect(await nft.totalMinted()).to.equal(1);
      expect(await nft.isRevoked(1)).to.be.false;
      expect(await nft.royaltyInfo(1, 10_000)).to.deep.equal([artist.address, 750n]);
    });

    it("submitAndMint: backend-signed verdict, artist submits and mints in one tx", async function () {
      const { artist, verifier } = ctx;
      const v = await makeVerdict(artist.address);
      const sig = await sign(verifier, registry, v);
      await expect(nft.connect(artist).submitAndMint(v, sig, "ipfs://meta", 0))
        .to.emit(registry, "VerdictSubmitted")
        .and.to.emit(registry, "ImageMinted");
      expect(await nft.ownerOf(1)).to.equal(artist.address);
      expect((await registry.verdictOf(v.reportHash)).decision).to.equal(Decision.PASS);
      expect(await nft.royaltyInfo(1, 10_000)).to.deep.equal([artist.address, 0n]);
    });

    it("submitAndMint still works if someone front-ran the submission", async function () {
      const { artist, verifier, outsider } = ctx;
      const v = await makeVerdict(artist.address);
      const sig = await sign(verifier, registry, v);
      await registry.connect(outsider).submitVerdict(v, sig); // copied from the mempool
      await expect(nft.connect(artist).submitAndMint(v, sig, "ipfs://meta", 0)).not.to.emit(
        registry,
        "VerdictSubmitted",
      );
      expect(await nft.ownerOf(1)).to.equal(artist.address);
    });

    it("only the verdict's artist can use it", async function () {
      const { artist, verifier, outsider } = ctx;
      const v = await makeVerdict(artist.address);
      const sig = await sign(verifier, registry, v);
      await expect(nft.connect(outsider).submitAndMint(v, sig, "ipfs://meta", 0))
        .to.be.revertedWithCustomError(nft, "ArtistMismatch")
        .withArgs(artist.address);
      await registry.submitVerdict(v, sig);
      await expect(nft.connect(outsider).mint(v.imageHash, "ipfs://meta", 0))
        .to.be.revertedWithCustomError(registry, "NotMintable")
        .withArgs(v.imageHash, outsider.address);
    });

    it("forged, expired and replayed signatures revert", async function () {
      const { artist, verifier, outsider } = ctx;
      const forged = await makeVerdict(artist.address);
      await expect(
        nft.connect(artist).submitAndMint(forged, await sign(outsider, registry, forged), "ipfs://m", 0),
      ).to.be.revertedWithCustomError(registry, "UnauthorizedSigner");

      const expired = await makeVerdict(artist.address);
      const expiredSig = await sign(verifier, registry, expired);
      await time.increaseTo(expired.expiresAt);
      await expect(nft.connect(artist).submitAndMint(expired, expiredSig, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "VerdictExpired",
      );

      const v = await makeVerdict(artist.address);
      const sig = await sign(verifier, registry, v);
      await nft.connect(artist).submitAndMint(v, sig, "ipfs://m", 0);
      // replay: the verdict is already recorded and already used
      await expect(nft.connect(artist).submitAndMint(v, sig, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "ImageAlreadyMinted",
      );
      expect(await nft.totalMinted()).to.equal(1);
    });

    it("refuses without a passing verdict", async function () {
      const { artist } = ctx;
      await expect(nft.connect(artist).mint(h("nothing"), "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "NotMintable",
      );
      for (const decision of [Decision.FAIL, Decision.REVIEW]) {
        const v = await submit(ctx, artist.address, { decision });
        await expect(nft.connect(artist).mint(v.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
          registry,
          "NotMintable",
        );
      }
      const voided = await submit(ctx, artist.address);
      await registry.connect(ctx.reviewer).voidVerdict(voided.reportHash);
      await expect(nft.connect(artist).mint(voided.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "NotMintable",
      );
    });

    it("mints a reviewer-approved REVIEW verdict", async function () {
      const v = await submit(ctx, ctx.artist.address, { decision: Decision.REVIEW });
      await registry.connect(ctx.reviewer).resolveReview(v.reportHash, true);
      await nft.connect(ctx.artist).mint(v.imageHash, "ipfs://m", 0);
      expect(await nft.ownerOf(1)).to.equal(ctx.artist.address);
    });

    it("one token per image, even with a second passing verdict", async function () {
      const { artist, artist2 } = ctx;
      const { v } = await mintPass(ctx, artist);
      await submit(ctx, artist.address, { imageHash: v.imageHash });
      await expect(nft.connect(artist).mint(v.imageHash, "ipfs://m", 0))
        .to.be.revertedWithCustomError(registry, "ImageAlreadyMinted")
        .withArgs(v.imageHash, await nft.getAddress(), 1);
      await submit(ctx, artist2.address, { imageHash: v.imageHash });
      await expect(nft.connect(artist2).mint(v.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "ImageAlreadyMinted",
      );
    });

    it("validates URI and royalty", async function () {
      const v = await submit(ctx, ctx.artist.address);
      await expect(nft.connect(ctx.artist).mint(v.imageHash, "", 0)).to.be.revertedWithCustomError(nft, "EmptyURI");
      await expect(nft.connect(ctx.artist).mint(v.imageHash, "ipfs://m", 1001))
        .to.be.revertedWithCustomError(nft, "RoyaltyTooHigh")
        .withArgs(1001);
      await nft.connect(ctx.artist).mint(v.imageHash, "ipfs://m", 1000);
      expect(await nft.royaltyInfo(1, 1000)).to.deep.equal([ctx.artist.address, 100n]);
    });

    it("is blocked while the registry is paused", async function () {
      const v = await submit(ctx, ctx.artist.address);
      await registry.connect(ctx.pauser).pause();
      await expect(nft.connect(ctx.artist).mint(v.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "EnforcedPause",
      );
    });

    it("cannot mint if the registry revoked its COLLECTION_ROLE", async function () {
      const v = await submit(ctx, ctx.artist.address);
      await registry.connect(ctx.admin).revokeRole(await registry.COLLECTION_ROLE(), await nft.getAddress());
      await expect(nft.connect(ctx.artist).mint(v.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
    });

    it("nonexistent tokens revert", async function () {
      for (const fn of ["tokenURI", "originalTokenURI", "isRevoked", "refreshMetadata"]) {
        await expect(nft[fn](99)).to.be.revertedWithCustomError(nft, "ERC721NonexistentToken").withArgs(99);
      }
      expect(await nft.royaltyInfo(99, 1000)).to.deep.equal([ethers.ZeroAddress, 0n]);
    });
  });

  describe("contract artists and re-entrancy", function () {
    let art;

    beforeEach(async function () {
      art = await (await ethers.getContractFactory("ReentrantArtist")).deploy(await nft.getAddress());
    });

    it("a contract wallet can mint and submitAndMint", async function () {
      const a = await art.getAddress();
      const v1 = await submit(ctx, a);
      await art.mint(v1.imageHash);
      const v2 = await makeVerdict(a);
      await art.submitAndMint(v2, await sign(ctx.verifier, registry, v2));
      expect(await nft.balanceOf(a)).to.equal(2);
    });

    it("a receiver that refuses the token reverts the whole mint", async function () {
      const v = await submit(ctx, await art.getAddress());
      await art.setMode(2, ethers.ZeroHash); // REJECT
      await expect(art.mint(v.imageHash))
        .to.be.revertedWithCustomError(nft, "ERC721InvalidReceiver")
        .withArgs(await art.getAddress());
      expect((await registry.mintOf(v.imageHash))[0]).to.equal(ethers.ZeroAddress);
      expect(await nft.totalMinted()).to.equal(0);
    });

    it("re-entering mint with another passing image works and sees consistent state", async function () {
      const a = await art.getAddress();
      const first = await submit(ctx, a);
      const second = await submit(ctx, a);
      await art.setMode(1, second.imageHash); // REENTER
      await art.mint(first.imageHash);
      expect(await art.reentered()).to.equal(2);
      expect(await nft.tokenOfImage(first.imageHash)).to.equal(1);
      expect(await nft.tokenOfImage(second.imageHash)).to.equal(2);
      expect(await registry.totalMints()).to.equal(2);
    });

    it("re-entering mint with the same image reverts everything", async function () {
      const v = await submit(ctx, await art.getAddress());
      await art.setMode(1, v.imageHash);
      await expect(art.mint(v.imageHash)).to.be.revertedWithCustomError(registry, "ImageAlreadyMinted");
      expect(await nft.totalMinted()).to.equal(0);
    });
  });

  describe("third-party collections (MintGated)", function () {
    it("share the one-mint-per-image rule with MintGateNFTV2", async function () {
      const { admin, artist, artist2 } = ctx;
      const tp = await (await ethers.getContractFactory("ThirdPartyCollection")).deploy(await registry.getAddress());
      const a = await submit(ctx, artist.address);
      await expect(tp.connect(artist).mint(a.imageHash)).to.be.revertedWithCustomError(
        registry,
        "AccessControlUnauthorizedAccount",
      );
      await registry.connect(admin).grantRole(await registry.COLLECTION_ROLE(), await tp.getAddress());
      await tp.connect(artist).mint(a.imageHash);
      expect(await tp.ownerOf(1)).to.equal(artist.address);
      expect((await registry.mintOf(a.imageHash))[0]).to.equal(await tp.getAddress());
      await expect(nft.connect(artist).mint(a.imageHash, "ipfs://m", 0)).to.be.revertedWithCustomError(
        registry,
        "ImageAlreadyMinted",
      );
      // and the other way round
      const { v: b } = await mintPass(ctx, artist2);
      await expect(tp.connect(artist2).mint(b.imageHash)).to.be.revertedWithCustomError(registry, "ImageAlreadyMinted");
    });
  });

  describe("revocation (dispute flow end to end)", function () {
    it("upheld dispute: token flagged, metadata swapped, no royalty, still transferable", async function () {
      const { artist, outsider, reviewer, artist2 } = ctx;
      const { v } = await mintPass(ctx, artist);
      await expect(nft.refreshMetadata(1)).to.be.revertedWithCustomError(nft, "NotRevoked").withArgs(1);

      await registry.connect(outsider).openDispute(v.imageHash, "ipfs://evidence", { value: BOND });
      expect(await nft.isRevoked(1)).to.be.false; // a dispute alone does not flag the token
      await registry.connect(reviewer).resolveDispute(1, true);

      expect(await nft.isRevoked(1)).to.be.true;
      expect(await nft.tokenURI(1)).to.equal(await nft.REVOKED_URI());
      expect(await nft.originalTokenURI(1)).to.equal("ipfs://meta");
      expect(await nft.royaltyInfo(1, 10_000)).to.deep.equal([ethers.ZeroAddress, 0n]);
      await expect(nft.refreshMetadata(1)).to.emit(nft, "MetadataUpdate").withArgs(1);

      const meta = JSON.parse((await nft.REVOKED_URI()).replace("data:application/json;utf8,", ""));
      expect(meta.attributes[0]).to.deep.equal({ trait_type: "status", value: "revoked" });

      await nft.connect(artist).transferFrom(artist.address, artist2.address, 1);
      expect(await nft.ownerOf(1)).to.equal(artist2.address);
      expect(await nft.isRevoked(1)).to.be.true; // revoked stays revoked
    });

    it("rejected dispute: token unaffected", async function () {
      const { v } = await mintPass(ctx, ctx.artist);
      await registry.connect(ctx.outsider).openDispute(v.imageHash, "ipfs://evidence", { value: BOND });
      await registry.connect(ctx.reviewer).resolveDispute(1, false);
      expect(await nft.isRevoked(1)).to.be.false;
      expect(await nft.tokenURI(1)).to.equal("ipfs://meta");
    });
  });
});
