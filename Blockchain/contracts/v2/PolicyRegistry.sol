// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";

/**
 * @title PolicyRegistry
 * @notice Public history of the Mint-Gate decision policies. Every verdict carries `policyHash`
 *         (SHA-256 of the exact policy JSON bytes); this contract announces each policy with its
 *         IPFS URI, so anyone can fetch the JSON, re-hash it and see which rules applied when.
 *
 * The owner is a TimelockController whose only proposer is the governance Safe: a policy change
 * is visible on chain (`CallScheduled`) for the timelock delay before it takes effect, and no
 * single key can make one. Entries are append-only; re-announcing an older hash (a rollback)
 * appends a new entry.
 */
contract PolicyRegistry is Ownable2Step {
    struct Policy {
        bytes32 policyHash; // SHA-256 of the policy JSON file
        uint64 activatedAt; // block time of the announcement
        string version; // e.g. "5.1.0"
        string uri; // ipfs://CID of the exact bytes
    }

    Policy[] private _policies;
    mapping(bytes32 policyHash => uint256) private _latest; // 1-based index of its latest entry

    event PolicySet(uint256 indexed index, bytes32 indexed policyHash, string version, string uri);

    error EmptyField();
    error LengthMismatch();
    error NoPolicy();
    error IndexOutOfRange(uint256 index);

    constructor(
        address owner_,
        bytes32[] memory hashes,
        string[] memory versions,
        string[] memory uris
    ) Ownable(owner_) {
        if (hashes.length != versions.length || hashes.length != uris.length) revert LengthMismatch();
        for (uint256 i; i < hashes.length; i++) _set(hashes[i], versions[i], uris[i]);
    }

    /// @notice Announce the policy that verdicts use from now on.
    function setPolicy(bytes32 policyHash, string calldata version, string calldata uri) external onlyOwner {
        _set(policyHash, version, uri);
    }

    // ---- views -----------------------------------------------------------------------------

    function policyCount() external view returns (uint256) {
        return _policies.length;
    }

    function policyAt(uint256 index) external view returns (Policy memory) {
        // A length, not a time: the struct holds a timestamp, which taints the array for Slither.
        // slither-disable-next-line timestamp
        if (index >= _policies.length) revert IndexOutOfRange(index);
        return _policies[index];
    }

    /// @notice The latest announced policy.
    function current() external view returns (Policy memory) {
        // slither-disable-next-line incorrect-equality,timestamp
        if (_policies.length == 0) revert NoPolicy();
        return _policies[_policies.length - 1];
    }

    /// @notice Whether `policyHash` was ever announced, and when it was last (re-)announced.
    function isRegistered(bytes32 policyHash) external view returns (bool, uint64 activatedAt) {
        uint256 i = _latest[policyHash];
        // i is an array index (0 = never announced), not a time or a balance.
        // slither-disable-next-line incorrect-equality,timestamp
        return i == 0 ? (false, 0) : (true, _policies[i - 1].activatedAt);
    }

    /// @notice The policy in force at time `t` (latest announcement at or before `t`), e.g. a
    ///         verdict's `issuedAt`. Reverts if none was announced yet.
    function policyActiveAt(uint64 t) external view returns (Policy memory) {
        for (uint256 i = _policies.length; i > 0; i--) {
            // Policies change rarely; the loop is short and only used by off-chain readers.
            // slither-disable-next-line timestamp
            if (_policies[i - 1].activatedAt <= t) return _policies[i - 1];
        }
        revert NoPolicy();
    }

    function _set(bytes32 policyHash, string memory version, string memory uri) private {
        if (policyHash == bytes32(0) || bytes(version).length == 0 || bytes(uri).length == 0) revert EmptyField();
        _policies.push(Policy(policyHash, uint64(block.timestamp), version, uri));
        _latest[policyHash] = _policies.length;
        emit PolicySet(_policies.length - 1, policyHash, version, uri);
    }
}
