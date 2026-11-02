// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC721URIStorage} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title MintGateNFT
 * @notice Tamper-resistant provenance for AI-verified artwork.
 *
 * Design principle: this contract does NOT detect forgery. Detection runs
 * off-chain in the Mint-Gate pipeline. The contract only
 *   1. permanently records each verification verdict (report hash, image
 *      hash, score, decision, policy hash) - including FAILED attempts;
 *   2. lets a human reviewer resolve REVIEW verdicts;
 *   3. refuses to mint unless a PASS (or reviewer-approved) verdict exists,
 *      and refuses to mint the same image twice.
 */
contract MintGateNFT is ERC721URIStorage, AccessControl {
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant REVIEWER_ROLE = keccak256("REVIEWER_ROLE");

    enum Decision { NONE, PASS, REVIEW, FAIL, REVIEW_APPROVED, REVIEW_REJECTED }

    struct Verification {
        bytes32 imageHash;      // SHA-256 of the canonical pixels
        bytes32 policyHash;     // SHA-256 of policy.json used for the verdict
        uint8 score;            // authenticity score 0-100
        Decision decision;
        uint64 timestamp;
        address verifier;
        uint256 tokenId;        // 0 until minted
        string policyVersion;
    }

    mapping(bytes32 => Verification) private _verifications;   // reportHash => verdict
    mapping(bytes32 => uint256) public tokenOfImage;             // imageHash  => tokenId
    mapping(uint256 => bytes32) public reportOfToken;            // tokenId    => reportHash
    uint256 private _nextTokenId = 1;
    uint256 public totalVerifications;

    event VerificationRecorded(bytes32 indexed reportHash, bytes32 indexed imageHash, uint8 score,
                               Decision decision, string policyVersion, address verifier);
    event ReviewResolved(bytes32 indexed reportHash, bool approved, address reviewer);
    event VerifiedMint(uint256 indexed tokenId, address indexed to, bytes32 indexed reportHash,
                       bytes32 imageHash, uint8 score);

    error AlreadyRecorded(bytes32 reportHash);
    error UnknownReport(bytes32 reportHash);
    error InvalidVerdict();
    error NotUnderReview(bytes32 reportHash);
    error NotMintable(bytes32 reportHash, Decision decision);
    error ReportAlreadyUsed(bytes32 reportHash, uint256 tokenId);
    error ImageAlreadyMinted(bytes32 imageHash, uint256 tokenId);

    constructor(address admin, address verifier, address reviewer) ERC721("MintGate Verified Art", "MGVA") {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(VERIFIER_ROLE, verifier);
        _grantRole(REVIEWER_ROLE, reviewer);
    }

    /// @notice Anchor an off-chain verification report. Called by the Mint-Gate backend.
    function recordVerification(
        bytes32 reportHash,
        bytes32 imageHash,
        uint8 score,
        Decision decision,
        bytes32 policyHash,
        string calldata policyVersion
    ) external onlyRole(VERIFIER_ROLE) {
        if (reportHash == bytes32(0) || imageHash == bytes32(0) || score > 100) revert InvalidVerdict();
        if (decision != Decision.PASS && decision != Decision.REVIEW && decision != Decision.FAIL)
            revert InvalidVerdict();
        if (_verifications[reportHash].decision != Decision.NONE) revert AlreadyRecorded(reportHash);

        _verifications[reportHash] = Verification({
            imageHash: imageHash,
            policyHash: policyHash,
            score: score,
            decision: decision,
            timestamp: uint64(block.timestamp),
            verifier: msg.sender,
            tokenId: 0,
            policyVersion: policyVersion
        });
        totalVerifications++;
        emit VerificationRecorded(reportHash, imageHash, score, decision, policyVersion, msg.sender);
    }

    /// @notice Human-in-the-loop resolution of a REVIEW verdict.
    function resolveReview(bytes32 reportHash, bool approved) external onlyRole(REVIEWER_ROLE) {
        Verification storage v = _verifications[reportHash];
        if (v.decision != Decision.REVIEW) revert NotUnderReview(reportHash);
        v.decision = approved ? Decision.REVIEW_APPROVED : Decision.REVIEW_REJECTED;
        emit ReviewResolved(reportHash, approved, msg.sender);
    }

    /// @notice Mint an NFT backed by a passing verification. One token per report and per image.
    function mintVerified(address to, bytes32 reportHash, string calldata uri)
        external
        onlyRole(VERIFIER_ROLE)
        returns (uint256 tokenId)
    {
        Verification storage v = _verifications[reportHash];
        if (v.decision == Decision.NONE) revert UnknownReport(reportHash);
        if (v.decision != Decision.PASS && v.decision != Decision.REVIEW_APPROVED)
            revert NotMintable(reportHash, v.decision);
        if (v.tokenId != 0) revert ReportAlreadyUsed(reportHash, v.tokenId);
        uint256 existing = tokenOfImage[v.imageHash];
        if (existing != 0) revert ImageAlreadyMinted(v.imageHash, existing);

        tokenId = _nextTokenId++;
        v.tokenId = tokenId;
        tokenOfImage[v.imageHash] = tokenId;
        reportOfToken[tokenId] = reportHash;

        _safeMint(to, tokenId);
        _setTokenURI(tokenId, uri);
        emit VerifiedMint(tokenId, to, reportHash, v.imageHash, v.score);
    }

    function getVerification(bytes32 reportHash) external view returns (Verification memory) {
        return _verifications[reportHash];
    }

    function totalMinted() external view returns (uint256) {
        return _nextTokenId - 1;
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721URIStorage, AccessControl)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
