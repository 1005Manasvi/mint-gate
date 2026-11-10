// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IMintGate} from "../../contracts/v2/IMintGate.sol";
import {DisputeResolver, IDisputeRegistry} from "../../contracts/v2/DisputeResolver.sol";
import {PolicyRegistry} from "../../contracts/v2/PolicyRegistry.sol";
import {MintGateBase} from "./Base.t.sol";

/// @dev Phase 6 properties: only registry reviewers resolve through the resolver; the policy in
///      force at any time is the latest announcement at or before it.
contract GovernanceFuzzTest is MintGateBase {
    DisputeResolver internal resolver;
    address internal artist = makeAddr("artist");
    address internal disputant = makeAddr("disputant");

    function setUp() public {
        _deploy();
        resolver = new DisputeResolver(IDisputeRegistry(address(registry)));
        bytes32 role = registry.REVIEWER_ROLE();
        vm.prank(admin);
        registry.grantRole(role, address(resolver));

        IMintGate.Verdict memory v = _verdict(keccak256("img"), keccak256("rep"), artist, IMintGate.Decision.PASS, 1);
        registry.submitVerdict(v, _sign(VERIFIER_KEY, v));
        vm.prank(artist);
        nft.mint(v.imageHash, "ipfs://m", 0);
        vm.deal(disputant, BOND);
        vm.prank(disputant);
        registry.openDispute{value: BOND}(v.imageHash, "ipfs://e");
    }

    function testFuzz_ResolverOnlyForReviewers(address caller, bool upheld, bytes32 reason) public {
        vm.assume(caller != reviewer && reason != bytes32(0));
        vm.prank(caller);
        vm.expectRevert(abi.encodeWithSelector(DisputeResolver.NotReviewer.selector, caller));
        resolver.resolveDispute(1, upheld, reason, "ipfs://r");
    }

    function testFuzz_ResolverRecordsAnyReason(bool upheld, bytes32 reason) public {
        vm.assume(reason != bytes32(0));
        vm.expectEmit(address(resolver));
        emit DisputeResolver.DisputeReasoned(1, keccak256("img"), reviewer, upheld, reason, "ipfs://r");
        vm.prank(reviewer);
        resolver.resolveDispute(1, upheld, reason, "ipfs://r");
        assertEq(registry.isRevoked(keccak256("img")), upheld);
        assertEq(registry.credits(upheld ? disputant : treasury), BOND);
    }

    function testFuzz_PolicyActiveAt(uint32[4] memory gaps, uint64 probe) public {
        PolicyRegistry p = new PolicyRegistry(address(this), new bytes32[](0), new string[](0), new string[](0));
        uint64[4] memory at;
        for (uint256 i; i < 4; i++) {
            vm.warp(block.timestamp + gaps[i]);
            p.setPolicy(keccak256(abi.encode(i)), "v", "ipfs://p");
            at[i] = uint64(block.timestamp);
        }
        probe = uint64(bound(probe, 0, at[3] + 1 days));
        int256 expected = -1;
        for (uint256 i; i < 4; i++) if (at[i] <= probe) expected = int256(i);
        if (expected < 0) {
            vm.expectRevert(PolicyRegistry.NoPolicy.selector);
            p.policyActiveAt(probe);
        } else {
            // equal timestamps: the latest announcement wins
            assertEq(p.policyActiveAt(probe).policyHash, keccak256(abi.encode(uint256(expected))));
        }
    }
}
