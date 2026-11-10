const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");

const sha = (s) => ethers.sha256(ethers.toUtf8Bytes(s));

describe("PolicyRegistry", function () {
  async function fixture() {
    const [owner, outsider, next] = await ethers.getSigners();
    const P = await ethers.getContractFactory("PolicyRegistry");
    const reg = await P.deploy(
      owner.address,
      [sha("policy-5.1.0"), sha("policy-5.1.0-permissive")],
      ["5.1.0", "5.1.0-permissive"],
      ["ipfs://a", "ipfs://b"],
    );
    return { reg, P, owner, outsider, next };
  }

  it("announces the constructor policies in order", async function () {
    const { reg, owner } = await loadFixture(fixture);
    expect(await reg.owner()).to.equal(owner.address);
    expect(await reg.policyCount()).to.equal(2);
    const p0 = await reg.policyAt(0);
    expect(p0.policyHash).to.equal(sha("policy-5.1.0"));
    expect(p0.version).to.equal("5.1.0");
    expect(p0.uri).to.equal("ipfs://a");
    expect((await reg.current()).version).to.equal("5.1.0-permissive");
    const [known, at] = await reg.isRegistered(sha("policy-5.1.0"));
    expect(known).to.equal(true);
    expect(at).to.equal(p0.activatedAt);
    expect(await reg.isRegistered(sha("nope"))).to.deep.equal([false, 0n]);
  });

  it("emits PolicySet from the constructor (indexers see the initial policies)", async function () {
    const { P, owner } = await loadFixture(fixture);
    const reg = await P.deploy(owner.address, [sha("x")], ["1"], ["ipfs://x"]);
    await expect(reg.deploymentTransaction()).to.emit(reg, "PolicySet").withArgs(0, sha("x"), "1", "ipfs://x");
  });

  it("only the owner sets a policy; it becomes current", async function () {
    const { reg, owner, outsider } = await loadFixture(fixture);
    await expect(reg.connect(outsider).setPolicy(sha("p6"), "6.0.0", "ipfs://c"))
      .to.be.revertedWithCustomError(reg, "OwnableUnauthorizedAccount")
      .withArgs(outsider.address);
    await expect(reg.connect(owner).setPolicy(sha("p6"), "6.0.0", "ipfs://c"))
      .to.emit(reg, "PolicySet")
      .withArgs(2, sha("p6"), "6.0.0", "ipfs://c");
    expect((await reg.current()).policyHash).to.equal(sha("p6"));
  });

  it("rejects empty fields and mismatched constructor arrays", async function () {
    const { reg, P, owner } = await loadFixture(fixture);
    await expect(reg.setPolicy(ethers.ZeroHash, "1", "ipfs://x")).to.be.revertedWithCustomError(reg, "EmptyField");
    await expect(reg.setPolicy(sha("a"), "", "ipfs://x")).to.be.revertedWithCustomError(reg, "EmptyField");
    await expect(reg.setPolicy(sha("a"), "1", "")).to.be.revertedWithCustomError(reg, "EmptyField");
    await expect(P.deploy(owner.address, [sha("a")], [], ["u"])).to.be.revertedWithCustomError(P, "LengthMismatch");
    await expect(P.deploy(owner.address, [sha("a")], ["1"], [])).to.be.revertedWithCustomError(P, "LengthMismatch");
  });

  it("an empty registry has no current or active policy", async function () {
    const { P, owner } = await loadFixture(fixture);
    const reg = await P.deploy(owner.address, [], [], []);
    await expect(reg.current()).to.be.revertedWithCustomError(reg, "NoPolicy");
    await expect(reg.policyActiveAt(2n ** 63n)).to.be.revertedWithCustomError(reg, "NoPolicy");
    await expect(reg.policyAt(0)).to.be.revertedWithCustomError(reg, "IndexOutOfRange").withArgs(0);
  });

  it("policyActiveAt returns the policy in force at a verdict's issuedAt", async function () {
    const { reg } = await loadFixture(fixture);
    const t0 = (await reg.policyAt(1)).activatedAt;
    await time.increase(1000);
    await reg.setPolicy(sha("p6"), "6.0.0", "ipfs://c");
    const t1 = (await reg.policyAt(2)).activatedAt;
    expect((await reg.policyActiveAt(t0)).version).to.equal("5.1.0-permissive");
    expect((await reg.policyActiveAt(t1 - 1n)).version).to.equal("5.1.0-permissive");
    expect((await reg.policyActiveAt(t1)).version).to.equal("6.0.0");
    await expect(reg.policyActiveAt(t0 - 1n)).to.be.revertedWithCustomError(reg, "NoPolicy");
  });

  it("a rollback re-announces an older hash as a new entry", async function () {
    const { reg } = await loadFixture(fixture);
    await reg.setPolicy(sha("p6"), "6.0.0", "ipfs://c");
    await time.increase(10);
    await reg.setPolicy(sha("policy-5.1.0"), "5.1.0", "ipfs://a");
    expect(await reg.policyCount()).to.equal(4);
    const [, at] = await reg.isRegistered(sha("policy-5.1.0"));
    expect(at).to.equal((await reg.policyAt(3)).activatedAt);
    expect((await reg.current()).version).to.equal("5.1.0");
  });

  it("ownership moves in two steps (Ownable2Step)", async function () {
    const { reg, owner, next } = await loadFixture(fixture);
    await reg.connect(owner).transferOwnership(next.address);
    expect(await reg.owner()).to.equal(owner.address);
    await reg.connect(next).acceptOwnership();
    expect(await reg.owner()).to.equal(next.address);
  });
});
