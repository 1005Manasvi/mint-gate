// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {MintGateRegistry} from "../v2/MintGateRegistry.sol";

/// @dev Test fixture: a disputant contract that cannot receive ETH.
contract EtherRejecter {
    MintGateRegistry public immutable registry;

    constructor(MintGateRegistry registry_) {
        registry = registry_;
    }

    function openDispute(bytes32 imageHash) external payable returns (uint256) {
        return registry.openDispute{value: msg.value}(imageHash, "ipfs://evidence");
    }

    function withdraw() external {
        registry.withdraw();
    }
}
