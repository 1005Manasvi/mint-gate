// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {IMintGate} from "../../contracts/v2/IMintGate.sol";
import {MintGateRegistry} from "../../contracts/v2/MintGateRegistry.sol";
import {MintGateNFTV2} from "../../contracts/v2/MintGateNFTV2.sol";

/// @dev Deployment + EIP-712 signing shared by the fuzz and invariant suites.
abstract contract MintGateBase is Test {
    uint256 internal constant SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    uint256 internal constant VERIFIER_KEY = 0xA11CE;
    uint256 internal constant BOND = 0.001 ether;

    MintGateRegistry internal registry;
    MintGateNFTV2 internal nft;
    address internal admin = makeAddr("admin");
    address internal verifier;
    address internal reviewer = makeAddr("reviewer");
    address internal pauser = makeAddr("pauser");
    address internal treasury = makeAddr("treasury");

    function _deploy() internal {
        verifier = vm.addr(VERIFIER_KEY);
        registry = new MintGateRegistry(3600, admin, verifier, reviewer, pauser, treasury, BOND);
        nft = new MintGateNFTV2(registry);
        bytes32 collectionRole = registry.COLLECTION_ROLE();
        vm.prank(admin);
        registry.grantRole(collectionRole, address(nft));
    }

    function _verdict(
        bytes32 imageHash,
        bytes32 reportHash,
        address artist,
        IMintGate.Decision decision,
        uint256 nonce
    ) internal view returns (IMintGate.Verdict memory) {
        return
            IMintGate.Verdict({
                imageHash: imageHash,
                reportHash: reportHash,
                policyHash: keccak256("policy-3.1.0"),
                score: 90,
                decision: decision,
                artist: artist,
                issuedAt: uint64(block.timestamp),
                expiresAt: uint64(block.timestamp + 1 days),
                nonce: nonce
            });
    }

    function _sign(uint256 key, IMintGate.Verdict memory v) internal view returns (bytes memory) {
        (uint8 sv, bytes32 r, bytes32 s) = vm.sign(key, registry.hashVerdict(v));
        return abi.encodePacked(r, s, sv);
    }

    /// @dev Independent EIP-712 encoding (field by field), to check the contract's hashVerdict.
    function _manualDigest(IMintGate.Verdict memory v) internal view returns (bytes32) {
        bytes32 domainSeparator = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256("MintGateRegistry"),
                keccak256("1"),
                block.chainid,
                address(registry)
            )
        );
        bytes32 structHash = keccak256(
            abi.encode(
                registry.VERDICT_TYPEHASH(),
                v.imageHash,
                v.reportHash,
                v.policyHash,
                uint256(v.score),
                uint256(uint8(v.decision)),
                v.artist,
                uint256(v.issuedAt),
                uint256(v.expiresAt),
                v.nonce
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator, structHash));
    }
}
