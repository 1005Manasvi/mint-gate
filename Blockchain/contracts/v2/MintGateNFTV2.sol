// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC721Utils} from "@openzeppelin/contracts/token/ERC721/utils/ERC721Utils.sol";
import {ERC2981} from "@openzeppelin/contracts/token/common/ERC2981.sol";
import {IERC4906} from "@openzeppelin/contracts/interfaces/IERC4906.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {IMintGate} from "./IMintGate.sol";
import {MintGated} from "./MintGated.sol";

/**
 * @title MintGateNFTV2
 * @notice Mint-Gate's own collection. The artist mints from their own wallet with a verdict
 *         from the registry, sets an ERC-2981 royalty for themselves, and the token shows a
 *         "revoked" status if a reviewer upholds a dispute. Ownerless and immutable.
 */
contract MintGateNFTV2 is IERC4906, ERC721, ERC2981, MintGated {
    uint96 public constant MAX_ROYALTY_BPS = 1000; // 10 %

    string public constant REVOKED_URI =
        'data:application/json;utf8,{"name":"Revoked Mint-Gate token","description":"A reviewer upheld a '
        'dispute against this artwork. The dispute and its evidence are recorded in the Mint-Gate registry.",'
        '"attributes":[{"trait_type":"status","value":"revoked"}]}';

    mapping(uint256 tokenId => string) private _tokenURIs;
    mapping(uint256 tokenId => bytes32) public imageOfToken;
    mapping(bytes32 imageHash => uint256) public tokenOfImage;
    uint256 private _lastTokenId;

    event VerifiedMint(uint256 indexed tokenId, address indexed artist, bytes32 indexed imageHash, uint96 royaltyBps);

    error ArtistMismatch(address artist);
    error EmptyURI();
    error RoyaltyTooHigh(uint96 bps);
    error NotRevoked(uint256 tokenId);

    constructor(IMintGate gate) ERC721("MintGate Verified Art v2", "MGVA2") MintGated(gate) {}

    /// @notice Mint with a passing verdict that is already on chain.
    function mint(bytes32 imageHash, string calldata uri, uint96 royaltyBps) external returns (uint256 tokenId) {
        tokenId = _record(imageHash, uri, royaltyBps);
        _finishMint(imageHash, tokenId);
    }

    /// @notice Submit a backend-signed verdict and mint in one transaction. If someone already
    ///         submitted the verdict (e.g. copied it from the mempool), submission is skipped.
    function submitAndMint(
        IMintGate.Verdict calldata v,
        bytes calldata signature,
        string calldata uri,
        uint96 royaltyBps
    ) external returns (uint256 tokenId) {
        if (v.artist != _msgSender()) revert ArtistMismatch(v.artist);
        tokenId = _record(v.imageHash, uri, royaltyBps);
        if (mintGate.verdictOf(v.reportHash).decision == IMintGate.Decision.NONE) {
            // The signer is not needed here: submitVerdict reverts unless it is a verifier.
            // slither-disable-next-line unused-return
            mintGate.submitVerdict(v, signature);
        }
        _finishMint(v.imageHash, tokenId);
    }

    /// @notice Emit ERC-4906 so marketplaces re-read the metadata of a revoked token.
    function refreshMetadata(uint256 tokenId) external {
        if (!isRevoked(tokenId)) revert NotRevoked(tokenId);
        emit MetadataUpdate(tokenId);
    }

    function isRevoked(uint256 tokenId) public view returns (bool) {
        _requireOwned(tokenId);
        return _isImageRevoked(imageOfToken[tokenId]);
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        return isRevoked(tokenId) ? REVOKED_URI : _tokenURIs[tokenId];
    }

    function originalTokenURI(uint256 tokenId) external view returns (string memory) {
        _requireOwned(tokenId);
        return _tokenURIs[tokenId];
    }

    /// @notice ERC-2981; a revoked token pays no royalty.
    function royaltyInfo(
        uint256 tokenId,
        uint256 salePrice
    ) public view override returns (address receiver, uint256 amount) {
        if (_isImageRevoked(imageOfToken[tokenId])) return (address(0), 0);
        return super.royaltyInfo(tokenId, salePrice);
    }

    function totalMinted() external view returns (uint256) {
        return _lastTokenId;
    }

    function supportsInterface(bytes4 interfaceId) public view override(ERC721, ERC2981, IERC165) returns (bool) {
        return interfaceId == bytes4(0x49064906) || super.supportsInterface(interfaceId);
    }

    /// @dev Effects: every state change and event happens here, before any external call.
    function _record(bytes32 imageHash, string calldata uri, uint96 royaltyBps) private returns (uint256 tokenId) {
        if (bytes(uri).length == 0) revert EmptyURI();
        if (royaltyBps > MAX_ROYALTY_BPS) revert RoyaltyTooHigh(royaltyBps);
        address artist = _msgSender();
        tokenId = ++_lastTokenId;
        _tokenURIs[tokenId] = uri;
        imageOfToken[tokenId] = imageHash;
        tokenOfImage[imageHash] = tokenId;
        _setTokenRoyalty(tokenId, artist, royaltyBps);
        _mint(artist, tokenId);
        emit VerifiedMint(tokenId, artist, imageHash, royaltyBps);
    }

    /// @dev Interactions: the registry reverts the whole mint unless the verdict allows it
    ///      (this also guarantees one token per image), then the ERC-721 receiver check runs last.
    function _finishMint(bytes32 imageHash, uint256 tokenId) private {
        _consumeVerdict(imageHash, tokenId);
        ERC721Utils.checkOnERC721Received(_msgSender(), address(0), _msgSender(), tokenId, "");
    }
}
