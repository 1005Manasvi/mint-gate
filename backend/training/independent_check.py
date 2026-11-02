"""Out-of-source generalisation check.

The in-distribution test split shares its *sources* with the training data
(same NFT collections, same Rapidata compression pipeline as the held-out Flux
images). That can inflate accuracy. This script evaluates on data from sources
that contributed NOTHING to training:

  authentic  4 NFT collections never used in training (CyberKongz, CrypToadz,
             Shiboshis, EtherBears - huggingnft, MIT)
  ai         Flux.1-dev images from a different publisher (LukasT9/Flux-1-Dev-Images-1k,
             Apache-2.0), i.e. not from Rapidata

Images get exactly the training normalisation, then both detectors are scored.
Results -> model/independent_check.json

Run from backend/:   python -m training.independent_check --per-collection 120 --flux 200
"""

import argparse
import io
import json
import random
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from huggingface_hub import HfFileSystem, list_repo_files
from PIL import Image
from sklearn.metrics import roc_auc_score

from config import MODEL_DIR
from detection.ai_detector import AIDetector
from detection.clip_similarity import ClipEncoder
from detection.resnet_classifier import ResNetClassifier
from training.fetch_realworld import download, normalise, parquet_rows

COLLECTIONS = ["cyberkongz", "cryptoadz-by-gremplin", "theshiboshis", "etherbears"]
FLUX_REPO = "LukasT9/Flux-1-Dev-Images-1k"


def to_view(data: bytes) -> Image.Image:
    """Training normalisation (shorter side 256 + JPEG), decoded back to an image."""
    return Image.open(io.BytesIO(normalise(Image.open(io.BytesIO(data))))).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-collection", type=int, default=120)
    ap.add_argument("--flux", type=int, default=200)
    args = ap.parse_args()
    rng = random.Random(5)
    fs = HfFileSystem()

    samples = []  # (group, label, image)
    for name in COLLECTIONS:
        repo = f"huggingnft/{name}"
        pq_file = sorted(f for f in list_repo_files(repo, repo_type="dataset") if f.endswith(".parquet"))[0]
        rows = list(parquet_rows(download(repo, pq_file), ["image"]))
        for row in rng.sample(rows, min(args.per_collection, len(rows))):
            samples.append((f"nft_{name}", 0, to_view(row["image"]["bytes"])))
        print(f"{name}: {min(args.per_collection, len(rows))} images")

    flux_files = sorted(f for f in list_repo_files(FLUX_REPO, repo_type="dataset") if f.endswith(".png"))
    chosen = rng.sample(flux_files, min(args.flux, len(flux_files)))

    def fetch(f):
        for _ in range(8):
            try:
                with fs.open(f"datasets/{FLUX_REPO}/{f}", "rb") as fh:
                    return fh.read()
            except Exception:
                continue
        return None

    with ThreadPoolExecutor(12) as ex:
        for data in ex.map(fetch, chosen):
            if data:
                samples.append(("flux_independent", 1, to_view(data)))
    print(f"flux (independent source): {sum(1 for s in samples if s[1] == 1)} images")

    resnet, ai, enc = ResNetClassifier(), AIDetector(), ClipEncoder()
    y = np.array([s[1] for s in samples])
    p_res, p_ai = [], []
    for _, _, img in samples:
        p_res.append(resnet.predict(img))
        p_ai.append(ai.predict(img, enc.encode_one(img))["probability"])
    p_res, p_ai = np.array(p_res), np.array(p_ai)

    out = {}
    for name, p in (("resnet18", p_res), ("ai_detector", p_ai)):
        pred = (p >= 0.5).astype(int)
        groups = {}
        for g in sorted({s[0] for s in samples}):
            m = np.array([s[0] == g for s in samples])
            groups[g] = round(float((pred[m] == y[m]).mean()), 4)
        out[name] = {
            "accuracy": round(float((pred == y).mean()), 4),
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "genuine_nft_correct": round(float((pred[y == 0] == 0).mean()), 4),
            "flux_detected": round(float((pred[y == 1] == 1).mean()), 4),
            "by_group": groups,
        }
    out["n_authentic"], out["n_ai"] = int((y == 0).sum()), int((y == 1).sum())
    print(json.dumps(out, indent=2))
    (MODEL_DIR / "independent_check.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
