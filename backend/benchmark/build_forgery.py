"""Real image-forgery benchmark (Phase 5 step 2): manipulated photos with ground-truth masks.

Source: KhangTruong/Tampered-Images-Combined-Dataset-2 (HF, card licence CC BY-SA 4.0; every
component dataset keeps its own research-use terms), `cross_test` split. Only the shards that hold
the datasets used here are downloaded (`SHARDS`, ~1 GB) and deleted from the HF cache afterwards.

| dataset | manipulations | real images here |
|---|---|---|
| CASIA v2.0 | splicing + copy-move, often post-processed | no (fakes only: pixel F1 + detection rate) |
| Columbia uncompressed | splicing (TIFF, uncompressed) | no |
| COVERAGE | copy-move with similar-but-genuine objects | yes (the originals) |
| IMD2020 (real-life subset + generated) | real-life manipulations from the web | yes |
| CocoGlide | GLIDE diffusion inpainting of COCO images | yes |

Per dataset and label at most `--per` images (seeded). Images are stored losslessly (PNG) at their
original resolution with their binary mask (255 = manipulated).

Output: data/benchmark/forgery/<dataset>/<label>_<i>.png (+ _mask.png) + manifest.json
Run from backend/:   python -m benchmark.build_forgery [--per 300]
"""

import argparse
import io
import json
import random

import numpy as np
from PIL import Image

from config import BACKEND_DIR
from training.fetch_realworld import download

REPO = "KhangTruong/Tampered-Images-Combined-Dataset-2"
SHARDS = [0, 8, 48, 49, 51, 52, 53, 54, 55]
KEEP = {
    "CASIA_V2": "casia_v2",
    "Columbia_Gray": "columbia",
    "Coverage": "coverage",
    "IMD2020": "imd2020",
    "IMD2020_RealLife": "imd2020_reallife",
    "CocoGlide": "cocoglide",
}
OUT = BACKEND_DIR / "data" / "benchmark" / "forgery"
Image.MAX_IMAGE_PIXELS = 120_000_000


def main():
    import pyarrow.parquet as pq

    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=300)
    args = ap.parse_args()
    rows = {}
    for s in SHARDS:
        path = download(REPO, f"data/cross_test-{s:05d}.parquet")
        t = pq.read_table(path, columns=["id", "image", "mask", "label_text", "dataset", "method"]).to_pylist()
        for r in t:
            if r["dataset"] in KEEP:
                rows.setdefault((KEEP[r["dataset"]], r["label_text"]), []).append(r)
        print(f"shard {s}: {len(t)} rows", flush=True)
    manifest = []
    for (ds, label), rs in sorted(rows.items()):
        rs.sort(key=lambda r: str(r["id"]))
        picked = random.Random(f"{ds}-{label}").sample(rs, min(args.per, len(rs)))
        d = OUT / ds
        d.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(picked):
            try:
                img = Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")
            except Exception as exc:
                print("skip", ds, r["id"], type(exc).__name__)
                continue
            name = f"{label}_{i:04d}"
            img.save(d / f"{name}.png")
            rec = {
                "file": f"{ds}/{name}.png",
                "dataset": ds,
                "label": label,
                "method": r.get("method"),
                "id": str(r["id"]),
                "width": img.width,
                "height": img.height,
            }
            if label == "fake" and r.get("mask") and r["mask"].get("bytes"):
                m = np.asarray(Image.open(io.BytesIO(r["mask"]["bytes"])).convert("L"))
                if m.shape != (img.height, img.width):
                    m = np.asarray(Image.fromarray(m).resize(img.size, Image.NEAREST))
                Image.fromarray(((m > 127) * 255).astype(np.uint8)).save(d / f"{name}_mask.png")
                rec["mask"] = f"{ds}/{name}_mask.png"
                rec["mask_ratio"] = round(float((m > 127).mean()), 4)
            manifest.append(rec)
        print(f"{ds} {label}: {len(picked)}", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=0))
    from collections import Counter

    print(Counter((r["dataset"], r["label"]) for r in manifest))


if __name__ == "__main__":
    main()
