// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControlDefaultAdminRules} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Address} from "@openzeppelin/contracts/utils/Address.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IMintGate} from "./IMintGate.sol";

/**
 * @title MintGateRegistry
 * @notice Verdict registry for AI-verified artwork, independent of any NFT contract.
 *
 * The Mint-Gate backend signs EIP-712 verdicts off chain; anyone submits them. Approved
 * collections `consume` a passing verdict when the artist mints, which allows exactly one token
 * per image across all collections. Minted images can be disputed (with a bond) and revoked by a
 * reviewer. Revocation is a permanent status flag, not a burn. See docs/contracts.md.
 */
contract MintGateRegistry is IMintGate, EIP712, AccessControlDefaultAdminRules, Pausable {
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER_ROLE");
    bytes32 public constant REVIEWER_ROLE = keccak256("REVIEWER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant COLLECTION_ROLE = keccak256("COLLECTION_ROLE");

    bytes32 public constant VERDICT_TYPEHASH =
        keccak256(
            "Verdict(bytes32 imageHash,bytes32 reportHash,bytes32 policyHash,uint8 score,uint8 decision,"
            "address artist,uint64 issuedAt,uint64 expiresAt,uint256 nonce)"
        );

    uint64 public constant MAX_VALIDITY = 30 days; // longest allowed signature window
    uint64 public constant MAX_CLOCK_SKEW = 15 minutes; // backend clock vs block time

    enum DisputeStatus {
        NONE,
        OPEN,
        UPHELD,
        REJECTED
    }

    struct Dispute {
        bytes32 imageHash;
        address disputant;
        uint96 bond;
        DisputeStatus status;
        uint64 openedAt;
        uint64 resolvedAt;
        string evidenceURI;
    }

    struct ImageRecord {
        address collection; // 0 until minted
        bool revoked;
        uint256 tokenId;
        bytes32 reportHash;
        uint256 openDispute; // disputeId, 0 if none
    }

    mapping(bytes32 reportHash => VerdictRecord) private _verdicts;
    mapping(bytes32 imageHash => mapping(address artist => bytes32 reportHash)) private _passing;
    mapping(bytes32 imageHash => ImageRecord) private _images;
    mapping(address signer => mapping(uint256 nonce => bool)) public nonceUsed;
    mapping(uint256 disputeId => Dispute) private _disputes;
    mapping(address account => uint256) public credits; // withdrawable bond payouts
    mapping(address artist => uint256) public strikes; // upheld disputes against the artist

    uint256 public totalVerdicts;
    uint256 public totalMints;
    uint256 public totalDisputes;
    uint256 public disputeBond;
    address public treasury;

    event VerdictSubmitted(
        bytes32 indexed reportHash,
        bytes32 indexed imageHash,
        address indexed artist,
        uint8 score,
        Decision decision,
        bytes32 policyHash,
        address signer
    );
    event ReviewResolved(bytes32 indexed reportHash, bool approved, address indexed reviewer);
    event VerdictVoided(bytes32 indexed reportHash, address indexed reviewer);
    event NonceCancelled(address indexed signer, uint256 nonce);
    event ImageMinted(
        bytes32 indexed imageHash,
        bytes32 indexed reportHash,
        address indexed collection,
        uint256 tokenId,
        address artist
    );
    event DisputeOpened(
        uint256 indexed disputeId,
        bytes32 indexed imageHash,
        address indexed disputant,
        uint256 bond,
        string evidenceURI
    );
    event DisputeResolved(uint256 indexed disputeId, bytes32 indexed imageHash, bool upheld, address reviewer);
    event ImageRevoked(bytes32 indexed imageHash, address indexed artist, uint256 indexed disputeId);
    event Withdrawal(address indexed account, uint256 amount);
    event DisputeBondChanged(uint256 bond);
    event TreasuryChanged(address indexed treasury);

    error InvalidVerdict();
    error VerdictExpired(uint64 expiresAt);
    error InvalidValidity();
    error UnauthorizedSigner(address signer);
    error NonceAlreadyUsed(address signer, uint256 nonce);
    error AlreadyRecorded(bytes32 reportHash);
    error NotUnderReview(bytes32 reportHash);
    error CannotVoid(bytes32 reportHash, Decision decision);
    error NotMintable(bytes32 imageHash, address artist);
    error ImageAlreadyMinted(bytes32 imageHash, address collection, uint256 tokenId);
    error NotMinted(bytes32 imageHash);
    error AlreadyRevoked(bytes32 imageHash);
    error DisputeAlreadyOpen(uint256 disputeId);
    error WrongBond(uint256 expected);
    error EmptyEvidence();
    error DisputeNotOpen(uint256 disputeId);
    error NothingToWithdraw();
    error ZeroAddress();

    constructor(
        uint48 adminDelay,
        address admin,
        address verifier,
        address reviewer,
        address pauser,
        address treasury_,
        uint256 disputeBond_
    ) EIP712("MintGateRegistry", "1") AccessControlDefaultAdminRules(adminDelay, admin) {
        if (verifier == address(0) || reviewer == address(0) || pauser == address(0)) revert ZeroAddress();
        _grantRole(VERIFIER_ROLE, verifier);
        _grantRole(REVIEWER_ROLE, reviewer);
        _grantRole(PAUSER_ROLE, pauser);
        _setTreasury(treasury_);
        _setDisputeBond(disputeBond_);
    }

    // ---- verdicts --------------------------------------------------------------------------

    /// @inheritdoc IMintGate
    function submitVerdict(Verdict calldata v, bytes calldata signature) external whenNotPaused returns (address) {
        if (v.imageHash == bytes32(0) || v.reportHash == bytes32(0) || v.artist == address(0) || v.score > 100)
            revert InvalidVerdict();
        if (v.decision != Decision.PASS && v.decision != Decision.REVIEW && v.decision != Decision.FAIL)
            revert InvalidVerdict();
        // Expiry windows are hours to days; validators can only shift block time by seconds.
        // slither-disable-next-line timestamp
        if (block.timestamp >= v.expiresAt) revert VerdictExpired(v.expiresAt);
        // slither-disable-next-line timestamp
        if (
            v.expiresAt <= v.issuedAt ||
            v.expiresAt - v.issuedAt > MAX_VALIDITY ||
            v.issuedAt > block.timestamp + MAX_CLOCK_SKEW
        ) revert InvalidValidity();

        address signer = ECDSA.recover(hashVerdict(v), signature);
        if (!hasRole(VERIFIER_ROLE, signer)) revert UnauthorizedSigner(signer);
        if (nonceUsed[signer][v.nonce]) revert NonceAlreadyUsed(signer, v.nonce);
        if (_verdicts[v.reportHash].decision != Decision.NONE) revert AlreadyRecorded(v.reportHash);

        nonceUsed[signer][v.nonce] = true;
        _verdicts[v.reportHash] = VerdictRecord({
            imageHash: v.imageHash,
            policyHash: v.policyHash,
            artist: v.artist,
            score: v.score,
            decision: v.decision,
            issuedAt: v.issuedAt,
            signer: signer,
            expiresAt: v.expiresAt
        });
        if (v.decision == Decision.PASS) _passing[v.imageHash][v.artist] = v.reportHash;
        totalVerdicts++;
        emit VerdictSubmitted(v.reportHash, v.imageHash, v.artist, v.score, v.decision, v.policyHash, signer);
        return signer;
    }

    /// @notice EIP-712 digest of a verdict: what the verifier signs.
    function hashVerdict(Verdict calldata v) public view returns (bytes32) {
        // Verdict has only static fields, so abi.encode(v) is the field-by-field encoding.
        return _hashTypedDataV4(keccak256(abi.encode(VERDICT_TYPEHASH, v)));
    }

    /// @notice Cancel a signature the caller issued but that is not on chain yet.
    function cancelNonce(uint256 nonce) external {
        if (nonceUsed[msg.sender][nonce]) revert NonceAlreadyUsed(msg.sender, nonce);
        nonceUsed[msg.sender][nonce] = true;
        emit NonceCancelled(msg.sender, nonce);
    }

    /// @notice Human-in-the-loop resolution of a REVIEW verdict.
    function resolveReview(bytes32 reportHash, bool approved) external onlyRole(REVIEWER_ROLE) {
        VerdictRecord storage r = _verdicts[reportHash];
        if (r.decision != Decision.REVIEW) revert NotUnderReview(reportHash);
        if (approved) {
            r.decision = Decision.REVIEW_APPROVED;
            _passing[r.imageHash][r.artist] = reportHash;
        } else {
            r.decision = Decision.REVIEW_REJECTED;
        }
        emit ReviewResolved(reportHash, approved, msg.sender);
    }

    /// @notice Invalidate a verdict that has not been used to mint (e.g. signed by a leaked key).
    function voidVerdict(bytes32 reportHash) external onlyRole(REVIEWER_ROLE) {
        VerdictRecord storage r = _verdicts[reportHash];
        Decision d = r.decision;
        if (d != Decision.PASS && d != Decision.REVIEW && d != Decision.REVIEW_APPROVED)
            revert CannotVoid(reportHash, d);
        // A used verdict is part of a mint; that is what disputes are for.
        if (_images[r.imageHash].reportHash == reportHash) revert CannotVoid(reportHash, d);
        r.decision = Decision.VOIDED;
        if (_passing[r.imageHash][r.artist] == reportHash) delete _passing[r.imageHash][r.artist];
        emit VerdictVoided(reportHash, msg.sender);
    }

    /// @inheritdoc IMintGate
    function isMintable(bytes32 imageHash, address artist) public view returns (bool, bytes32 reportHash) {
        reportHash = _passing[imageHash][artist];
        return (reportHash != bytes32(0) && _images[imageHash].collection == address(0) && !paused(), reportHash);
    }

    /// @inheritdoc IMintGate
    function consume(
        bytes32 imageHash,
        address artist,
        uint256 tokenId
    ) external onlyRole(COLLECTION_ROLE) whenNotPaused returns (bytes32 reportHash) {
        ImageRecord storage img = _images[imageHash];
        if (img.collection != address(0)) revert ImageAlreadyMinted(imageHash, img.collection, img.tokenId);
        reportHash = _passing[imageHash][artist];
        if (reportHash == bytes32(0)) revert NotMintable(imageHash, artist);

        img.collection = msg.sender;
        img.tokenId = tokenId;
        img.reportHash = reportHash;
        totalMints++;
        emit ImageMinted(imageHash, reportHash, msg.sender, tokenId, artist);
    }

    // ---- disputes --------------------------------------------------------------------------

    /// @notice Contest a minted image (plagiarism, undetected AI, ...). Costs `disputeBond`,
    ///         returned if a reviewer upholds the dispute, forfeited to the treasury otherwise.
    function openDispute(bytes32 imageHash, string calldata evidenceURI) external payable returns (uint256 disputeId) {
        ImageRecord storage img = _images[imageHash];
        if (img.collection == address(0)) revert NotMinted(imageHash);
        if (img.revoked) revert AlreadyRevoked(imageHash);
        if (img.openDispute != 0) revert DisputeAlreadyOpen(img.openDispute);
        if (msg.value != disputeBond) revert WrongBond(disputeBond);
        if (bytes(evidenceURI).length == 0) revert EmptyEvidence();

        disputeId = ++totalDisputes;
        img.openDispute = disputeId;
        _disputes[disputeId] = Dispute({
            imageHash: imageHash,
            disputant: msg.sender,
            bond: uint96(msg.value), // == disputeBond, which _setDisputeBond caps at uint96
            status: DisputeStatus.OPEN,
            openedAt: uint64(block.timestamp),
            resolvedAt: 0,
            evidenceURI: evidenceURI
        });
        emit DisputeOpened(disputeId, imageHash, msg.sender, msg.value, evidenceURI);
    }

    function resolveDispute(uint256 disputeId, bool upheld) external onlyRole(REVIEWER_ROLE) {
        Dispute storage d = _disputes[disputeId];
        if (d.status != DisputeStatus.OPEN) revert DisputeNotOpen(disputeId);
        ImageRecord storage img = _images[d.imageHash];

        d.status = upheld ? DisputeStatus.UPHELD : DisputeStatus.REJECTED;
        d.resolvedAt = uint64(block.timestamp);
        img.openDispute = 0;
        if (upheld) {
            address artist = _verdicts[img.reportHash].artist;
            img.revoked = true;
            strikes[artist]++;
            credits[d.disputant] += d.bond;
            emit ImageRevoked(d.imageHash, artist, disputeId);
        } else {
            credits[treasury] += d.bond;
        }
        emit DisputeResolved(disputeId, d.imageHash, upheld, msg.sender);
    }

    /// @notice Pull payment of returned or forfeited bonds.
    function withdraw() external {
        uint256 amount = credits[msg.sender];
        // `credits` only changes through resolveDispute / withdraw (no forced balance), so == 0 is exact.
        // slither-disable-next-line incorrect-equality,timestamp
        if (amount == 0) revert NothingToWithdraw();
        credits[msg.sender] = 0;
        emit Withdrawal(msg.sender, amount);
        Address.sendValue(payable(msg.sender), amount);
    }

    // ---- views -----------------------------------------------------------------------------

    /// @inheritdoc IMintGate
    function verdictOf(bytes32 reportHash) external view returns (VerdictRecord memory) {
        return _verdicts[reportHash];
    }

    /// @inheritdoc IMintGate
    function imageStatus(bytes32 imageHash) public view returns (ImageStatus) {
        ImageRecord storage img = _images[imageHash];
        if (img.revoked) return ImageStatus.REVOKED;
        if (img.openDispute != 0) return ImageStatus.DISPUTED;
        if (img.collection != address(0)) return ImageStatus.MINTED;
        return ImageStatus.NONE;
    }

    /// @inheritdoc IMintGate
    function isRevoked(bytes32 imageHash) external view returns (bool) {
        return _images[imageHash].revoked;
    }

    function mintOf(
        bytes32 imageHash
    ) external view returns (address collection, uint256 tokenId, bytes32 reportHash, uint256 activeDispute) {
        ImageRecord storage img = _images[imageHash];
        return (img.collection, img.tokenId, img.reportHash, img.openDispute);
    }

    function disputeOf(uint256 disputeId) external view returns (Dispute memory) {
        return _disputes[disputeId];
    }

    // ---- governance ------------------------------------------------------------------------

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    /// @dev Admin only: a leaked pauser key can freeze the registry but not unfreeze it.
    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    function setDisputeBond(uint256 bond) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setDisputeBond(bond);
    }

    function setTreasury(address treasury_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setTreasury(treasury_);
    }

    function supportsInterface(bytes4 interfaceId) public view override returns (bool) {
        return interfaceId == type(IMintGate).interfaceId || super.supportsInterface(interfaceId);
    }

    function _setDisputeBond(uint256 bond) private {
        disputeBond = SafeCast.toUint96(bond);
        emit DisputeBondChanged(bond);
    }

    function _setTreasury(address treasury_) private {
        if (treasury_ == address(0)) revert ZeroAddress();
        treasury = treasury_;
        emit TreasuryChanged(treasury_);
    }
}
