"""Calibrate Mint-Gate thresholds from data and write them into policy.json.

Negatives (must NOT be flagged):
  * authentic test-split artworks (genuine, never minted, not in the index)
  * AI test-split artworks (for the copy-move check only)
Positives (must be flagged):
  * near-duplicates of minted references (crop/resize/recolour/JPEG/rotate/text)
  * copy-move forgeries of test artworks

Threshold rules (all numbers measured here are written to policy.json):
  copy-move : review / fail at the 95th / 99th percentile of genuine art
              (<= 5 % / 1 % false-positive rate).
  CLIP      : density-relative copy score (ReferenceIndex.copy_score);
              review / fail at the 95th / 99th percentile of genuine new art
              (NFTs + paintings). Pixel-identical copies always score 1.0.
  pHash/ORB : measured only against "sparse" references (nearest-neighbour
              similarity < 0.95). In dense generative collections genuine
              siblings share hashes and keypoints, so matches there are not
              copy evidence. Thresholds at the 5th/1st (pHash) or 95th/99th
              (ORB) percentile of genuine new art.

Run from backend/:   python -m training.calibrate --n 300
"""

import argparse
import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import roc_auc_score
from tqdm import tqdm

from config import POLICY_FILE, PROJECT_DIR
from detection import copy_move, feature_match
from detection.ai_detector import AIDetector
from detection.clip_similarity import ClipEncoder
from detection.hashing import phash_int
from detection.manipulations import copy_move as make_clone
from detection.manipulations import near_duplicate
from detection.preprocessing import detector_view, load_path
from detection.reference_index import ReferenceIndex
from detection.resnet_classifier import ResNetClassifier
from training.realworld_data import open_image, records
from training.split import load_split, paths


def plagiarism_stats(img, idx, enc):
    """Same measurements as the pipeline: pHash / ORB only against sparse references."""
    sims, dists, cand = idx.search(phash_int(img), enc.encode_one(img))
    q = feature_match.describe(img)
    sparse = idx.sparse
    orb = max(
        [feature_match.match(q, feature_match.describe(load_path(idx.path(i))))[0] for i in cand if sparse[i]] or [0]
    )
    return {"phash": idx.sparse_min_distance(dists)[0], "clip": idx.copy_score(sims)[0], "orb": int(orb)}


def thresholds(neg, pos, higher_is_worse):
    neg, pos = np.asarray(neg, float), np.asarray(pos, float)
    if higher_is_worse:
        review, fail = np.percentile(neg, 95), np.percentile(neg, 99)
        fail = max(fail, review + 1e-3)
        tpr = lambda t: float((pos >= t).mean())
        fpr = lambda t: float((neg >= t).mean())
    else:
        review, fail = np.percentile(neg, 5), np.percentile(neg, 1)
        fail = min(fail, review - 1)
        tpr = lambda t: float((pos <= t).mean())
        fpr = lambda t: float((neg <= t).mean())
    return {
        "review": float(review),
        "fail": float(fail),
        "tpr_at_review": round(tpr(review), 4),
        "fpr_at_review": round(fpr(review), 4),
        "tpr_at_fail": round(tpr(fail), 4),
        "fpr_at_fail": round(fpr(fail), 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    args = ap.parse_args()
    rng = random.Random(7)
    split = load_split()
    idx, enc = ReferenceIndex(), ClipEncoder()

    # Genuine new uploads: half pixel-art NFTs, half paintings - none of them in the index.
    new_art = rng.sample(paths(split, "authentic", "test"), args.n)
    new_paintings = rng.sample([r for r in records("test") if r["generator"] == "human_art"], args.n // 2)
    genuine = [load_path(p) for p in new_art[: args.n // 2]] + [open_image(r) for r in new_paintings]
    # Plagiarised copies of protected works: half NFTs, half paintings.
    is_art = np.array([f.startswith("art/") and not f.startswith("art/nft_") for f in idx.files])
    minted = rng.sample(list(np.flatnonzero(~is_art)), args.n // 2) + rng.sample(
        list(np.flatnonzero(is_art)), args.n // 2
    )

    neg, pos = {"phash": [], "clip": [], "orb": []}, {"phash": [], "clip": [], "orb": []}
    for img in tqdm(genuine, desc="genuine new art"):
        for k, v in plagiarism_stats(img, idx, enc).items():
            neg[k].append(v)
    for i in tqdm(minted, desc="near-duplicates"):
        for k, v in plagiarism_stats(near_duplicate(load_path(idx.path(int(i))), rng), idx, enc).items():
            pos[k].append(v)
    half = args.n // 2  # first half NFTs, second half paintings (for per-domain reporting)

    # Copy-move on REAL-WORLD genuine images (paintings, drawings, photos) + pixel art.
    rw_auth = rng.sample(records("val", "authentic"), args.n)
    cm_imgs = [open_image(r) for r in rw_auth] + [load_path(p) for p in new_art[: args.n // 3]]
    cm_neg, cm_pos = [], []
    for img in tqdm(cm_imgs, desc="copy-move"):
        cm_neg.append(copy_move.analyse(img)["score"])
        cm_pos.append(copy_move.analyse(make_clone(img, rng))["score"])

    # Learned detectors: thresholds from false-positive rates on genuine validation art.
    resnet, ai = ResNetClassifier(), AIDetector()
    det_neg = {"resnet": [], "ai_detector": []}
    for r in tqdm(records("val", "authentic"), desc="detector FPR"):
        view = detector_view(open_image(r))
        det_neg["resnet"].append(resnet.predict(view))
        det_neg["ai_detector"].append(ai.predict(view, enc.encode_one(view))["probability"])
    det_th = {}
    for name, vals in det_neg.items():
        v = np.array(vals)
        review = float(np.clip(np.percentile(v, 95), 0.5, 0.95))
        fail = float(np.clip(np.percentile(v, 99), review + 0.02, 0.99))
        det_th[name] = {
            "review": round(review, 3),
            "fail": round(fail, 3),
            "genuine_fpr_at_review": round(float((v >= review).mean()), 4),
            "genuine_fpr_at_fail": round(float((v >= fail).mean()), 4),
            "n": len(v),
        }

    def auc(n, p, higher_is_worse):
        s = np.concatenate([n, p]) * (1 if higher_is_worse else -1)
        return round(float(roc_auc_score([0] * len(n) + [1] * len(p), s)), 4)

    def describe(n, p, higher_is_worse):
        return {
            "auc": auc(n, p, higher_is_worse),
            "genuine_p5_p50_p95": np.percentile(n, [5, 50, 95]).round(4).tolist(),
            "copies_p5_p50_p95": np.percentile(p, [5, 50, 95]).round(4).tolist(),
        }

    cm = thresholds(cm_neg, cm_pos, higher_is_worse=True)
    # pHash / ORB / copy score only apply to one-off artworks (collection members are excluded
    # from these checks), so their thresholds are calibrated on the painting half only.
    art_neg = {k: v[half:] for k, v in neg.items()}
    art_pos = {k: v[half:] for k, v in pos.items()}
    cs = thresholds(art_neg["clip"], art_pos["clip"], higher_is_worse=True)  # density-relative copy score
    ph = thresholds(art_neg["phash"], art_pos["phash"], higher_is_worse=False)
    ob = thresholds(art_neg["orb"], art_pos["orb"], higher_is_worse=True)
    neg_clip, pos_clip = np.array(neg["clip"]), np.array(pos["clip"])
    clip_review, clip_fail = round(cs["review"], 4), round(max(cs["fail"], cs["review"] + 0.01), 4)
    per_domain = {}
    for dom, sl in (("nft", slice(0, half)), ("painting", slice(half, None))):
        n_, p_ = neg_clip[sl], pos_clip[sl]
        per_domain[dom] = {
            "auc": auc(n_, p_, True),
            "genuine_flagged_at_review": round(float((n_ >= clip_review).mean()), 4),
            "copies_caught_at_review": round(float((p_ >= clip_review).mean()), 4),
            "copies_failed": round(float((p_ >= clip_fail).mean()), 4),
        }
    th = {
        "copy_move": {"review": round(cm["review"], 2), "fail": round(cm["fail"], 2)},
        "clip": {
            "review": clip_review,
            "fail": clip_fail,
            "label": "CLIP copy score vs protected collection (density-relative)",
        },
        "phash": {
            "review": int(ph["review"]),
            "fail": int(ph["fail"]),
            "advisory": False,
            "label": "pHash distance to nearest protected work (sparse references)",
        },
        "orb": {
            "review": int(np.ceil(ob["review"])),
            "fail": int(np.ceil(ob["fail"])) + 1,
            "advisory": False,
            "label": "ORB geometric inliers vs nearest protected work (sparse references)",
        },
        **{k: {"review": v["review"], "fail": v["fail"]} for k, v in det_th.items()},
    }
    cal = {
        "copy_move": {**describe(cm_neg, cm_pos, True), **cm},
        "clip": {**describe(neg["clip"], pos["clip"], True), **cs, "per_domain": per_domain},
        "phash": {**describe(neg["phash"], pos["phash"], False), **ph},
        "orb": {**describe(neg["orb"], pos["orb"], True), **ob},
        **det_th,
    }
    print(json.dumps({"thresholds": th, "measurements": cal}, indent=2))

    policy = json.loads(POLICY_FILE.read_text())
    for name, t in th.items():
        policy["checks"][name].update(t)
    # Weights reflect the out-of-source check (training/independent_check.py): the CLIP-probe
    # AI detector generalises to unseen collections/generators, the ResNet-18 does not.
    policy["weights"] = {
        "resnet": 0.15,
        "ai_detector": 0.40,
        "copy_move": 0.15,
        "clip": 0.15,
        "phash": 0.10,
        "orb": 0.05,
    }
    policy["checks"]["resnet"]["max_status"] = "REVIEW"
    policy["version"] = "3.1.0"
    policy["calibration"] = {"n": args.n, "results": cal}
    POLICY_FILE.write_text(json.dumps(policy, indent=2))
    print("updated", POLICY_FILE)

    # Figure for the report
    fig, axes = plt.subplots(1, 4, figsize=(18, 3.8))
    series = [
        ("phash", neg["phash"], pos["phash"], "pHash distance (lower = copy)"),
        ("clip", neg["clip"], pos["clip"], "CLIP copy score (density-relative)"),
        ("orb", neg["orb"], pos["orb"], "ORB RANSAC inliers"),
        ("copy_move", cm_neg, cm_pos, "Copy-move peak ratio"),
    ]
    for ax, (name, n, p, title) in zip(axes, series):
        allv = np.concatenate([n, p]).astype(float)
        if name == "copy_move":  # heavy right tail -> log axis
            bins = np.logspace(0, np.log10(allv.max() + 1), 40)
            ax.set_xscale("log")
        else:
            bins = np.histogram_bin_edges(allv, bins=40)
        ax.hist(n, bins=bins, alpha=0.6, label="genuine", color="#2a9d8f")
        ax.hist(p, bins=bins, alpha=0.6, label="forged / copy", color="#e76f51")
        ax.axvline(th[name]["review"], color="#e9c46a", ls="--", label="review")
        ax.axvline(th[name]["fail"], color="#c1121f", ls="--", label="fail")
        ax.set_title(f"{title}\nAUC {cal[name]['auc']:.2f}", fontsize=10)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    out = Path(PROJECT_DIR) / "assets" / "calibration.png"
    fig.savefig(out, dpi=130)
    print("figure:", out)


if __name__ == "__main__":
    main()
