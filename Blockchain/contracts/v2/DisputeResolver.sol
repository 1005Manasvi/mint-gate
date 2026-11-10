// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";

interface IDisputeRegistry is IAccessControl {
    struct Dispute {
        bytes32 imageHash;
        address disputant;
        uint96 bond;
        uint8 status;
        uint64 openedAt;
        uint64 resolvedAt;
        string evidenceURI;
    }

    // slither-disable-next-line naming-convention
    function REVIEWER_ROLE() external view returns (bytes32); // the registry's getter name

    function resolveDispute(uint256 disputeId, bool upheld) external;

    function disputeOf(uint256 disputeId) external view returns (Dispute memory);

    function mintOf(bytes32 imageHash) external view returns (address, uint256, bytes32, uint256);
}

interface IRefreshableMetadata {
    function refreshMetadata(uint256 tokenId) external;
}

/**
 * @title DisputeResolver
 * @notice Resolves MintGateRegistry disputes *with a written reason*: the reviewer's reason is
 *         pinned to IPFS off chain and its SHA-256 + URI are recorded here in the same transaction
 *         as the decision, so every revocation is explained and the explanation cannot be swapped.
 *
 * The registry is immutable, so this contract is added next to it: the registry admin grants it
 * REVIEWER_ROLE, and it accepts a call only from an account that itself holds REVIEWER_ROLE on the
 * registry (one reviewer list, managed by the registry admin = the governance Safe). It has no
 * owner and no state. After an upheld dispute it asks the token's collection to emit ERC-4906
 * `MetadataUpdate`, best effort (collections without `refreshMetadata` are skipped).
 */
contract DisputeResolver {
    IDisputeRegistry public immutable registry;

    event DisputeReasoned(
        uint256 indexed disputeId,
        bytes32 indexed imageHash,
        address indexed reviewer,
        bool upheld,
        bytes32 reasonHash,
        string reasonURI
    );

    error NotReviewer(address account);
    error EmptyReason();
    error ZeroAddress();

    constructor(IDisputeRegistry registry_) {
        if (address(registry_) == address(0)) revert ZeroAddress();
        registry = registry_;
    }

    /// @param reasonHash SHA-256 of the reason document stored at `reasonURI`.
    function resolveDispute(uint256 disputeId, bool upheld, bytes32 reasonHash, string calldata reasonURI) external {
        if (!registry.hasRole(registry.REVIEWER_ROLE(), msg.sender)) revert NotReviewer(msg.sender);
        if (reasonHash == bytes32(0) || bytes(reasonURI).length == 0) revert EmptyReason();
        bytes32 imageHash = registry.disputeOf(disputeId).imageHash;
        emit DisputeReasoned(disputeId, imageHash, msg.sender, upheld, reasonHash, reasonURI);
        registry.resolveDispute(disputeId, upheld); // reverts unless the dispute is open
        if (upheld) {
            // Only the collection and token id are needed; the report hash and dispute pointer are not.
            // slither-disable-next-line unused-return
            (address collection, uint256 tokenId, , ) = registry.mintOf(imageHash);
            // Best effort: the collection holds COLLECTION_ROLE (vetted by the admin).
            try IRefreshableMetadata(collection).refreshMetadata(tokenId) {} catch {}
        }
    }
}
