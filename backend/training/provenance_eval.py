"""Provenance signals on real files (Phase 5 step 3): how often do they fire, and are they ever wrong?

The benchmark images on disk are re-encoded (metadata and watermarks gone), so this script streams
the ORIGINAL bytes into memory (no disk cache) and runs `detection.provenance.analyse` on them,
reverse search off:
  AI       OpenFakeTiny core test (every still-image generator, original files as published),
           LukasT9/Flux-1-Dev-Images-1k PNGs (the v1 sample)
  genuine  COCO val2017 JPEGs (camera EXIF), huggingnft collection PNGs, on-chain NFT originals
           (Alchemy CDN, the out-of-source collections)
Reports per group: AI-evidence rate (recall on AI / false-alarm rate on genuine), which signal fired,
and C2PA presence, with 95 % CIs.

`--nft` (Phase 5b) runs on the original token files of the NFT-native sets instead: AI collections
(data/ai_nft/raw/, training/ai_nft_data.py) and copymints (data/copymint/images/) -> provenance_nft.json.

Run from backend/:   python -m training.provenance_eval [--nft]
"""

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import requests
from huggingface_hub import HfFileSystem, list_repo_files

from config import MODEL_DIR
from detection import provenance as pv
from detection.preprocessing import InvalidImage, prepare
from training import stats
from training.oos_data import COCO, COCO_OK_LICENCES, FLUX_REPO, OPENFAKE, V1_COLLECTIONS, fetch_bytes

OUT = MODEL_DIR / "benchmarks" / "provenance.json"


def nft_sources():
    """(label, group, raw bytes) of the Phase 5b original token files (local, as fetched)."""
    from training.ai_nft_data import BRAINDROPS
    from training.ai_nft_data import RAW_DIR as AI_RAW
    from training.copymint_data import IMG_DIR as CM_DIR

    slugs = sorted([s for s, _, _ in BRAINDROPS.values()] + ["bastard_gan_punks_v2"], key=len, reverse=True)
    for f in sorted(AI_RAW.iterdir()):
        slug = next(s for s in slugs if f.stem.startswith(s + "_"))
        yield "ai", f"ai_nft:{slug}", f.read_bytes()
    for f in sorted(CM_DIR.iterdir()):
        yield "copy", f"copymint:{f.stem.split('_')[0]}", f.read_bytes()


def signal_of(r):
    d = r["details"]
    if d["watermarks"]:
        return "watermark"
    if d["c2pa"] and d["c2pa"].get("valid") and pv.c2pa_verdict(d["c2pa"]):
        return "c2pa"
    if d["metadata"]["generators"] or d["metadata"]["xmp_source_types"]:
        return "metadata"
    return None


def sources(fs):
    pf = pq.ParquetFile(fs.open(f"datasets/{OPENFAKE}/core/test-00000-of-00001.parquet", "rb", block_size=1 << 24))
    for n, batch in enumerate(pf.iter_batches(batch_size=16, columns=["image", "label", "model", "type"])):
        for r in batch.to_pylist():  # 16 rows at a time: this laptop has little memory to spare
            if r["type"] == "video":
                continue
            lab = "ai" if r["label"] == "fake" else "authentic"
            yield lab, f"openfake:{r['model']}", r["image"]["bytes"]
        if n % 10 == 0:
            print("openfake batch", n, flush=True)
    rng = random.Random(5)
    flux = sorted(f for f in list_repo_files(FLUX_REPO, repo_type="dataset") if f.endswith(".png"))
    with ThreadPoolExecutor(8) as ex:
        for data in ex.map(lambda f: fetch_bytes(fs, f"datasets/{FLUX_REPO}/{f}"), rng.sample(flux, 200)):
            if data:
                yield "ai", "flux1-dev", data
    from huggingface_hub import hf_hub_download

    from training.fetch_realworld import download, parquet_rows

    for name in V1_COLLECTIONS:
        repo = f"huggingnft/{name}"
        f = sorted(x for x in list_repo_files(repo, repo_type="dataset") if x.endswith(".parquet"))[0]
        rows = list(parquet_rows(download(repo, f), ["image"]))
        for row in random.Random(name).sample(rows, min(100, len(rows))):
            yield "authentic", f"nft_hf:{name}", row["image"]["bytes"]
    p = hf_hub_download(COCO, "data/validation-00000-of-00001-e3c37e369512a3aa.parquet", repo_type="dataset")
    rows = [
        r for r in pq.read_table(p, columns=["license", "coco_url"]).to_pylist() if r["license"] in COCO_OK_LICENCES
    ]
    http = requests.Session()
    for r in random.Random(13).sample(rows, 300):
        try:
            resp = http.get(r["coco_url"], timeout=60)
            if resp.status_code == 200:
                yield "authentic", "photo:coco", resp.content
        except requests.RequestException:
            pass
    yield from onchain(http)


def onchain(http, per=6):
    from sqlalchemy import text

    import db
    from ingest.alchemy import Alchemy
    from training.oos_data import ONCHAIN_EXCLUDE

    with db.session() as s:
        rows = s.execute(
            text(
                "SELECT c.name, r.contract, r.token_id FROM reference_items r JOIN nft_collections c ON c.id = r.collection_id "
                "WHERE r.source = 'alchemy' AND r.status = 'ready' AND r.chain_id = 1"
            )
        ).all()
    by = defaultdict(list)
    for name, contract, tid in rows:
        if name not in ONCHAIN_EXCLUDE:
            by[(name, contract)].append(str(int(tid)))
    al = Alchemy("eth-mainnet")
    for (name, contract), tids in sorted(by.items()):
        for tid in random.Random(f"prov-{contract}").sample(tids, min(per, len(tids))):
            try:
                img = al.nft_metadata(contract, tid).get("image") or {}
                url = img.get("originalUrl") if (img.get("originalUrl") or "").startswith("http") else None
                url = url or img.get("cachedUrl")
                if not url:
                    continue
                r = http.get(url, timeout=60)
                if r.status_code == 200 and len(r.content) < 25_000_000:
                    yield "authentic", f"onchain:{name}", r.content
            except Exception:
                continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nft", action="store_true", help="Phase 5b NFT-native original files")
    args = ap.parse_args()
    out_file = OUT.with_name("provenance_nft.json") if args.nft else OUT
    rows = []
    t0 = time.time()
    for label, group, raw in nft_sources() if args.nft else sources(HfFileSystem()):
        try:
            if args.nft:  # metadata / C2PA from the original bytes; pixels from the file an artist could upload
                from training.ai_nft_data import as_upload

                img = prepare(as_upload(raw))
            else:
                img = prepare(raw)
        except InvalidImage:
            continue
        r = pv.analyse(raw, img.rgb)
        c = r["details"]["c2pa"]
        rows.append(
            {
                "label": label,
                "group": group,
                "value": r["value"],
                "signal": signal_of(r),
                "c2pa": bool(c),
                "c2pa_valid": bool(c and c.get("valid")),
                "reason": r["reason"],
                "generator_hint": (c or {}).get("claim_generator", ""),
                "ms": r["ms"],
            }
        )
        if len(rows) % 200 == 0:
            print(len(rows), f"{time.time() - t0:.0f}s", flush=True)
    out = {"n": len(rows), "groups": {}, "pooled": {}}
    for label in ("ai", "authentic", "copy"):
        sub = [x for x in rows if x["label"] == label]
        if not sub:
            continue
        out["pooled"][label] = {
            "ai_evidence": stats.rate([x["value"] >= pv.AI_GENERATED for x in sub]),
            "any_evidence": stats.rate([x["value"] != pv.NOTHING for x in sub]),
            "c2pa_present": stats.rate([x["c2pa"] for x in sub]),
            "signals": dict(Counter(x["signal"] for x in sub if x["signal"])),
        }
    for g in sorted({x["group"] for x in rows}):
        sub = [x for x in rows if x["group"] == g]
        out["groups"][g] = {
            "label": sub[0]["label"],
            "ai_evidence": stats.rate([x["value"] >= 1 for x in sub]),
            "c2pa_present": stats.rate([x["c2pa"] for x in sub]),
            "signals": dict(Counter(x["signal"] for x in sub if x["signal"])),
            "examples": sorted({x["reason"] for x in sub if x["value"] != 0})[:3],
        }
    ms = sorted(x["ms"] for x in rows)
    out["ms_p50"], out["ms_p95"] = ms[len(ms) // 2], ms[int(len(ms) * 0.95)]
    out_file.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["pooled"], indent=1))
    for g, e in out["groups"].items():
        print(f"{g:40s} {e['label']:9s} AI evidence {stats.fmt_rate(e['ai_evidence'])}  {e['signals']}")


if __name__ == "__main__":
    main()
