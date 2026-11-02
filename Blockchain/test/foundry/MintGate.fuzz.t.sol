// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IMintGate} from "../../contracts/v2/IMintGate.sol";
import {MintGateRegistry} from "../../contracts/v2/MintGateRegistry.sol";
import {MintGateNFTV2} from "../../contracts/v2/MintGateNFTV2.sol";
import {MintGateBase} from "./Base.t.sol";

/// @dev Property-based tests: each runs `fuzz.runs` times with random inputs.
contract MintGateFuzzTest is MintGateBase {
    function setUp() public {
        _deploy();
        vm.warp(1_800_000_000);
    }

    function _artist(address a) internal view returns (address) {
        // EOAs only: precompiles / contracts would need an ERC-721 receiver.
        vm.assume(a != address(0) && a.code.length == 0 && uint160(a) > 0x100);
        vm.assume(a != address(registry) && a != address(nft) && a != address(vm));
        return a;
    }

    function testFuzz_DigestMatchesIndependentEncoding(
        bytes32 image,
        bytes32 report,
        bytes32 policy,
        uint8 score,
        uint8 decision,
        address artist,
        uint64 issuedAt,
        uint64 expiresAt,
        uint256 nonce
    ) public view {
        IMintGate.Verdict memory v = IMintGate.Verdict(
            image,
            report,
            policy,
            score,
            IMintGate.Decision(bound(decision, 0, 6)),
            artist,
            issuedAt,
            expiresAt,
            nonce
        );
        assertEq(registry.hashVerdict(v), _manualDigest(v));
    }

    function testFuzz_ValidVerdictIsStoredAndOnlyPassIsMintable(
        bytes32 image,
        bytes32 report,
        uint8 score,
        uint8 decision,
        address artist,
        uint64 window,
        uint256 nonce
    ) public {
        vm.assume(image != 0 && report != 0);
        artist = _artist(artist);
        IMintGate.Verdict memory v = _verdict(image, report, artist, IMintGate.Decision(bound(decision, 1, 3)), nonce);
        v.score = uint8(bound(score, 0, 100));
        v.expiresAt = v.issuedAt + uint64(bound(window, 1, 30 days));

        assertEq(registry.submitVerdict(v, _sign(VERIFIER_KEY, v)), verifier);
        IMintGate.VerdictRecord memory r = registry.verdictOf(report);
        assertEq(r.imageHash, image);
        assertEq(r.artist, artist);
        assertEq(r.score, v.score);
        assertEq(uint8(r.decision), uint8(v.decision));
        assertEq(r.signer, verifier);
        (bool ok, ) = registry.isMintable(image, artist);
        assertEq(ok, v.decision == IMintGate.Decision.PASS);
    }

    function testFuzz_ForgedSignerReverts(uint256 key, bytes32 image, address artist) public {
        key = bound(key, 1, SECP256K1_N - 1);
        vm.assume(key != VERIFIER_KEY && image != 0);
        artist = _artist(artist);
        IMintGate.Verdict memory v = _verdict(image, keccak256(abi.encode(image)), artist, IMintGate.Decision.PASS, 1);
        bytes memory sig = _sign(key, v); // before expectRevert: _sign makes an external call
        vm.expectRevert(abi.encodeWithSelector(MintGateRegistry.UnauthorizedSigner.selector, vm.addr(key)));
        registry.submitVerdict(v, sig);
    }

    function testFuzz_TamperedFieldReverts(uint8 field, bytes32 noise, address artist) public {
        vm.assume(noise != 0);
        artist = _artist(artist);
        IMintGate.Verdict memory v = _verdict(keccak256("img"), keccak256("rep"), artist, IMintGate.Decision.FAIL, 7);
        bytes memory sig = _sign(VERIFIER_KEY, v);
        field = uint8(bound(field, 0, 5));
        if (field == 0) v.imageHash ^= noise;
        else if (field == 1) v.reportHash ^= noise;
        else if (field == 2) v.policyHash ^= noise;
        else if (field == 3) v.nonce ^= uint256(noise);
        else if (field == 4) v.decision = IMintGate.Decision.PASS;
        else v.score = uint8(uint256(noise) % 101) == v.score ? v.score - 1 : uint8(uint256(noise) % 101);
        vm.assume(v.imageHash != 0 && v.reportHash != 0);
        // The recovered signer is some random address, never the verifier.
        vm.expectPartialRevert(MintGateRegistry.UnauthorizedSigner.selector);
        registry.submitVerdict(v, sig);
    }

    function testFuzz_ExpiredReverts(uint64 window, uint64 late) public {
        IMintGate.Verdict memory v = _verdict(
            keccak256("i"),
            keccak256("r"),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            1
        );
        v.expiresAt = v.issuedAt + uint64(bound(window, 1, 30 days));
        bytes memory sig = _sign(VERIFIER_KEY, v);
        vm.warp(uint256(v.expiresAt) + bound(late, 0, 3650 days));
        vm.expectRevert(abi.encodeWithSelector(MintGateRegistry.VerdictExpired.selector, v.expiresAt));
        registry.submitVerdict(v, sig);
    }

    function testFuzz_WindowTooLongReverts(uint64 extra) public {
        IMintGate.Verdict memory v = _verdict(
            keccak256("i"),
            keccak256("r"),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            1
        );
        v.expiresAt = v.issuedAt + 30 days + uint64(bound(extra, 1, 3650 days));
        bytes memory sig = _sign(VERIFIER_KEY, v);
        vm.expectRevert(MintGateRegistry.InvalidValidity.selector);
        registry.submitVerdict(v, sig);
    }

    function testFuzz_ReplayReverts(bytes32 image, uint256 nonce, address relayer) public {
        vm.assume(image != 0);
        IMintGate.Verdict memory v = _verdict(
            image,
            keccak256(abi.encode(image)),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            nonce
        );
        bytes memory sig = _sign(VERIFIER_KEY, v);
        registry.submitVerdict(v, sig);
        vm.prank(relayer);
        vm.expectRevert(abi.encodeWithSelector(MintGateRegistry.NonceAlreadyUsed.selector, verifier, nonce));
        registry.submitVerdict(v, sig);
    }

    function testFuzz_MalleableSignatureReverts(bytes32 image) public {
        vm.assume(image != 0);
        IMintGate.Verdict memory v = _verdict(
            image,
            keccak256(abi.encode(image)),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            1
        );
        (uint8 sv, bytes32 r, bytes32 s) = vm.sign(VERIFIER_KEY, registry.hashVerdict(v));
        bytes32 highS = bytes32(SECP256K1_N - uint256(s));
        vm.expectRevert(abi.encodeWithSignature("ECDSAInvalidSignatureS(bytes32)", highS));
        registry.submitVerdict(v, abi.encodePacked(r, highS, sv == 27 ? uint8(28) : uint8(27)));
    }

    function testFuzz_OnlyTheArtistCanMint(address artist, address other, bytes32 image) public {
        vm.assume(image != 0);
        artist = _artist(artist);
        other = _artist(other);
        vm.assume(artist != other);
        IMintGate.Verdict memory v = _verdict(image, keccak256(abi.encode(image)), artist, IMintGate.Decision.PASS, 1);
        bytes memory sig = _sign(VERIFIER_KEY, v);

        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(MintGateNFTV2.ArtistMismatch.selector, artist));
        nft.submitAndMint(v, sig, "ipfs://m", 0);

        registry.submitVerdict(v, sig);
        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(MintGateRegistry.NotMintable.selector, image, other));
        nft.mint(image, "ipfs://m", 0);

        vm.prank(artist);
        uint256 id = nft.mint(image, "ipfs://m", 0);
        assertEq(nft.ownerOf(id), artist);

        vm.prank(artist);
        vm.expectPartialRevert(MintGateRegistry.ImageAlreadyMinted.selector);
        nft.mint(image, "ipfs://m", 0);
    }

    function testFuzz_RoyaltyCap(uint96 bps, uint256 price) public {
        price = bound(price, 0, 1e30);
        IMintGate.Verdict memory v = _verdict(
            keccak256("i"),
            keccak256("r"),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            1
        );
        registry.submitVerdict(v, _sign(VERIFIER_KEY, v));
        vm.prank(address(0xBEEF));
        if (bps > 1000) {
            vm.expectRevert(abi.encodeWithSelector(MintGateNFTV2.RoyaltyTooHigh.selector, bps));
            nft.mint(v.imageHash, "ipfs://m", bps);
        } else {
            uint256 id = nft.mint(v.imageHash, "ipfs://m", bps);
            (address to, uint256 amount) = nft.royaltyInfo(id, price);
            assertEq(to, address(0xBEEF));
            assertEq(amount, (price * bps) / 10_000);
        }
    }

    function testFuzz_DisputeNeedsExactBond(uint256 value) public {
        value = bound(value, 0, 1 ether);
        vm.assume(value != BOND);
        IMintGate.Verdict memory v = _verdict(
            keccak256("i"),
            keccak256("r"),
            address(0xBEEF),
            IMintGate.Decision.PASS,
            1
        );
        registry.submitVerdict(v, _sign(VERIFIER_KEY, v));
        vm.prank(address(0xBEEF));
        nft.mint(v.imageHash, "ipfs://m", 0);
        vm.deal(address(this), value);
        vm.expectRevert(abi.encodeWithSelector(MintGateRegistry.WrongBond.selector, BOND));
        registry.openDispute{value: value}(v.imageHash, "ipfs://e");
    }
}
