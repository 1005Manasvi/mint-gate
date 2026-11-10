// Governance with the real Safe v1.4.1 bytecode (vendor/safe-1.4.1): a 2-of-3 Safe holds the registry
// admin + pauser roles and is the only proposer of the timelock that owns the PolicyRegistry.
const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const { ADMIN_DELAY, BOND, deployFixture, mintPass, submit } = require("./helpers");
const S = require("../../scripts/lib/safe");

const TIMELOCK_DELAY = 3600;
const sha = (s) => ethers.sha256(ethers.toUtf8Bytes(s));

describe("Governance (Safe 2-of-3 + timelock)", function () {
  async function fixture() {
    const ctx = await deployFixture();
    const signers = await ethers.getSigners();
    const [o1, o2, o3, stranger] = signers.slice(10, 14);
    const { chainId } = await ethers.provider.getNetwork();
    const addrs = await S.deploySingletons(ctx.admin);
    const { address } = await S.createSafe(ctx.admin, addrs, [o1.address, o2.address, o3.address], 2);
    const safe = S.safeContract(address, ctx.admin);

    const timelock = await (
      await ethers.getContractFactory("MintGateTimelock")
    ).deploy(TIMELOCK_DELAY, [address], [ethers.ZeroAddress]);
    const policies = await (
      await ethers.getContractFactory("PolicyRegistry")
    ).deploy(await timelock.getAddress(), [sha("5.1.0")], ["5.1.0"], ["ipfs://p5"]);
    const resolver = await (await ethers.getContractFactory("DisputeResolver")).deploy(await ctx.registry.getAddress());

    // hand-over, as scripts/handover.js does it (the deployer is still admin here)
    const r = ctx.registry.connect(ctx.admin);
    await r.grantRole(await r.REVIEWER_ROLE(), await resolver.getAddress());
    await r.grantRole(await r.PAUSER_ROLE(), address);
    await r.revokeRole(await r.PAUSER_ROLE(), ctx.pauser.address);
    await r.setTreasury(address);
    await r.beginDefaultAdminTransfer(address);

    /** Execute `data` on `to` from the Safe with the given owners' signatures. */
    async function viaSafe(to, data, owners, sender = stranger) {
      const tx = S.safeTx(to, data, await safe.nonce());
      const sigs = [];
      for (const o of owners) sigs.push(await S.signSafeTx(o, chainId, address, tx));
      return S.execSafeTx(safe.connect(sender), tx, sigs);
    }
    return { ...ctx, o1, o2, o3, stranger, chainId, safe, safeAddress: address, timelock, policies, resolver, viaSafe };
  }

  const call = (c, fn, args = []) => c.interface.encodeFunctionData(fn, args);

  it("the Safe is a real 2-of-3 Safe v1.4.1", async function () {
    const { safe, o1, o2, o3 } = await loadFixture(fixture);
    expect(await safe.VERSION()).to.equal("1.4.1");
    expect(await safe.getThreshold()).to.equal(2);
    expect(await safe.getOwners()).to.deep.equal([o1.address, o2.address, o3.address]);
  });

  it("our EIP-712 SafeTx hash equals the Safe's own getTransactionHash", async function () {
    const { safe, safeAddress, chainId, registry } = await loadFixture(fixture);
    const tx = S.safeTx(await registry.getAddress(), call(registry, "acceptDefaultAdminTransfer"), 7);
    const onChain = await safe.getTransactionHash(
      tx.to,
      tx.value,
      tx.data,
      tx.operation,
      tx.safeTxGas,
      tx.baseGas,
      tx.gasPrice,
      tx.gasToken,
      tx.refundReceiver,
      tx.nonce,
    );
    expect(S.hashSafeTx(chainId, safeAddress, tx)).to.equal(onChain);
  });

  it("accepting admin needs two owner signatures and the admin delay", async function () {
    const { registry, viaSafe, o1, o2, safeAddress, admin } = await loadFixture(fixture);
    const accept = call(registry, "acceptDefaultAdminTransfer");
    await expect(viaSafe(await registry.getAddress(), accept, [o1])).to.be.reverted; // GS020: 1 of 2
    // two signatures but before the delay: the inner call fails, so the Safe reverts (safeTxGas = 0)
    await expect(viaSafe(await registry.getAddress(), accept, [o1, o2])).to.be.reverted;
    await time.increase(ADMIN_DELAY + 1);
    await viaSafe(await registry.getAddress(), accept, [o1, o2]);
    expect(await registry.defaultAdmin()).to.equal(safeAddress);
    expect(await registry.hasRole(await registry.DEFAULT_ADMIN_ROLE(), admin.address)).to.equal(false);
  });

  it("a signature of a non-owner or a duplicated signature does not count", async function () {
    const { registry, safe, safeAddress, chainId, o1, stranger } = await loadFixture(fixture);
    await time.increase(ADMIN_DELAY + 1);
    const tx = S.safeTx(await registry.getAddress(), call(registry, "acceptDefaultAdminTransfer"), await safe.nonce());
    const own = await S.signSafeTx(o1, chainId, safeAddress, tx);
    const foreign = await S.signSafeTx(stranger, chainId, safeAddress, tx);
    await expect(S.execSafeTx(safe.connect(stranger), tx, [own, foreign])).to.be.reverted; // GS026
    await expect(S.execSafeTx(safe.connect(stranger), tx, [own, own])).to.be.reverted; // GS026 (not ascending)
  });

  describe("after the hand-over", function () {
    async function handedOver() {
      const ctx = await fixture();
      await time.increase(ADMIN_DELAY + 1);
      await ctx.viaSafe(await ctx.registry.getAddress(), call(ctx.registry, "acceptDefaultAdminTransfer"), [
        ctx.o1,
        ctx.o3,
      ]);
      return ctx;
    }

    it("a leaked deployer key can no longer change roles, parameters, pause state or the policy", async function () {
      const { registry, policies, timelock, admin, pauser, stranger } = await loadFixture(handedOver);
      const role = await registry.VERIFIER_ROLE();
      for (const attempt of [
        () => registry.connect(admin).grantRole(role, stranger.address),
        () => registry.connect(admin).revokeRole(role, admin.address),
        () => registry.connect(admin).setDisputeBond(0),
        () => registry.connect(admin).setTreasury(admin.address),
        () => registry.connect(admin).unpause(),
        () => registry.connect(admin).beginDefaultAdminTransfer(admin.address),
        () => registry.connect(pauser).pause(),
      ]) {
        await expect(attempt()).to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount");
      }
      await expect(policies.connect(admin).setPolicy(sha("evil"), "6", "ipfs://evil")).to.be.revertedWithCustomError(
        policies,
        "OwnableUnauthorizedAccount",
      );
      const data = call(policies, "setPolicy", [sha("evil"), "6", "ipfs://evil"]);
      await expect(
        timelock
          .connect(admin)
          .schedule(await policies.getAddress(), 0, data, ethers.ZeroHash, ethers.ZeroHash, TIMELOCK_DELAY),
      ).to.be.revertedWithCustomError(timelock, "AccessControlUnauthorizedAccount");
    });

    it("a single Safe owner key cannot act alone either", async function () {
      const { registry, viaSafe, o2, stranger } = await loadFixture(handedOver);
      const grant = call(registry, "grantRole", [await registry.VERIFIER_ROLE(), stranger.address]);
      await expect(viaSafe(await registry.getAddress(), grant, [o2])).to.be.reverted;
      await expect(registry.connect(o2).grantRole(await registry.VERIFIER_ROLE(), stranger.address)).to.be.reverted;
    });

    it("two owners rotate the verifier key", async function () {
      const ctx = await loadFixture(handedOver);
      const { registry, viaSafe, o1, o2, verifier, verifier2 } = ctx;
      const role = await registry.VERIFIER_ROLE();
      await viaSafe(await registry.getAddress(), call(registry, "grantRole", [role, verifier2.address]), [o1, o2]);
      await viaSafe(await registry.getAddress(), call(registry, "revokeRole", [role, verifier.address]), [o2, o1]);
      expect(await registry.hasRole(role, verifier.address)).to.equal(false);
      await submit(ctx, ctx.artist.address, {}, verifier2);
    });

    it("emergency: the Safe pauses (PAUSER) and unpauses (ADMIN)", async function () {
      const { registry, viaSafe, o2, o3 } = await loadFixture(handedOver);
      await viaSafe(await registry.getAddress(), call(registry, "pause"), [o2, o3]);
      expect(await registry.paused()).to.equal(true);
      await viaSafe(await registry.getAddress(), call(registry, "unpause"), [o2, o3]);
      expect(await registry.paused()).to.equal(false);
    });

    it("policy change: Safe schedules, it waits the delay, anyone executes", async function () {
      const { policies, timelock, viaSafe, o1, o2, stranger } = await loadFixture(handedOver);
      const target = await policies.getAddress();
      const data = call(policies, "setPolicy", [sha("6.0.0"), "6.0.0", "ipfs://p6"]);
      const args = [target, 0, data, ethers.ZeroHash, ethers.id("p6")];
      await expect(
        viaSafe(await timelock.getAddress(), call(timelock, "schedule", [...args, TIMELOCK_DELAY]), [o1, o2]),
      ).to.emit(timelock, "CallScheduled");
      const id = await timelock.hashOperation(...args);
      expect(await timelock.isOperationPending(id)).to.equal(true);
      await expect(timelock.connect(stranger).execute(...args)).to.be.revertedWithCustomError(
        timelock,
        "TimelockUnexpectedOperationState",
      );
      await time.increase(TIMELOCK_DELAY);
      await expect(timelock.connect(stranger).execute(...args))
        .to.emit(policies, "PolicySet")
        .withArgs(1, sha("6.0.0"), "6.0.0", "ipfs://p6");
      expect((await policies.current()).version).to.equal("6.0.0");
    });

    it("the Safe can cancel an announced policy change; the delay itself is timelocked", async function () {
      const { policies, timelock, viaSafe, o1, o3, stranger } = await loadFixture(handedOver);
      const args = [
        await policies.getAddress(),
        0,
        call(policies, "setPolicy", [sha("x"), "x", "ipfs://x"]),
        ethers.ZeroHash,
        ethers.ZeroHash,
      ];
      await viaSafe(await timelock.getAddress(), call(timelock, "schedule", [...args, TIMELOCK_DELAY]), [o1, o3]);
      const id = await timelock.hashOperation(...args);
      await viaSafe(await timelock.getAddress(), call(timelock, "cancel", [id]), [o1, o3]);
      await time.increase(TIMELOCK_DELAY);
      await expect(timelock.connect(stranger).execute(...args)).to.be.revertedWithCustomError(
        timelock,
        "TimelockUnexpectedOperationState",
      );
      await expect(timelock.connect(stranger).updateDelay(0)).to.be.revertedWithCustomError(
        timelock,
        "TimelockUnauthorizedCaller",
      );
      expect(await timelock.getMinDelay()).to.equal(TIMELOCK_DELAY);
    });

    it("owner-key rotation: two owners swap out a leaked owner", async function () {
      const { safe, safeAddress, viaSafe, o1, o2, o3, stranger } = await loadFixture(handedOver);
      // swapOwner(prevOwner, oldOwner, newOwner); the owner list is a linked list starting at 0x1
      const SENTINEL = "0x0000000000000000000000000000000000000001";
      const owners = await safe.getOwners();
      const prev = owners.indexOf(o3.address) === 0 ? SENTINEL : owners[owners.indexOf(o3.address) - 1];
      await viaSafe(safeAddress, safe.interface.encodeFunctionData("swapOwner", [prev, o3.address, stranger.address]), [
        o1,
        o2,
      ]);
      expect(await safe.isOwner(o3.address)).to.equal(false);
      expect(await safe.isOwner(stranger.address)).to.equal(true);
      expect(await safe.getThreshold()).to.equal(2);
    });

    it("forfeited bonds go to the Safe (treasury), reviewers still resolve via the resolver", async function () {
      const ctx = await loadFixture(handedOver);
      const { registry, resolver, reviewer, outsider, safeAddress, viaSafe, o1, o2 } = ctx;
      const { v } = await mintPass(ctx, ctx.artist);
      await registry.connect(outsider).openDispute(v.imageHash, "ipfs://e", { value: BOND });
      await resolver.connect(reviewer).resolveDispute(1, false, ethers.id("r"), "ipfs://r");
      expect(await registry.credits(safeAddress)).to.equal(BOND);
      const before = await ethers.provider.getBalance(safeAddress);
      await viaSafe(await registry.getAddress(), call(registry, "withdraw"), [o1, o2]);
      expect(await ethers.provider.getBalance(safeAddress)).to.equal(before + BOND);
    });
  });
});
