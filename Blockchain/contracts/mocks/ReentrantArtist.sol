// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC721Receiver} from "@openzeppelin/contracts/token/ERC721/IERC721Receiver.sol";
import {IMintGate} from "../v2/IMintGate.sol";
import {MintGateNFTV2} from "../v2/MintGateNFTV2.sol";

/// @dev Test fixture: an artist that is a contract and re-enters `mint` from the ERC-721
///      receiver callback, or refuses the token.
contract ReentrantArtist is IERC721Receiver {
    enum Mode {
        ACCEPT,
        REENTER,
        REJECT
    }

    MintGateNFTV2 public immutable nft;
    Mode public mode;
    bytes32 public reenterImage;
    uint256 public reentered;

    constructor(MintGateNFTV2 nft_) {
        nft = nft_;
    }

    function setMode(Mode m, bytes32 image) external {
        mode = m;
        reenterImage = image;
    }

    function mint(bytes32 imageHash) external returns (uint256) {
        return nft.mint(imageHash, "ipfs://art", 500);
    }

    function submitAndMint(IMintGate.Verdict calldata v, bytes calldata sig) external returns (uint256) {
        return nft.submitAndMint(v, sig, "ipfs://art", 500);
    }

    function onERC721Received(address, address, uint256, bytes calldata) external returns (bytes4) {
        if (mode == Mode.REJECT) return bytes4(0);
        if (mode == Mode.REENTER) {
            mode = Mode.ACCEPT;
            reentered = nft.mint(reenterImage, "ipfs://again", 0);
        }
        return IERC721Receiver.onERC721Received.selector;
    }
}
