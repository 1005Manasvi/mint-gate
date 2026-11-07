"""Permissively licensed GENUINE paintings + photos for the --permissive-only AI detector.

The permissive training sources (huggingnft MIT, Rapidata CDLA-P, SDXL MIT) contain no genuine
painting or photo, so a permissive-only probe flagged 100 % of them. This adds:
  artbench_pd     public-domain paintings (alfredplpl/artbench-pd-256x256, MIT card; the artworks are
                  in the public domain), one row group read over HTTP
  coco_train_cc   COCO train2017 photos licensed CC BY 2.0 / CC BY-SA 2.0 / no known copyright / US Gov
                  (the out-of-source set uses COCO *val*: disjoint images, same source; DOCCI stays
                  fully out-of-source for this variant)
Stored separately (data/permissive_extra/) so the main detector's training set is unchanged.
85 % train / 15 % val. Every image gets the training normalisation.

Run from backend/:   python -m training.fetch_permissive [--paintings 1000 --photos 800]
"""

import argparse
import io
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import requests
from huggingface_hub import HfFileSystem, hf_hub_download
from PIL import Image

from config import BACKEND_DIR
from training.fetch_realworld import normalise
from training.oos_data import COCO, COCO_OK_LICENCES

OUT = BACKEND_DIR / "data" / "permissive_extra"
IMG_DIR = OUT / "images"
MANIFEST = OUT / "manifest.json"
ARTBENCH = "alfredplpl/artbench-pd-256x256"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paintings", type=int, default=1000)
    ap.add_argument("--photos", type=int, default=800)
    args = ap.parse_args()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    recs, rng = [], random.Random(21)

    def add(key, data, source, licence):
        try:
            (IMG_DIR / f"{key}.jpg").write_bytes(normalise(Image.open(io.BytesIO(data))))
        except Exception:
            return
        recs.append({"file": f"{key}.jpg", "label": "authentic", "source": source, "licence": licence,
                     "generator": "human_art" if source == "artbench_pd" else "real_photo",
                     "split": "val" if rng.random() < 0.15 else "train"})

    fs = HfFileSystem()
    pf = pq.ParquetFile(fs.open(f"datasets/{ARTBENCH}/data/train-00000-of-00003-4c3fdb0c6fcc316c.parquet", "rb",
                                block_size=1 << 24))
    got = 0
    for batch in pf.iter_batches(batch_size=64, columns=["image"]):
        for i, r in enumerate(batch.to_pylist()):
            if got >= args.paintings:
                break
            add(f"artbench_{got}", r["image"]["bytes"], "artbench_pd", "public domain (MIT card)")
            got += 1
        if got >= args.paintings:
            break
    print("paintings", got, flush=True)

    p = hf_hub_download(COCO, "data/train-00000-of-00001-0084e041f1902997.parquet", repo_type="dataset")
    rows = [r for r in pq.read_table(p, columns=["license", "coco_url", "image_id"]).to_pylist()
            if r["license"] in COCO_OK_LICENCES]
    http = requests.Session()

    def get(r):
        for attempt in range(4):
            try:
                resp = http.get(r["coco_url"], timeout=60)
                if resp.status_code == 200:
                    return r, resp.content
            except requests.RequestException:
                time.sleep(1 + attempt)
        return r, None

    with ThreadPoolExecutor(8) as ex:
        for r, data in ex.map(get, random.Random(22).sample(rows, args.photos)):
            if data:
                add(f"cocotrain_{r['image_id']}", data, "coco_train_cc", COCO_OK_LICENCES[r["license"]])
    MANIFEST.write_text(json.dumps(recs))
    from collections import Counter

    print(Counter((r["source"], r["split"]) for r in recs))


if __name__ == "__main__":
    main()
