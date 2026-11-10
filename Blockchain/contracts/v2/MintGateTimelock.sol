// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

/**
 * @title MintGateTimelock
 * @notice OpenZeppelin TimelockController, unchanged, under a project name. Owner of the
 *         PolicyRegistry: the governance Safe proposes (and may cancel), anyone executes once the
 *         delay has passed, and there is no admin, so the delay itself can only change through a
 *         timelocked call.
 */
contract MintGateTimelock is TimelockController {
    constructor(
        uint256 minDelay,
        address[] memory proposers,
        address[] memory executors
    ) TimelockController(minDelay, proposers, executors, address(0)) {}
}
