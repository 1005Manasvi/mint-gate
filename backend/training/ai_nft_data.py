"""Phase 5b: REAL AI-generated NFT collections as an out-of-source AI group (evaluation only).

Collections whose creators STATE that the art is AI-generated, in their own token metadata (the
statement is stored verbatim in `data/ai_nft/collections.json`). The list was shown to the owner
before ingestion (2026-11-10). Collection-level labels are noisy (an artist may retouch outputs):
every number is reported per collection.

  BrainDrops (Ethereum 0xdfde78d2baec499fe18f2be74b6c287eed9511d7, "the home of AI art"): one ERC-721
  contract, project p = token ids p * 1,000,000 + k. Projects kept: those whose metadata names the AI
  method (Brain Loops, Genesis, DreamScapes, Confluence, ClipMatrix Creatures, Life In West America,
  All AI Art Looks The Same, Materia Mania, Mappings, corpo|real, Anthrocinematica, New Realities).
  Dropped: projects that state no method (SIGHTS, Elemental Creation, UBK, The Rapture, ...).
  Bastard GAN Punks V2 (Ethereum 0x31385d3520bced94f77aae104b406994d8f2168c): "trained with GAN" on
  CryptoPunks attributes (pixel-art PFP).
  Dropped: Winds of Yawanawa (Refik Anadol: "Data Paintings", no AI method stated).

None of them is in the training manifest or the Phase 5 OOS set (checked by contract / name).
Original files (Alchemy CDN `cachedUrl`; the PNG render for videos) are kept in data/ai_nft/raw/ for
the provenance check; the OOS copy gets the usual normalisation (`oos_data.Out.add`), group
`ai_nft:<collection>`, domain `ai_nft_collection`, its own select / report split.

Run from backend/:   python -m training.ai_nft_data [--per 40]
"""

import argparse
import io
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import requests
from PIL import Image

from config import BACKEND_DIR
from ingest.alchemy import Alchemy
from training.oos_data import Out

OUT = BACKEND_DIR / "data" / "ai_nft"
RAW_DIR = OUT / "raw"
MAX_BYTES = 25_000_000
RESIZED_PX = 2048
ZERO = "0x0000000000000000000000000000000000000000"

BRAIN = "0xdfde78d2baec499fe18f2be74b6c287eed9511d7"
BGAN = "0x31385d3520bced94f77aae104b406994d8f2168c"
# project id -> (slug, title, AI method as the project states it)
BRAINDROPS = {
    1: ("brain_loops", "Brain Loops", "text-to-image model (looping videos; PNG stills)"),
    3: ("genesis", "Genesis", "'raw AI images'"),
    4: ("dreamscapes", "DreamScapes", "VQGAN+CLIP"),
    5: ("confluence", "Confluence", "neural generative model trained on the artist's sketchbook"),
    11: ("clipmatrix_creatures", "ClipMatrix Creatures", "AI text-to-3D (animated; first frame)"),
    15: ("life_in_west_america", "Life In West America", "custom-trained generative models ('post-photography')"),
    16: ("all_ai_art_looks_the_same", "All AI Art Looks The Same", "14 AI artists, AI collaborative art"),
    17: ("materia_mania", "Materia Mania", "diffusion models"),
    25: ("mappings", "Mappings", "generative AI process"),
    30: ("corpo_real", "corpo|real", "AI-collaborative fashion"),
    32: ("anthrocinematica", "Anthrocinematica", "AI video (PNG stills)"),
    35: ("new_realities", "New Realities", "AI latent space"),
}


def log(*a):
    print(*a, flush=True)


def as_upload(raw: bytes) -> bytes:
    """The file an artist could actually submit: the original if Mint-Gate accepts it (<= 10 MB,
    <= 40 MP, an allowed format), else the artist's obvious fix: longest side 2,048 px, JPEG q95."""
    from config import MAX_IMAGE_PIXELS, MAX_UPLOAD_BYTES
    from detection.preprocessing import ALLOWED_FORMATS

    im = Image.open(io.BytesIO(raw))
    if len(raw) <= MAX_UPLOAD_BYTES and im.width * im.height <= MAX_IMAGE_PIXELS and im.format in ALLOWED_FORMATS:
        return raw
    im.draft("RGB", (RESIZED_PX, RESIZED_PX))
    im = im.convert("RGB")
    im.thumbnail((RESIZED_PX, RESIZED_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def list_tokens(al: Alchemy, address: str) -> list[int]:
    ids, key = [], None
    while True:
        params = {"contractAddress": address, "withMetadata": "false", "limit": 100}
        if key:
            params["pageKey"] = key
        page = al._request("GET", "nft", "/getNFTsForContract", params=params)
        ids += [int(n["tokenId"]) for n in page.get("nfts", [])]
        key = page.get("pageKey")
        if not key:
            return ids


def mint_dates(al: Alchemy, address: str, project_of) -> dict:
    """{group: [first mint, last mint]} (UTC dates) from the contract's mint transfers."""
    out, key = {}, None
    while True:
        p = {
            "fromBlock": "0x0",
            "toBlock": "latest",
            "fromAddress": ZERO,
            "contractAddresses": [address],
            "category": ["erc721"],
            "withMetadata": True,
            "maxCount": "0x3e8",
            "order": "asc",
        }
        if key:
            p["pageKey"] = key
        res = al.rpc("alchemy_getAssetTransfers", [p])
        for t in res.get("transfers", []):
            g = project_of(int(t["erc721TokenId"] or t["tokenId"], 16))
            ts = (t.get("metadata") or {}).get("blockTimestamp", "")[:10]
            if g and ts:
                lo, hi = out.get(g, [ts, ts])
                out[g] = [min(lo, ts), max(hi, ts)]
        key = res.get("pageKey")
        if not key:
            return out


def evidence(al: Alchemy, address: str, token: int, contract_level=False) -> dict:
    """The creator's own statement: the token description (BrainDrops: per project) or, for a
    collection whose token text is not about the art (Bastard GAN Punks: GPT-2 stories), the contract's."""
    if contract_level:
        desc = (al.contract_metadata(address).get("openSeaMetadata") or {}).get("description") or ""
        return {"sample_token": None, "statement": " ".join(desc.split())[:600]}
    m = al.nft_metadata(address, token)
    meta = (m.get("raw") or {}).get("metadata") or {}
    desc = (meta.get("description") if isinstance(meta, dict) else "") or ""
    if not desc:
        desc = ((m.get("contract") or {}).get("openSeaMetadata") or {}).get("description") or ""
    return {"sample_token": str(token), "statement": " ".join(desc.split())[:600]}


def resized_url(cached_url: str, px: int = RESIZED_PX) -> str | None:
    """Alchemy's CDN rendition of a cached image, longest side <= px (its thumbnailUrl scheme)."""
    head, sep, tail = cached_url.partition("nft2-cdn.alchemy.com/")
    if not sep:
        return None
    return f"{head}{sep}cdn-cgi/image/width={px},height={px},fit=scale-down/{tail}"


def download(http, al, address, token):
    """(bytes, content type, original url, source). Originals above MAX_BYTES (BrainDrops ships
    27-44 MB PNGs; Mint-Gate accepts 10 MB uploads) come as the CDN's 2,048 px rendition instead."""
    for attempt in range(4):
        try:
            img = al.nft_metadata(address, token).get("image") or {}
            ct, size, cached = img.get("contentType") or "", img.get("size") or 0, img.get("cachedUrl")
            urls = []
            if ct.startswith("image/") and cached and size <= MAX_BYTES:
                urls.append((cached, "alchemy_cdn"))
            if cached and resized_url(cached):
                urls.append((resized_url(cached), f"alchemy_cdn_{RESIZED_PX}px"))
            if img.get("pngUrl"):
                urls.append((img["pngUrl"], "alchemy_png"))
            for url, source in urls:
                r = http.get(url, timeout=120)
                if r.status_code == 200 and 0 < len(r.content) <= MAX_BYTES:
                    return r.content, ct, img.get("originalUrl"), source
            time.sleep(3)  # Alchemy caches lazily: retry once the image is cached
        except Exception:
            time.sleep(3 + 3 * attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=40, help="tokens per collection")
    args = ap.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    al = Alchemy("eth-mainnet")
    http = requests.Session()

    brain_ids = list_tokens(al, BRAIN)
    log(f"[ai_nft] BrainDrops: {len(brain_ids)} tokens")
    by_proj = {}
    for t in brain_ids:
        by_proj.setdefault(t // 1_000_000, []).append(t)
    bgan_ids = list_tokens(al, BGAN)
    log(f"[ai_nft] Bastard GAN Punks V2: {len(bgan_ids)} tokens")

    colls = {}
    for p, (slug, title, method) in BRAINDROPS.items():
        colls[slug] = {
            "title": title,
            "platform": "BrainDrops",
            "chain": "Ethereum",
            "address": BRAIN,
            "project": p,
            "tokens_on_chain": len(by_proj.get(p, [])),
            "method": method,
            "pool": sorted(by_proj.get(p, [])),
            "link": f"https://braindrops.cloud/projects/{p}",
        }
    colls["bastard_gan_punks_v2"] = {
        "title": "Bastard GAN Punks V2",
        "platform": "own contract",
        "chain": "Ethereum",
        "address": BGAN,
        "tokens_on_chain": len(bgan_ids),
        "method": "GAN trained on CryptoPunks attributes",
        "pool": sorted(bgan_ids),
        "link": "https://opensea.io/collection/bastard-gan-punks-v2",
    }
    proj_slug = {p: s for p, (s, _, _) in BRAINDROPS.items()}
    dates = mint_dates(al, BRAIN, lambda t: proj_slug.get(t // 1_000_000))
    dates.update(mint_dates(al, BGAN, lambda t: "bastard_gan_punks_v2"))

    out = Out()
    for slug, c in colls.items():
        pool = c.pop("pool")
        c["minted"] = dates.get(slug)
        c.update(evidence(al, c["address"], pool[0], contract_level=c["address"] == BGAN) if pool else {})
        picked = sorted(random.Random(f"ai-nft-{slug}").sample(pool, min(args.per, len(pool))))
        todo = [t for t in picked if f"ainft_{slug}_{t}.jpg" not in out.seen]
        got = 0
        with ThreadPoolExecutor(6) as ex:
            for t, res in zip(todo, ex.map(lambda t, a=c["address"]: download(http, al, a, t), todo)):
                if res is None:
                    log(f"[ai_nft] {slug} #{t}: no image")
                    continue
                data, ct, original_url, source = res
                if source != "alchemy_cdn":
                    c.setdefault("image_sources", {})[source] = c.get("image_sources", {}).get(source, 0) + 1
                try:
                    im = Image.open(io.BytesIO(data))
                    ext = (im.format or "png").lower().replace("jpeg", "jpg")
                except Exception:
                    log(f"[ai_nft] {slug} #{t}: not an image")
                    continue
                (RAW_DIR / f"{slug}_{t}.{ext}").write_bytes(data)
                if out.add(
                    f"ainft_{slug}_{t}",
                    data,
                    "ai",
                    f"ai_nft:{slug}",
                    "ai_nft_collection",
                    f"Ethereum {c['address']} token {t}",
                    "token owner's terms (evaluation only)",
                    generator=slug,
                ):
                    got += 1
        c["sampled"] = len([r for r in out.records if r["group"] == f"ai_nft:{slug}"])
        log(f"[ai_nft] {c['title']}: +{got} ({c['sampled']} in the OOS set), minted {c['minted']}")
        out.save()
    (OUT / "collections.json").write_text(
        json.dumps({"generated": datetime.now(UTC).strftime("%Y-%m-%d"), "collections": colls}, indent=2)
    )
    out.save()


if __name__ == "__main__":
    main()
