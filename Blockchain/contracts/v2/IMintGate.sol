// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/**
 * @title IMintGate
 * @notice Verdict registry interface: what an ERC-721 needs to refuse mints without a passing
 *         AI-forensics verdict. See docs/contracts.md for the state machines.
 */
interface IMintGate {
    enum Decision {
        NONE,
        PASS,
        REVIEW,
        FAIL,
        REVIEW_APPROVED,
        REVIEW_REJECTED,
        VOIDED
    }

    enum ImageStatus {
        NONE,
        MINTED,
        DISPUTED,
        REVOKED
    }

    /// @dev The EIP-712 message the verifier signs. `decision` is encoded as uint8.
    struct Verdict {
        bytes32 imageHash; // SHA-256 of the canonical pixels
        bytes32 reportHash; // SHA-256 of the full report (primary key)
        bytes32 policyHash; // SHA-256 of the policy used
        uint8 score; // authenticity 0-100
        Decision decision; // PASS, REVIEW or FAIL
        address artist; // the only wallet allowed to mint with it
        uint64 issuedAt;
        uint64 expiresAt; // submission deadline
        uint256 nonce; // one use per signer
    }

    struct VerdictRecord {
        bytes32 imageHash;
        bytes32 policyHash;
        address artist;
        uint8 score;
        Decision decision;
        uint64 issuedAt;
        address signer;
        uint64 expiresAt;
    }

    /// @notice Put a verifier-signed verdict on chain. Anyone may submit it.
    function submitVerdict(Verdict calldata v, bytes calldata signature) external returns (address signer);

    /// @notice Whether `artist` may mint `imageHash` now, and the passing report that allows it.
    function isMintable(bytes32 imageHash, address artist) external view returns (bool, bytes32 reportHash);

    /// @notice Called by an approved collection when it mints: uses up the image's single mint.
    function consume(bytes32 imageHash, address artist, uint256 tokenId) external returns (bytes32 reportHash);

    function verdictOf(bytes32 reportHash) external view returns (VerdictRecord memory);

    function imageStatus(bytes32 imageHash) external view returns (ImageStatus);

    function isRevoked(bytes32 imageHash) external view returns (bool);
}
