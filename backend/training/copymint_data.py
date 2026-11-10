"""Phase 5b evaluation set: REAL copymint collections (forgeries of indexed NFTs, minted on-chain).

Every collection below was verified with Alchemy `getContractMetadata` (name, symbol, supply,
OpenSea slug / description) and a public source (Etherscan token page / OpenSea collection page).
They are forgeries, NOT protected works: their images live in `data/copymint/` and are never
added to `reference_items`.

  RR/BAYC                Ethereum 0x2EE6AF0dFf3A1CE3F7E3414C52c48fd50d73691e  re-mint of BAYC (same art)
  CryptoPhunks V2        Ethereum 0xf07468eAd8cf26c752C676E43C814FEe9c8CF402  CryptoPunks mirrored left-right
  Phunky Ape Yacht Club  Ethereum 0x176e0fe17314def59f0f06e976e1b74203be4a55  BAYC mirrored left-right

Pairing (`original_token`): CryptoPhunks and PHAYC reuse the original's token id; RR/BAYC tokens
have their own ids and name the original in their metadata ("#7400"). Every pair is checked on the
pixels: `mapping_mad` = mean absolute RGB difference of the foreground at 64 px (area-averaged, the copy un-mirrored)
between the copy and the indexed original; resampling-robust, so it verifies the mapping, not the
engine. Originals: BAYC on-chain index rows (`1:<BAYC>:<id>`), CryptoPunks = the seed collection
(`cryptopunkN.jpg`) or Wrapped CryptoPunks rows. The 1,000 CryptoPunks held out of the index as the
genuine "new punk" test set are deliberately not indexed: copies of them are reported as
"original not indexed", never as detector misses.

Files are the token images as a copyminter would upload them: Alchemy's CDN copy of the original
file (`cachedUrl`), else the on-chain `tokenURI` -> metadata -> image through a public IPFS gateway.
Output: data/copymint/images/* + data/copymint/manifest.json + collections.json
Run from backend/:   python -m training.copymint_data [--n 300] [--only rrbayc,cryptophunks,phayc] [--remap]
"""

import argparse
import io
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image
from sqlalchemy import text
from web3 import Web3

import db
from config import BACKEND_DIR
from detection.copy_index import resolve_image
from detection.preprocessing import load_path
from ingest.alchemy import Alchemy

OUT = BACKEND_DIR / "data" / "copymint"
IMG_DIR = OUT / "images"
MANIFEST = OUT / "manifest.json"
MAX_BYTES = 25_000_000
MAPPING_MAX_MAD = 12.0  # correct pairs measure ~1-6, a wrong token ~40+ (see the run log)
GATEWAYS = ("https://gateway.pinata.cloud/ipfs/", "https://ipfs.io/ipfs/", "https://dweb.link/ipfs/")
TOKEN_URI = Web3.keccak(text="tokenURI(uint256)")[:4].hex().removeprefix("0x")

BAYC = "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D"
WPUNKS = "0xb7F7F6C52F2e2fdb1963Eab30438024864c313F6"

COLLECTIONS = {
    "rrbayc": {
        "name": "RR/BAYC",
        "chain": "Ethereum",
        "chain_id": 1,
        "address": "0x2EE6AF0dFf3A1CE3F7E3414C52c48fd50d73691e",
        "copies": "Bored Ape Yacht Club",
        "transform": "none",
        "mapping": "name",
        "evidence": (
            "Alchemy getContractMetadata: name 'Bored Ape Yacht Club', symbol BAYC, 9,532 tokens; OpenSea slug "
            "'rrbayc' ('you can't copy an NFT. conceptual art by Ryder Ripps', rrbayc.com). Subject of Yuga Labs "
            "v. Ripps (C.D. Cal., filed June 2022). Token metadata names the copied ape ('#7400')."
        ),
        "links": [
            "https://opensea.io/collection/rrbayc",
            "https://etherscan.io/token/0x2EE6AF0dFf3A1CE3F7E3414C52c48fd50d73691e",
        ],
    },
    "cryptophunks": {
        "name": "CryptoPhunks V2",
        "chain": "Ethereum",
        "chain_id": 1,
        "address": "0xf07468eAd8cf26c752C676E43C814FEe9c8CF402",
        "copies": "CryptoPunks",
        "transform": "mirror",
        "mapping": "same_id",
        "evidence": (
            "Alchemy getContractMetadata: name CryptoPhunksV2, symbol PHUNK, 10,000 tokens; OpenSea slug "
            "'crypto-phunks' (verified): 'CryptoPhunks face left'. Etherscan token page; V1 contract "
            "0xA82F3a61F002F83Eba7D184c50bB2a8B359cA1cE not used."
        ),
        "links": [
            "https://opensea.io/collection/crypto-phunks",
            "https://etherscan.io/token/0xf07468ead8cf26c752c676e43c814fee9c8cf402",
        ],
    },
    "phayc": {
        "name": "Phunky Ape Yacht Club",
        "chain": "Ethereum",
        "chain_id": 1,
        "address": "0x176e0fe17314def59f0f06e976e1b74203be4a55",
        "copies": "Bored Ape Yacht Club",
        "transform": "mirror",
        "mapping": "same_id",
        "evidence": (
            "Alchemy getContractMetadata: name PhunkyApeYachtClub, symbol PAYC, 9,999 tokens; OpenSea slug "
            "'phunkyapeyachtclub' ('Lets get phunky. Not affiliated with Yuga Labs.'); Etherscan: verified "
            "contract PhunkyApeYachtClub."
        ),
        "links": [
            "https://opensea.io/collection/phunkyapeyachtclub",
            "https://etherscan.io/token/0x176e0fe17314def59f0f06e976e1b74203be4a55",
        ],
    },
}


def log(*a):
    print(*a, flush=True)


def token_ids(al: Alchemy, address: str) -> list[str]:
    ids, key = [], None
    while True:
        params = {"contractAddress": address, "withMetadata": "false", "limit": 100}
        if key:
            params["pageKey"] = key
        page = al._request("GET", "nft", "/getNFTsForContract", params=params)
        ids += [str(int(n["tokenId"])) for n in page.get("nfts", [])]
        key = page.get("pageKey")
        if not key:
            return ids


def original_refs(kind: str, ids: list[str]) -> dict:
    """original token id -> (reference name, source, image_uri) of the indexed original."""
    out = {}
    with db.session() as s:
        contract = BAYC if kind == "Bored Ape Yacht Club" else WPUNKS
        rows = s.execute(
            text(
                "SELECT token_id, contract, image_uri FROM reference_items WHERE source = 'alchemy' "
                "AND status = 'ready' AND chain_id = 1 AND lower(contract) = lower(:c)"
            ),
            {"c": contract},
        ).all()
        for tid, c, uri in rows:
            out[str(int(tid))] = (f"1:{c}:{tid}", "alchemy", uri)
        if kind == "CryptoPunks":
            seeds = {
                r[0]
                for r in s.execute(
                    text("SELECT image_uri FROM reference_items WHERE source = 'seed' AND status = 'ready'")
                )
            }
            for t in ids:
                uri = f"seed/cryptopunk{t}.jpg"
                if uri in seeds:  # the seed punk wins: same image, larger file
                    out[t] = (uri.removeprefix("seed/"), "seed", uri)
    return {t: out[t] for t in ids if t in out}


def unmirror(img: Image.Image, transform: str) -> Image.Image:
    return img.transpose(Image.FLIP_LEFT_RIGHT) if transform == "mirror" else img


def _flat(img: Image.Image, px=64) -> np.ndarray:
    """RGB on white (transparent corners), area-averaged to px x px."""
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return np.asarray(Image.alpha_composite(bg, img).convert("RGB").resize((px, px), Image.BOX), np.float32)


def _background(a: np.ndarray) -> np.ndarray:
    """Median colour of the 1-pixel border (the flat background of PFP / pixel-art NFTs)."""
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return np.median(border, axis=0)


def mapping_check(path, rec) -> tuple[float | None, float | None]:
    """(foreground MAD, background colour distance). Background pixels (within 24 of the image's own
    border colour in BOTH images) are left out of the MAD: the seed CryptoPunks carry Larva Labs'
    red / purple "for sale" / "bid" backgrounds, the copies the standard blue one."""
    if not rec.get("original_uri"):
        return None, None
    ref_path = resolve_image(rec["original_source"], rec["original_uri"])
    if ref_path is None or not ref_path.exists():
        return None, None
    q = _flat(unmirror(load_path(path), rec["transform"]))
    r = _flat(load_path(ref_path))
    bq, br = _background(q), _background(r)
    bg = (np.abs(q - bq).max(-1) <= 24) & (np.abs(r - br).max(-1) <= 24)
    fg = ~bg if (~bg).sum() >= 64 else np.ones(bg.shape, bool)
    return round(float(np.abs(q - r)[fg].mean()), 2), round(float(np.abs(bq - br).max()), 1)


def ipfs_get(http, uri: str):
    if uri.startswith("ipfs://"):
        path = uri.removeprefix("ipfs://").removeprefix("ipfs/")
        urls = [g + path for g in GATEWAYS]
    else:
        urls = [uri]
    for u in urls:
        try:
            r = http.get(u, timeout=60)
            if r.status_code == 200 and 0 < len(r.content) <= MAX_BYTES:
                return r.content
        except requests.RequestException:
            continue
    return None


def onchain_token_uri(al: Alchemy, address: str, tid: str) -> str | None:
    data = "0x" + TOKEN_URI + hex(int(tid))[2:].rjust(64, "0")
    try:
        b = bytes.fromhex(al.rpc("eth_call", [{"to": address, "data": data}, "latest"])[2:])
        n = int.from_bytes(b[32:64], "big")
        return b[64 : 64 + n].decode()
    except Exception:
        return None


def _uploadable(data: bytes) -> bool:
    from detection.preprocessing import ALLOWED_FORMATS

    try:
        return (Image.open(io.BytesIO(data)).format or "") in ALLOWED_FORMATS
    except Exception:
        return False


def fetch_token(al: Alchemy, http, spec: dict, tid: str):
    """(bytes, metadata name, original url, source) or (None, reason). A cached file in a format
    Mint-Gate does not accept (AVIF) is replaced by Alchemy's PNG rendition of it."""
    name = original_url = None
    for _attempt in range(2):  # Alchemy caches lazily: a token without an image often has one seconds later
        m = al.nft_metadata(spec["address"], tid)
        img = m.get("image") or {}
        meta = (m.get("raw") or {}).get("metadata") or {}
        name = meta.get("name") if isinstance(meta, dict) else None
        original_url = img.get("originalUrl") or (meta.get("image") if isinstance(meta, dict) else None)
        for url, source in ((img.get("cachedUrl"), "alchemy_cdn"), (img.get("pngUrl"), "alchemy_png")):
            if not url:
                continue
            r = http.get(url, timeout=90)
            if r.status_code == 200 and 0 < len(r.content) <= MAX_BYTES and _uploadable(r.content):
                return r.content, name, original_url, source
        time.sleep(3)
    uri = onchain_token_uri(al, spec["address"], tid)
    if not uri:
        return None, "no tokenURI"
    raw = ipfs_get(http, uri)
    if raw is None:
        return None, "metadata unreachable"
    try:
        meta = json.loads(raw)
    except ValueError:
        return None, "metadata not JSON"
    name, original_url = meta.get("name") or name, meta.get("image")
    data = ipfs_get(http, original_url) if original_url else None
    if data is None:
        return None, "image unreachable"
    return data, name, original_url, "ipfs_gateway"


def original_token(spec, tid, name):
    if spec["mapping"] == "same_id":
        return tid
    m = re.fullmatch(r"\s*#?(\d+)\s*", str(name or ""))
    return str(int(m.group(1))) if m else None


def fetch_collection(al: Alchemy, key: str, spec: dict, n: int, records: list, seen: set):
    ids = token_ids(al, spec["address"])
    log(f"[copymint] {spec['name']}: {len(ids)} tokens on-chain")
    picked = sorted(random.Random(f"copymint-{key}").sample(ids, min(n, len(ids))), key=int)
    http = requests.Session()
    have = {r["token_id"] for r in records if r["collection"] == key}

    def get(tid):
        if tid in have:
            return tid, None
        for attempt in range(4):
            try:
                return tid, fetch_token(al, http, spec, tid)
            except Exception as exc:  # flaky network: retry, never crash the run
                err = type(exc).__name__
                time.sleep(3 + 3 * attempt)
        return tid, (None, err)

    added = 0
    with ThreadPoolExecutor(6) as ex:
        for tid, got in ex.map(get, picked):
            if got is None:
                continue
            if got[0] is None:
                log(f"[copymint] {key} #{tid}: skipped ({got[1]})")
                continue
            data, name, original_url, source = got
            try:
                im = Image.open(io.BytesIO(data))
                im.load()
                ext = (im.format or "png").lower().replace("jpeg", "jpg")
            except Exception as exc:
                log(f"[copymint] {key} #{tid}: not an image ({type(exc).__name__})")
                continue
            fname = f"{key}_{tid}.{ext}"
            (IMG_DIR / fname).write_bytes(data)
            records.append(
                {
                    "file": fname,
                    "collection": key,
                    "token_id": tid,
                    "chain_id": spec["chain_id"],
                    "contract": spec["address"],
                    "metadata_name": name,
                    "original_url": original_url,
                    "image_source": source,
                    "copies": spec["copies"],
                    "transform": spec["transform"],
                    "original_token": original_token(spec, tid, name),
                }
            )
            seen.add(fname)
            added += 1
    log(f"[copymint] {spec['name']}: {added} new images")


def remap(records):
    """Attach the indexed original (if any) and the pixel check of the pairing."""
    for key in {r["collection"] for r in records}:
        rs = [r for r in records if r["collection"] == key]
        refs = original_refs(COLLECTIONS[key]["copies"], [r["original_token"] for r in rs if r["original_token"]])
        for r in rs:
            ref = refs.get(r["original_token"])
            r["original_ref"], r["original_source"], r["original_uri"] = ref if ref else (None, None, None)
            r["mapping_mad"], r["background_diff"] = mapping_check(IMG_DIR / r["file"], r)
            r.pop("mapping_diff_px", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--only", default=",".join(COLLECTIONS))
    ap.add_argument("--remap", action="store_true", help="only recompute originals + mapping checks")
    args = ap.parse_args()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    stale = [r for r in records if not _uploadable((IMG_DIR / r["file"]).read_bytes())]
    if stale:  # fetched before the format check: drop and fetch again
        for r in stale:
            (IMG_DIR / r["file"]).unlink()
        records = [r for r in records if r not in stale]
        log(f"[copymint] refetching {len(stale)} files in unsupported formats")
        args.remap = False
        args.only = ",".join(sorted({r["collection"] for r in stale}))
    if not args.remap:
        al = Alchemy("eth-mainnet")
        seen = {r["file"] for r in records}
        for key in args.only.split(","):
            fetch_collection(al, key, COLLECTIONS[key], args.n, records, seen)
            MANIFEST.write_text(json.dumps(records, indent=0))
    remap(records)
    MANIFEST.write_text(json.dumps(records, indent=0))
    (OUT / "collections.json").write_text(json.dumps(COLLECTIONS, indent=2))
    for key in COLLECTIONS:
        rs = [r for r in records if r["collection"] == key]
        mads = [r["mapping_mad"] for r in rs if r.get("mapping_mad") is not None]
        log(
            f"{key:14s} n={len(rs):4d} originals indexed {sum(1 for r in rs if r.get('original_ref'))}"
            f"  pairing verified (MAD <= {MAPPING_MAX_MAD}) {sum(1 for d in mads if d <= MAPPING_MAX_MAD)}/{len(mads)}"
            f"  MAD median {np.median(mads) if mads else '-'} max {max(mads) if mads else '-'}"
        )


if __name__ == "__main__":
    main()
