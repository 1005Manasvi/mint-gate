"""Pin minted artwork, metadata and verification report to IPFS via Pinata.

When PINATA_JWT is not configured (local development) the caller falls back to
URLs served by this backend.
"""

import json

import requests

from config import IPFS_GATEWAY, PINATA_JWT

PIN_FILE = "https://api.pinata.cloud/pinning/pinFileToIPFS"
PIN_JSON = "https://api.pinata.cloud/pinning/pinJSONToIPFS"


class IPFSError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(PINATA_JWT)


def _headers():
    return {"Authorization": f"Bearer {PINATA_JWT}"}


def pin_file(data: bytes, filename: str, content_type: str) -> str:
    r = requests.post(
        PIN_FILE,
        headers=_headers(),
        timeout=120,
        files={"file": (filename, data, content_type)},
        data={"pinataMetadata": json.dumps({"name": filename})},
    )
    if r.status_code != 200:
        raise IPFSError(f"Pinata file upload failed ({r.status_code}): {r.text[:200]}")
    return r.json()["IpfsHash"]


def pin_json(obj: dict, name: str) -> str:
    r = requests.post(
        PIN_JSON, headers=_headers(), timeout=60, json={"pinataContent": obj, "pinataMetadata": {"name": name}}
    )
    if r.status_code != 200:
        raise IPFSError(f"Pinata JSON upload failed ({r.status_code}): {r.text[:200]}")
    return r.json()["IpfsHash"]


def gateway_url(uri: str) -> str:
    """ipfs://CID[/path] -> https gateway URL; other URLs unchanged."""
    return IPFS_GATEWAY + uri[len("ipfs://") :] if uri.startswith("ipfs://") else uri
