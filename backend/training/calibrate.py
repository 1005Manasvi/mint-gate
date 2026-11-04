"""Calibrate Mint-Gate thresholds from data and write them into policy.json (policy 4.x).

Copy checks (Phase 4 engine, detection/copy_engine.py) come from the copy-detection benchmark
(benchmark/copydet.py; run its `features`, `lightglue` and `evaluate` steps first). Per benchmark
query the three values are computed exactly as the engine computes them:
  copy_exact      min PDQ distance (8 dihedral query hashes) to any reference; PDQ quality < 50 -> 256
  copy_semantic   max gated retrieval score over references that are NOT generative-collection
                  members (v1 lesson: siblings are not copies; members only count on copy_exact)
  copy_geometric  max LightGlue inliers over the top-10 retrieved non-member references (only if the
                  benchmark gate adopted LightGlue; copy-engine-1.0.0 rejected it)
Thresholds: review / fail at the 1 % / 0.2 % false-positive rate of the genuine (distractor)
queries of the VAL split; every rate below is measured on the TEST split, per domain. The v1
checks (pHash / CLIP copy score / ORB, policy 3.1.0) are scored on the same test queries.

Copy-move + learned detectors (models unchanged in Phase 4): review / fail at the 95th / 99th
percentile of genuine validation art, as before; `--keep-detectors` reuses the stored values.

Run from backend/:   python -m training.calibrate [--n 300] [--keep-detectors]
"""

import argparse
import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pdqhash
from sklearn.metrics import roc_auc_score
from tqdm import tqdm

from config import POLICY_FILE, PROJECT_DIR
from detection.preprocessing import load_path
from training.realworld_data import open_image, records
from training.split import load_split, paths

POLICY_VERSION = "4.0.0"
COPY_FPR_REVIEW, COPY_FPR_FAIL = 0.01, 0.002
WEIGHTS = {
    "resnet": 0.15,
    "ai_detector": 0.40,
    "copy_move": 0.15,
    "copy_semantic": 0.20,  # + v1 ORB's 0.05: LightGlue did not pass the benchmark gate
    "copy_exact": 0.10,
}
LABELS = {
    "copy_exact": "PDQ distance to the nearest protected work (bits, mirrored / rotated too)",
    "copy_semantic": "SSCD copy score vs protected works + on-chain NFTs (density-relative)",
    "copy_geometric": "ALIKED + LightGlue inliers vs the nearest protected one-off work",
}


def pdq_quality(paths_):
    return np.array(
        [pdqhash.compute(np.asarray(load_path(p).convert("RGB")))[1] for p in tqdm(paths_, desc="PDQ quality")]
    )


def copy_values(profile="gpu"):
    """(values per check, Bench) for every benchmark query, as the engine computes them.
    profile "cpu": the region crops the CPU profile runs (`regions_cpu`, possibly none)."""
    from benchmark import copydet as cd

    b = cd.Bench()
    cfg = json.loads((cd.OUT_DIR / "copydet.json").read_text())["tuned"]
    eng = json.loads((Path(cd.MODEL_DIR) / "copy_engine.json").read_text())
    if profile == "cpu":
        eng_cpu = json.loads((Path(cd.MODEL_DIR) / "copy_engine.json").read_text()).get("regions_cpu")
        cfg = {**cfg, "gates": {**cfg["gates"], "regions": bool(eng_cpu)}, "regions": eng_cpu or cfg["regions"]}
    S = cd.retrieval(cfg, pin=False)
    member = b.collection
    S_allowed = np.where(member[None, :], -np.inf, S)
    pd8 = cd.popcount_distances(cd.load("pdq_queries"), cd.load("pdq_refs"))
    qpath = cd.CACHE / "pdq_quality_queries.npy"
    if not qpath.exists():
        np.save(qpath, pdq_quality([cd.QUERY_DIR / f"{q['id']}.jpg" for q in b.queries]))
    quality = np.load(qpath)
    pd8 = np.where(quality[:, None] >= eng["pdq_min_quality"], pd8, 256)
    # Collection members: PDQ-close siblings are common (CryptoPunks: 0-10 bits apart), so a member
    # only counts when it is also pixel-identical (detection.copy_engine._exact). The PDQ quality
    # gate applies to one-off works only.
    pd_raw = cd.popcount_distances(cd.load("pdq_queries"), cd.load("pdq_refs"))
    px = member_pixels(pd_raw, member, b, eng["pdq_max_bits"])
    max_px = member_pixel_budget(px, b)
    eng["member_max_diff_px"] = max_px
    eng.pop("member_exact_min_sscd", None)
    (Path(cd.MODEL_DIR) / "copy_engine.json").write_text(json.dumps(eng, indent=2))
    pd8 = np.where(member[None, :], np.where(px <= max_px, pd_raw, 256), pd8)
    exact = pd8.min(1)
    geo = np.zeros(len(b.queries))
    lg = cd.CACHE / "lightglue_fused.json"
    if eng["lightglue"] and lg.exists():
        idx, inl = cd.inlier_matrix(b, json.loads(lg.read_text()))
        geo = np.where(member[idx], 0, inl).max(1)
    v = {"copy_exact": exact, "copy_semantic": S_allowed.max(1)}
    if eng["lightglue"]:  # only if the benchmark gate adopted LightGlue (copy-engine-1.0.0: rejected)
        v["copy_geometric"] = geo
    nearest = {"copy_semantic": S_allowed.argmax(1), "copy_exact": pd8.argmin(1)}
    v1 = cd.v1_scores(b) if (cd.CACHE / "orb_v1.json").exists() else None
    return v, nearest, b, v1


def member_pixels(pd_raw, member, b, max_bits, per_query=5):
    """(queries, refs) differing-pixel counts for the <= 5 PDQ-closest members within max_bits
    (cached); every other pair is 4096 (never pixel-identical)."""
    from benchmark.build_copydet import source_path
    from detection import fingerprints as fp

    cache = cd_cache() / "member_pixels.npy"
    if cache.exists():
        return np.load(cache)
    out = np.full(pd_raw.shape, 4096, np.int16)
    refs = {}
    for i in tqdm(range(len(b.queries)), desc="member pixel check"):
        close = np.flatnonzero(member & (pd_raw[i] <= max_bits))
        if not len(close):
            continue
        q64 = fp.rgb_small(b.query_image(i))
        for j in close[np.argsort(pd_raw[i, close], kind="stable")][:per_query]:
            if j not in refs:
                refs[j] = fp.rgb_small(load_path(source_path(b.refs[j]["name"])))
            out[i, j] = fp.differing_pixels(q64, refs[j])
    np.save(cache, out)
    return out


def cd_cache():
    from benchmark import copydet as cd

    return cd.CACHE


def member_pixel_budget(px, b):
    """Half the differing-pixel count of the closest genuine VAL sibling. The 2x margin is deliberate:
    the live index is ~60x the benchmark DB, so closer siblings exist there (the SSCD-cosine rule
    calibrated without a margin failed 36 % of genuine new CryptoPunks against the live index)."""
    gen_val = (b.gt < 0) & (b.qsplit == "val")
    worst = int(px[gen_val].min()) if gen_val.any() else 4096
    budget = max(0, worst // 2)
    print(f"[calibrate] member exact copies: <= {budget} differing pixels (closest genuine val sibling: {worst})")
    return budget


def threshold(neg, higher_is_worse, fpr):
    q = 1 - fpr if higher_is_worse else fpr
    return float(np.quantile(neg, q))


def calibrate_copy():
    v, nearest, b, v1 = copy_values()
    val, test = b.qsplit == "val", b.qsplit == "test"
    genuine = b.gt < 0
    th, cal = {}, {}
    for name, vals in v.items():
        hiw = name != "copy_exact"
        neg = vals[val & genuine]
        review, fail = threshold(neg, hiw, COPY_FPR_REVIEW), threshold(neg, hiw, COPY_FPR_FAIL)
        if name == "copy_exact":
            review = min(review - 0.5, 31.5)  # never review above PDQ's industry match threshold (31 bits)
            fail = min(fail - 0.5, review - 4)
        else:
            review = max(review, 1e-3) if name == "copy_geometric" else review
            fail = max(fail, review + (1 if name == "copy_geometric" else 1e-3))
        th[name] = {"review": round(review, 4), "fail": round(fail, 4), "label": LABELS[name]}
        s = vals if hiw else -vals
        mask = val
        try:
            auc = round(float(roc_auc_score(~genuine[mask], s[mask])), 4)
        except ValueError:
            auc = None
        cal[name] = {"val_auc": auc}
    out = {"thresholds": th, "per_check": cal, "test": {}}

    def flagged(vals, name, level):
        t = th[name][level]
        return vals >= t if name != "copy_exact" else vals <= t

    for dom in ("painting", "nft", "all"):
        m = test & ((b.qdom == dom) if dom != "all" else True)
        pos, neg = m & ~genuine, m & genuine
        res = {"copies": int(pos.sum()), "genuine": int(neg.sum())}
        any_review = np.zeros(len(genuine), bool)
        any_fail = np.zeros(len(genuine), bool)
        for name, vals in v.items():
            r, f = flagged(vals, name, "review"), flagged(vals, name, "fail")
            any_review |= r
            any_fail |= f
            res[name] = {
                "copies_flagged": round(float(r[pos].mean()), 4),
                "copies_failed": round(float(f[pos].mean()), 4),
                "genuine_flagged": round(float(r[neg].mean()), 4),
                "genuine_failed": round(float(f[neg].mean()), 4),
            }
        near_ok = nearest["copy_semantic"] == b.gt
        res["engine"] = {
            "copies_flagged": round(float(any_review[pos].mean()), 4),
            "copies_flagged_right_reference": round(float((any_review & near_ok)[pos].mean()), 4),
            "copies_failed": round(float(any_fail[pos].mean()), 4),
            "genuine_flagged": round(float(any_review[neg].mean()), 4),
            "genuine_failed": round(float(any_fail[neg].mean()), 4),
        }
        if v1 is not None:  # policy 3.1.0: any of pHash / CLIP / ORB at risk >= 0.5 (review) / 1.0 (fail)
            top = v1.max(1)
            res["v1_policy_3.1.0"] = {
                "copies_flagged": round(float((top[pos] >= 0.5).mean()), 4),
                "copies_failed": round(float((top[pos] >= 1.0).mean()), 4),
                "genuine_flagged": round(float((top[neg] >= 0.5).mean()), 4),
                "genuine_failed": round(float((top[neg] >= 1.0).mean()), 4),
            }
        out["test"][dom] = res
    # CPU profile (no GPU, no / fewer region crops), same thresholds: what a CPU-only deployment gets.
    v_cpu, _, _, _ = copy_values("cpu")
    out["test_cpu_profile"] = {}
    for dom in ("painting", "nft"):
        m = test & (b.qdom == dom)
        pos, neg = m & ~genuine, m & genuine
        rv = np.zeros(len(genuine), bool)
        fv = np.zeros(len(genuine), bool)
        for name, vals in v_cpu.items():
            rv |= flagged(vals, name, "review")
            fv |= flagged(vals, name, "fail")
        out["test_cpu_profile"][dom] = {
            "copies_flagged": round(float(rv[pos].mean()), 4),
            "copies_failed": round(float(fv[pos].mean()), 4),
            "genuine_flagged": round(float(rv[neg].mean()), 4),
            "genuine_failed": round(float(fv[neg].mean()), 4),
        }
    return th, out, v, b


def calibrate_detectors(n, rng):
    """copy_move, resnet, ai_detector: genuine-art false-positive rates (unchanged from policy 3.x)."""
    from detection import copy_move
    from detection.ai_detector import AIDetector
    from detection.clip_similarity import ClipEncoder
    from detection.manipulations import copy_move as make_clone
    from detection.preprocessing import detector_view
    from detection.resnet_classifier import ResNetClassifier

    split = load_split()
    new_art = rng.sample(paths(split, "authentic", "test"), n)
    rw_auth = rng.sample(records("val", "authentic"), n)
    cm_imgs = [open_image(r) for r in rw_auth] + [load_path(p) for p in new_art[: n // 3]]
    cm_neg, cm_pos = [], []
    for img in tqdm(cm_imgs, desc="copy-move"):
        cm_neg.append(copy_move.analyse(img)["score"])
        cm_pos.append(copy_move.analyse(make_clone(img, rng))["score"])
    cm_neg, cm_pos = np.array(cm_neg), np.array(cm_pos)
    review, fail = np.percentile(cm_neg, 95), max(np.percentile(cm_neg, 99), np.percentile(cm_neg, 95) + 1e-3)
    th = {"copy_move": {"review": round(float(review), 2), "fail": round(float(fail), 2)}}
    cal = {
        "copy_move": {
            "auc": round(float(roc_auc_score([0] * len(cm_neg) + [1] * len(cm_pos), np.r_[cm_neg, cm_pos])), 4),
            "tpr_at_review": round(float((cm_pos >= review).mean()), 4),
            "fpr_at_review": round(float((cm_neg >= review).mean()), 4),
        }
    }
    enc, resnet, ai = ClipEncoder(), ResNetClassifier(), AIDetector()
    det = {"resnet": [], "ai_detector": []}
    for r in tqdm(records("val", "authentic"), desc="detector FPR"):
        view = detector_view(open_image(r))
        det["resnet"].append(resnet.predict(view))
        det["ai_detector"].append(ai.predict(view, enc.encode_one(view))["probability"])
    for name, vals in det.items():
        x = np.array(vals)
        review = float(np.clip(np.percentile(x, 95), 0.5, 0.95))
        fail = float(np.clip(np.percentile(x, 99), review + 0.02, 0.99))
        th[name] = {"review": round(review, 3), "fail": round(fail, 3)}
        cal[name] = {"genuine_fpr_at_review": round(float((x >= review).mean()), 4), "n": len(x)}
    return th, cal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--keep-detectors", action="store_true", help="reuse copy-move / ResNet / AI thresholds")
    args = ap.parse_args()
    policy = json.loads(POLICY_FILE.read_text())

    copy_th, copy_cal, values, b = calibrate_copy()
    print(json.dumps(copy_cal, indent=2))
    if args.keep_detectors:
        det_th = {
            k: {x: policy["checks"][k][x] for x in ("review", "fail")} for k in ("copy_move", "resnet", "ai_detector")
        }
        det_cal = {k: policy.get("calibration", {}).get("results", {}).get(k) for k in det_th}
    else:
        det_th, det_cal = calibrate_detectors(args.n, random.Random(7))

    checks = {k: policy["checks"][k] for k in ("resnet", "ai_detector", "copy_move")}
    for k, t in det_th.items():
        checks[k].update(t)
    checks["resnet"]["max_status"] = "REVIEW"
    for k, t in copy_th.items():
        checks[k] = {"label": t["label"], "review": t["review"], "fail": t["fail"]}
    policy = {
        "version": POLICY_VERSION,
        # Weights reflect the out-of-source checks: the CLIP-probe AI detector generalises to unseen
        # collections / generators, the ResNet-18 does not; copy weights mirror v1's pHash / CLIP / ORB.
        "weights": WEIGHTS,
        "checks": checks,
        "score": policy["score"],
        "calibration": {
            "copy": {"source": "benchmark/copydet.py (val thresholds, test rates)", **copy_cal},
            "detectors": det_cal,
        },
    }
    POLICY_FILE.write_text(json.dumps(policy, indent=2))
    print("updated", POLICY_FILE, "->", POLICY_VERSION)

    fig, axes = plt.subplots(1, len(values), figsize=(5 * len(values), 3.8))
    genuine = b.gt < 0
    for ax, (name, vals) in zip(axes, values.items()):
        m = b.qsplit == "test"
        allv = vals[m].astype(float)
        bins = np.histogram_bin_edges(allv[np.isfinite(allv)], bins=40)
        ax.hist(vals[m & genuine], bins=bins, alpha=0.6, label="genuine (distractors)", color="#2a9d8f")
        ax.hist(vals[m & ~genuine], bins=bins, alpha=0.6, label="copies", color="#e76f51")
        ax.axvline(copy_th[name]["review"], color="#e9c46a", ls="--", label="review")
        ax.axvline(copy_th[name]["fail"], color="#c1121f", ls="--", label="fail")
        ax.set_yscale("log")
        ax.set_title(f"{name} (test split)", fontsize=10)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    out = Path(PROJECT_DIR) / "assets" / "calibration.png"
    fig.savefig(out, dpi=130)
    print("figure:", out)


if __name__ == "__main__":
    main()
