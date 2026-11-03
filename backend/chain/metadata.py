"""ERC-721 metadata for a verified artwork: pinned to IPFS (Pinata), or served by this app locally."""

import json

import config
from chain import ipfs

PRIVATE_FIELDS = ("evidence", "review", "publication", "attestations", "artist", "status", "legacy_v1", "claim")


def publish(report: dict) -> dict:
    """Store image, public report and metadata; returns {"token_uri", "image", "report"}."""
    rid = report["report_id"]
    image_bytes = (config.UPLOADS_DIR / f"{rid}.png").read_bytes()
    public_report = {k: v for k, v in report.items() if k not in PRIVATE_FIELDS}
    status = "REVIEW_APPROVED" if report.get("status") == "REVIEW_APPROVED" else report["decision"]
    attributes = [
        {"trait_type": "Authenticity score", "value": report["score"]},
        {"trait_type": "Mint-Gate decision", "value": status},
        {"trait_type": "Policy version", "value": report["policy_version"]},
    ]
    if ipfs.enabled():
        image_uri = f"ipfs://{ipfs.pin_file(image_bytes, f'{rid}.png', 'image/png')}"
        # Pinned as a file, not with pinJSONToIPFS: Pinata re-serialises JSON (1.0 -> 1), and the
        # provenance page re-hashes these exact bytes in the browser against the on-chain reportHash.
        report_bytes = json.dumps(public_report, sort_keys=True, indent=1).encode()
        report_uri = f"ipfs://{ipfs.pin_file(report_bytes, f'mintgate-report-{rid}.json', 'application/json')}"
    else:  # local development fallback
        image_uri = f"{config.PUBLIC_BASE_URL}/api/upload/{rid}.png"
        report_uri = f"{config.PUBLIC_BASE_URL}/api/report/{rid}"
    metadata = {
        "name": f"Mint-Gate verified artwork {report['image_sha256'][:10]}",
        "description": "Artwork verified by the Mint-Gate AI forensics pipeline before minting.",
        "image": image_uri,
        "external_url": f"{config.PUBLIC_BASE_URL}/?report={rid}",
        "attributes": attributes,
        "mint_gate": {
            "report_id": rid,
            "report_hash": report["report_hash"],
            "image_sha256": report["image_sha256"],
            "verification_report": report_uri,
        },
    }
    (config.METADATA_DIR / f"{rid}.json").write_text(json.dumps(metadata, indent=2))
    if ipfs.enabled():
        token_uri = f"ipfs://{ipfs.pin_json(metadata, f'mintgate-metadata-{rid}')}"
    else:
        token_uri = f"{config.PUBLIC_BASE_URL}/metadata/{rid}.json"
    return {"token_uri": token_uri, "image": image_uri, "report": report_uri}
