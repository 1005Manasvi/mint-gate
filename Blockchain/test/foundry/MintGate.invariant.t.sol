// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {IMintGate} from "../../contracts/v2/IMintGate.sol";
import {MintGateRegistry} from "../../contracts/v2/MintGateRegistry.sol";
import {MintGateNFTV2} from "../../contracts/v2/MintGateNFTV2.sol";
import {ThirdPartyCollection} from "../../contracts/mocks/ThirdPartyCollection.sol";
import {DisputeResolver, IDisputeRegistry} from "../../contracts/v2/DisputeResolver.sol";
import {MintGateBase} from "./Base.t.sol";

/// @dev Random sequences of every user / role action. A small pool of images and artists makes
///      collisions (same image, several verdicts, several collections) frequent.
contract Handler is Test {
    MintGateRegistry public immutable registry;
    MintGateNFTV2 public immutable nft;
    ThirdPartyCollection public immutable tp;
    address public immutable admin;
    address public immutable reviewer;
    address public immutable pauser;
    address public immutable treasury;

    uint256 public verifierKey;
    bytes32[5] public images;
    address[3] public artists;
    address[3] public disputants;

    bytes32[] public reports;
    mapping(bytes32 => bool) public signerWasVerifier; // at submission time
    IMintGate.Verdict internal lastVerdict;
    bytes internal lastSig;
    uint256 internal nonce;

    mapping(bytes32 => address) public minterOf;
    uint256[] public tpTokens;
    mapping(uint256 => bytes32) public tpImage;
    bytes32[] public revokedSeen;

    uint256 public mintsOk;
    uint256 public disputesOk;
    uint256 public resolvedOk;
    bool public rogueAccepted;
    bool public replayAccepted;
    uint256 public calls;

    // Phase 6 bond accounting (ghost state)
    DisputeResolver public resolver;
    uint256 public deposited; // every bond ever paid in
    uint256 public totalWithdrawn;
    mapping(address => uint256) public withdrawnBy;
    mapping(uint256 => uint256) public bondAtOpen; // disputeId -> msg.value when opened
    uint256 public bondChanges;
    uint256 public viaResolver;

    constructor(MintGateRegistry r, MintGateNFTV2 n, ThirdPartyCollection t, uint256 key, address[4] memory roles) {
        (registry, nft, tp, verifierKey) = (r, n, t, key);
        resolver = new DisputeResolver(IDisputeRegistry(address(r)));
        (admin, reviewer, pauser, treasury) = (roles[0], roles[1], roles[2], roles[3]);
        for (uint256 i; i < 5; i++) images[i] = keccak256(abi.encode("image", i));
        for (uint256 i; i < 3; i++) {
            artists[i] = makeAddr(string(abi.encode("artist", i)));
            disputants[i] = makeAddr(string(abi.encode("disputant", i)));
        }
    }

    function _sign(uint256 key, IMintGate.Verdict memory v) internal view returns (bytes memory) {
        (uint8 sv, bytes32 r, bytes32 s) = vm.sign(key, registry.hashVerdict(v));
        return abi.encodePacked(r, s, sv);
    }

    function _newVerdict(uint256 seed) internal returns (IMintGate.Verdict memory v) {
        nonce++;
        v = IMintGate.Verdict({
            imageHash: images[seed % 5],
            reportHash: keccak256(abi.encode("report", nonce)),
            policyHash: keccak256("policy"),
            score: uint8(seed % 101),
            decision: IMintGate.Decision(1 + ((seed >> 8) % 3)),
            artist: artists[(seed >> 16) % 3],
            issuedAt: uint64(block.timestamp),
            expiresAt: uint64(block.timestamp + 1 days),
            nonce: nonce
        });
    }

    function submit(uint256 seed) external {
        calls++;
        IMintGate.Verdict memory v = _newVerdict(seed);
        bytes memory sig = _sign(verifierKey, v);
        bool isVerifier = registry.hasRole(registry.VERIFIER_ROLE(), vm.addr(verifierKey));
        try registry.submitVerdict(v, sig) {
            reports.push(v.reportHash);
            signerWasVerifier[v.reportHash] = isVerifier;
            lastVerdict = v;
            lastSig = sig;
        } catch {}
    }

    function submitRogue(uint256 seed, uint256 key) external {
        calls++;
        key = bound(key, 1, 2 ** 128);
        if (key == verifierKey) return;
        IMintGate.Verdict memory v = _newVerdict(seed);
        try registry.submitVerdict(v, _sign(key, v)) {
            rogueAccepted = true;
        } catch {}
    }

    function replayLast() external {
        calls++;
        if (lastSig.length == 0) return;
        try registry.submitVerdict(lastVerdict, lastSig) {
            replayAccepted = true;
        } catch {}
    }

    function resolveReview(uint256 seed, bool approve) external {
        calls++;
        if (reports.length == 0) return;
        vm.prank(reviewer);
        try registry.resolveReview(reports[seed % reports.length], approve) {} catch {}
    }

    function voidVerdict(uint256 seed) external {
        calls++;
        if (reports.length == 0) return;
        vm.prank(reviewer);
        try registry.voidVerdict(reports[seed % reports.length]) {} catch {}
    }

    function mint(uint256 seed, bool thirdParty) external {
        calls++;
        bytes32 image = images[seed % 5];
        address artist = artists[(seed >> 8) % 3];
        if (reports.length != 0 && seed % 4 != 0) {
            // usually aim at a recorded verdict, so mints are frequent rather than lucky
            IMintGate.VerdictRecord memory r = registry.verdictOf(reports[(seed >> 16) % reports.length]);
            (image, artist) = (r.imageHash, r.artist);
        }
        vm.prank(artist);
        if (thirdParty) {
            try tp.mint(image) returns (uint256 id) {
                tpTokens.push(id);
                tpImage[id] = image;
                minterOf[image] = artist;
                mintsOk++;
            } catch {}
        } else {
            try nft.mint(image, "ipfs://m", uint96(seed % 1001)) {
                minterOf[image] = artist;
                mintsOk++;
            } catch {}
        }
    }

    function openDispute(uint256 seed) external {
        calls++;
        address d = disputants[seed % 3];
        uint256 bond = registry.disputeBond();
        vm.deal(d, bond);
        vm.prank(d);
        try registry.openDispute{value: bond}(images[(seed >> 8) % 5], "ipfs://e") returns (uint256 id) {
            disputesOk++;
            deposited += bond;
            bondAtOpen[id] = bond;
        } catch {}
    }

    function resolveDispute(uint256 seed, bool upheld) external {
        calls++;
        uint256 n = registry.totalDisputes();
        if (n == 0) return;
        uint256 id = 1 + (seed % n);
        bytes32 image = registry.disputeOf(id).imageHash;
        vm.prank(reviewer);
        if ((seed >> 128) % 2 == 0) {
            try registry.resolveDispute(id, upheld) {
                if (upheld) revokedSeen.push(image);
                resolvedOk++;
            } catch {}
        } else {
            // the Phase 6 path: reviewer resolves through the resolver, with a reason hash
            try resolver.resolveDispute(id, upheld, keccak256(abi.encode(id)), "ipfs://reason") {
                if (upheld) revokedSeen.push(image);
                resolvedOk++;
                viaResolver++;
            } catch {}
        }
    }

    function withdraw(uint256 seed) external {
        calls++;
        address who = seed % 4 == 3 ? treasury : disputants[seed % 3];
        uint256 owed = registry.credits(who);
        vm.prank(who);
        try registry.withdraw() {
            withdrawnBy[who] += owed;
            totalWithdrawn += owed;
        } catch {}
    }

    /// Admin changes the bond while disputes are open: open disputes must keep their snapshot.
    function setBond(uint256 seed) external {
        calls++;
        vm.prank(admin);
        registry.setDisputeBond(bound(seed, 0, 0.01 ether));
        bondChanges++;
    }

    function transfer(uint256 seed) external {
        calls++;
        uint256 n = nft.totalMinted();
        if (n == 0) return;
        uint256 id = 1 + (seed % n);
        address owner = nft.ownerOf(id);
        vm.prank(owner);
        nft.transferFrom(owner, artists[(seed >> 8) % 3], id);
    }

    function rotateVerifier(uint256 seed) external {
        calls++;
        uint256 newKey = bound(seed, 1, 2 ** 128);
        bytes32 role = registry.VERIFIER_ROLE();
        vm.startPrank(admin);
        registry.grantRole(role, vm.addr(newKey));
        if (vm.addr(newKey) != vm.addr(verifierKey)) registry.revokeRole(role, vm.addr(verifierKey));
        vm.stopPrank();
        verifierKey = newKey;
    }

    function togglePause() external {
        calls++;
        if (registry.paused()) {
            vm.prank(admin);
            registry.unpause();
        } else {
            vm.prank(pauser);
            registry.pause();
        }
    }

    function warp(uint256 secs) external {
        calls++;
        vm.warp(block.timestamp + bound(secs, 0, 2 days));
    }

    // views for the invariants
    function reportCount() external view returns (uint256) {
        return reports.length;
    }

    function tpCount() external view returns (uint256) {
        return tpTokens.length;
    }

    function revokedCount() external view returns (uint256) {
        return revokedSeen.length;
    }
}

contract MintGateInvariantTest is MintGateBase {
    Handler internal handler;
    ThirdPartyCollection internal tp;

    function setUp() public {
        _deploy();
        tp = new ThirdPartyCollection(registry);
        bytes32 collectionRole = registry.COLLECTION_ROLE();
        vm.prank(admin);
        registry.grantRole(collectionRole, address(tp));
        handler = new Handler(registry, nft, tp, VERIFIER_KEY, [admin, reviewer, pauser, treasury]);
        bytes32 reviewerRole = registry.REVIEWER_ROLE();
        address resolver = address(handler.resolver());
        vm.prank(admin);
        registry.grantRole(reviewerRole, resolver);
        targetContract(address(handler));
        excludeSender(address(handler));
    }

    /// 1. One token per image across both collections, and the collections agree with the registry.
    function invariant_OneTokenPerImage() public view {
        uint256 mintedImages;
        for (uint256 i; i < 5; i++) {
            (address collection, , , ) = registry.mintOf(handler.images(i));
            if (collection != address(0)) mintedImages++;
        }
        assertEq(registry.totalMints(), mintedImages, "registry mint count");
        assertEq(nft.totalMinted() + handler.tpCount(), mintedImages, "tokens == distinct images");

        for (uint256 id = 1; id <= nft.totalMinted(); id++) {
            bytes32 image = nft.imageOfToken(id);
            (address collection, uint256 tokenId, , ) = registry.mintOf(image);
            assertEq(collection, address(nft));
            assertEq(tokenId, id);
            assertEq(nft.tokenOfImage(image), id);
        }
        for (uint256 i; i < handler.tpCount(); i++) {
            uint256 id = handler.tpTokens(i);
            (address collection, uint256 tokenId, , ) = registry.mintOf(handler.tpImage(id));
            assertEq(collection, address(tp));
            assertEq(tokenId, id);
        }
    }

    /// 2. Every mint is backed by a passing verdict, signed by a verifier, naming the minter.
    function invariant_NoMintWithoutValidVerdict() public view {
        for (uint256 i; i < 5; i++) {
            bytes32 image = handler.images(i);
            (address collection, , bytes32 reportHash, ) = registry.mintOf(image);
            if (collection == address(0)) continue;
            IMintGate.VerdictRecord memory r = registry.verdictOf(reportHash);
            assertTrue(
                r.decision == IMintGate.Decision.PASS || r.decision == IMintGate.Decision.REVIEW_APPROVED,
                "minted with a non-passing verdict"
            );
            assertEq(r.imageHash, image, "verdict for another image");
            assertEq(r.artist, handler.minterOf(image), "minted by someone other than the artist");
            assertTrue(handler.signerWasVerifier(reportHash), "signer was not a verifier");
        }
        assertFalse(handler.rogueAccepted(), "non-verifier signature accepted");
        assertFalse(handler.replayAccepted(), "replayed verdict accepted");
    }

    /// 3. Revoked stays revoked (through transfers, new disputes, pauses, key rotations).
    function invariant_RevokedStaysRevoked() public view {
        for (uint256 i; i < handler.revokedCount(); i++) {
            bytes32 image = handler.revokedSeen(i);
            assertTrue(registry.isRevoked(image));
            assertEq(uint8(registry.imageStatus(image)), uint8(IMintGate.ImageStatus.REVOKED));
            uint256 id = nft.tokenOfImage(image);
            if (id != 0) {
                assertTrue(nft.isRevoked(id));
                assertEq(nft.tokenURI(id), nft.REVOKED_URI());
            }
        }
    }

    /// 4. Solvency: the registry holds exactly the open bonds plus unclaimed credits.
    function invariant_Solvency() public view {
        uint256 owed;
        for (uint256 id = 1; id <= registry.totalDisputes(); id++) {
            MintGateRegistry.Dispute memory d = registry.disputeOf(id);
            if (d.status == MintGateRegistry.DisputeStatus.OPEN) owed += d.bond;
        }
        for (uint256 i; i < 3; i++) owed += registry.credits(handler.disputants(i));
        owed += registry.credits(treasury);
        assertEq(address(registry).balance, owed);
    }

    // ---- Phase 6: bond accounting ----------------------------------------------------------

    /// 5. Conservation: every wei paid in as a bond is either still in the registry or withdrawn.
    function invariant_BondConservation() public view {
        assertEq(address(registry).balance + handler.totalWithdrawn(), handler.deposited(), "bond leak");
    }

    /// 6. Each dispute keeps the bond it was opened with, whatever the admin sets later.
    function invariant_BondSnapshot() public view {
        for (uint256 id = 1; id <= registry.totalDisputes(); id++) {
            assertEq(registry.disputeOf(id).bond, handler.bondAtOpen(id), "bond changed after opening");
        }
    }

    /// 7. Payouts follow the outcome: a disputant's credits + withdrawals equal the bonds of their
    ///    upheld disputes; the treasury's equal the bonds of all rejected disputes. Open bonds are
    ///    nobody's yet.
    function invariant_PayoutsFollowOutcome() public view {
        uint256[3] memory earned;
        uint256 forfeited;
        for (uint256 id = 1; id <= registry.totalDisputes(); id++) {
            MintGateRegistry.Dispute memory d = registry.disputeOf(id);
            if (d.status == MintGateRegistry.DisputeStatus.REJECTED) forfeited += d.bond;
            if (d.status != MintGateRegistry.DisputeStatus.UPHELD) continue;
            for (uint256 i; i < 3; i++) if (d.disputant == handler.disputants(i)) earned[i] += d.bond;
        }
        for (uint256 i; i < 3; i++) {
            address who = handler.disputants(i);
            assertEq(registry.credits(who) + handler.withdrawnBy(who), earned[i], "disputant payout");
        }
        assertEq(registry.credits(treasury) + handler.withdrawnBy(treasury), forfeited, "treasury payout");
    }

    /// 8. At most one open dispute per image, and the image points at exactly that dispute.
    function invariant_OneOpenDisputePerImage() public view {
        for (uint256 i; i < 5; i++) {
            bytes32 image = handler.images(i);
            uint256 open;
            uint256 openId;
            for (uint256 id = 1; id <= registry.totalDisputes(); id++) {
                MintGateRegistry.Dispute memory d = registry.disputeOf(id);
                if (d.imageHash == image && d.status == MintGateRegistry.DisputeStatus.OPEN)
                    (open, openId) = (open + 1, id);
            }
            assertLe(open, 1, "two open disputes on one image");
            (, , , uint256 active) = registry.mintOf(image);
            assertEq(active, openId, "image points at the wrong dispute");
        }
    }

    /// The handler reaches deep states (mints in both collections, disputes, revocations), so
    /// the invariants above are not passing on an empty system.
    function test_HandlerReachesDeepStates() public {
        for (uint256 i; i < 800; i++) {
            uint256 seed = uint256(keccak256(abi.encode(i)));
            uint256 action = i % 8;
            if (action == 0 || action == 1) handler.submit(seed);
            else if (action == 2) handler.mint(seed, seed % 3 == 0);
            else if (action == 3) handler.openDispute(seed);
            else if (action == 4) handler.resolveDispute(seed, seed % 2 == 0);
            else if (action == 5) handler.withdraw(seed);
            else if (action == 6 && i % 48 == 6) handler.setBond(seed);
            else handler.resolveReview(seed, true);
        }
        assertGt(handler.mintsOk(), 4, "mints");
        assertGt(handler.tpCount(), 0, "third-party mints");
        assertGt(handler.disputesOk(), 2, "disputes");
        assertGt(handler.revokedCount(), 0, "revocations");
        assertGt(handler.viaResolver(), 0, "resolutions through the resolver");
        assertGt(handler.totalWithdrawn(), 0, "withdrawals");
        assertGt(handler.bondChanges(), 0, "bond changes");
        invariant_OneTokenPerImage();
        invariant_NoMintWithoutValidVerdict();
        invariant_RevokedStaysRevoked();
        invariant_Solvency();
        invariant_BondConservation();
        invariant_BondSnapshot();
        invariant_PayoutsFollowOutcome();
        invariant_OneOpenDisputePerImage();
    }
}
