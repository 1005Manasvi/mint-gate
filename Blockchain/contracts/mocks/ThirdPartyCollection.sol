// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {IMintGate} from "../v2/IMintGate.sol";
import {MintGated} from "../v2/MintGated.sol";

/// @dev Test fixture and usage example: a minimal third-party collection gated by Mint-Gate.
contract ThirdPartyCollection is ERC721, MintGated {
    uint256 private _last;

    constructor(IMintGate gate) ERC721("Third Party", "TP") MintGated(gate) {}

    function mint(bytes32 imageHash) external returns (uint256 id) {
        id = ++_last;
        _mint(_msgSender(), id);
        _consumeVerdict(imageHash, id);
    }
}
