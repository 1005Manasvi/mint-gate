// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Context} from "@openzeppelin/contracts/utils/Context.sol";
import {ERC165Checker} from "@openzeppelin/contracts/utils/introspection/ERC165Checker.sol";
import {IMintGate} from "./IMintGate.sol";

/**
 * @title MintGated
 * @notice Inherit this in any ERC-721 to require a passing Mint-Gate verdict for every mint.
 *
 * Call `_consumeVerdict(imageHash, tokenId)` in your mint function, after your own state
 * changes (checks-effects-interactions): it reverts the whole mint unless the caller holds a
 * passing verdict for the image and the image has never been minted in any collection.
 * The registry admin must grant your collection COLLECTION_ROLE.
 */
abstract contract MintGated is Context {
    IMintGate public immutable mintGate;

    error InvalidMintGate(address gate);

    constructor(IMintGate gate) {
        if (!ERC165Checker.supportsInterface(address(gate), type(IMintGate).interfaceId))
            revert InvalidMintGate(address(gate));
        mintGate = gate;
    }

    /// @dev The artist is always the caller, so a collection cannot mint someone else's verdict.
    function _consumeVerdict(bytes32 imageHash, uint256 tokenId) internal returns (bytes32 reportHash) {
        return mintGate.consume(imageHash, _msgSender(), tokenId);
    }

    function _isImageRevoked(bytes32 imageHash) internal view returns (bool) {
        return mintGate.isRevoked(imageHash);
    }
}
