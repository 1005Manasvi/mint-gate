"""AI-image detector benchmark (Phase 5): frozen backbones + linear probes, gated on out-of-source data.

Candidates (UnivFD recipe, Ojha et al. CVPR 2023: frozen features + logistic-regression probe):
CLIP ViT-B/32 (= the v1 detector, the baseline), CLIP ViT-L/14, SigLIP 2 B/16 and So400m/16 (256 px),
DINOv2 ViT-S/14 and B/14, every pairwise probe ensemble (probability average, weight on val), and the
LoRA fine-tune written by `training/lora_finetune.py` (loaded from data/ai_bench/probs_*.npz).

Protocol
  train      realworld `train` split (same images for every candidate); C and ensemble weights: `val`
  thresholds review / fail = 95th / 99th percentile of genuine `val` probabilities, clipped exactly as
             `training/calibrate.py` does (so the operating point is the one the policy would deploy)
  in-source  `test` (AUC), `test_unseen` (Flux, never trained on)
  out-of-source  `training/oos_data.py` (v2): generators and genuine sources absent from training.
             Ranked on its "select" half; the shipped model's numbers come from the "report" half.
  primary metric  OOS balanced accuracy at the review threshold = (AI flagged + (1 - macro genuine
             false-alarm rate over the genuine domains)) / 2; secondary: OOS ROC-AUC
  gate       a candidate beats the baseline only if the paired-bootstrap 95 % CI of its primary-metric
             gain over CLIP-B/32 lies above 0 and its OOS AUC is not significantly worse
             (CI of the AUC difference does not lie below 0). Among the winners the best primary
             metric wins; if the best is not significantly better than a cheaper winner, the cheaper
             (CPU-feasible) one ships (hardware budget + "fewer / smaller models win ties").

Steps (run from backend/):
  python -m training.ai_backbones fetch                 # vision towers -> model/backbones/
  python -m training.ai_backbones features [--keys ...] # data/ai_bench/<key>.npz (GPU, sequential)
  python -m training.ai_backbones evaluate              # model/benchmarks/ai_detector.json + table
"""

import argparse
import itertools
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from config import BACKEND_DIR, MODEL_DIR
from detection.backbones import REGISTRY
from training import stats
from training.oos_data import IMG_DIR as OOS_IMG_DIR
from training.oos_data import MANIFEST as OOS_MANIFEST
from training.realworld_data import load_manifest, open_image

BENCH_DIR = BACKEND_DIR / "data" / "ai_bench"
OUT = MODEL_DIR / "benchmarks" / "ai_detector.json"
BASELINE = "clip-b32"
GENUINE_DOMAINS = ("nft_collection", "pixel_art", "painting", "photo")
CS = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
# CPU cost per upload (ms, 2 threads, measured with `benchmark.latency`-style timing in Phase 5;
# filled from model/benchmarks/ai_backbone_latency.json when present)
LATENCY_FILE = MODEL_DIR / "benchmarks" / "ai_backbone_latency.json"


def oos_manifest():
    return json.loads(OOS_MANIFEST.read_text())


def _load_oos(r):
    return Image.open(OOS_IMG_DIR / r["file"]).convert("RGB")


def features(keys, batch=32):
    import torch

    from detection.backbones import Backbone

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    rw, oos = load_manifest(), oos_manifest()
    sets = [("rw", rw, open_image), ("oos", oos, _load_oos)]
    if PERM_MANIFEST.exists():  # permissive genuine extras (training/fetch_permissive.py)
        sets.append(("perm", json.loads(PERM_MANIFEST.read_text()), _load_perm))
    for key in keys:
        bb, t0 = None, time.time()
        for name, recs, loader in sets:
            out = BENCH_DIR / f"{key}_{name}.npz"
            files = [r["file"] for r in recs]
            if not files:
                continue
            feats, start = [], 0
            if out.exists():
                old = np.load(out)
                old_files = list(old["files"])
                if old_files == files:
                    print(f"{key} {name}: cached")
                    continue
                if old_files == files[: len(old_files)]:  # rows appended (e.g. a new OOS group): encode the tail
                    feats, start = [old["x"]], len(old_files)
                    print(f"{key} {name}: {start} cached, {len(files) - start} new")
            bb = bb or Backbone(key)
            with ThreadPoolExecutor(4) as ex:
                for i in range(start, len(recs), batch):
                    imgs = list(ex.map(loader, recs[i : i + batch]))
                    feats.append(bb.encode(imgs).astype(np.float16))
                    if (i // batch) % 50 == 0:
                        print(f"  {key} {name} {i}/{len(recs)} ({time.time() - t0:.0f}s)", flush=True)
            np.savez(out, x=np.concatenate(feats), files=files)
            print(f"{key} {name}: {len(files)} images, {time.time() - t0:.0f}s", flush=True)
        del bb
        torch.cuda.empty_cache()


# ---- probes ------------------------------------------------------------------------------------


def fit_probe(Xtr, ytr, Xva, yva):
    """Same recipe as the v1 probe (training/train_ai_detector.fit): standardise, balanced LR, C on val AUC."""
    scaler = StandardScaler().fit(Xtr)
    best = None
    for C in CS:
        clf = LogisticRegression(C=C, max_iter=5000, class_weight="balanced").fit(scaler.transform(Xtr), ytr)
        a = stats.auc(yva, clf.decision_function(scaler.transform(Xva)))
        if best is None or a > best[0]:
            best = (a, C, clf)
    a, C, clf = best
    params = {
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "C": C,
    }
    return params, a


def predict(params, X):
    z = (np.asarray(X, np.float64) - np.array(params["mean"])) / np.array(params["scale"])
    return 1 / (1 + np.exp(-(z @ np.array(params["coef"]) + params["intercept"])))


def thresholds(p_val_genuine):
    """calibrate.calibrate_detectors: review = p95 clipped to [0.5, 0.95], fail = p99 in [review+0.02, 0.99]."""
    review = float(np.clip(np.percentile(p_val_genuine, 95), 0.5, 0.95))
    fail = float(np.clip(np.percentile(p_val_genuine, 99), review + 0.02, 0.99))
    return round(review, 3), round(fail, 3)


# ---- metrics -----------------------------------------------------------------------------------


# Training sources whose licence allows commercial use (training/fetch_realworld.py, fetch_v2.py):
# huggingnft collections (MIT), Rapidata DALL-E 3 / MJ / SD3 / Flux (CDLA-Permissive-2.0), SDXL (MIT).
# Excluded by --permissive-only: Tiny-GenImage (CC BY-NC-SA), WikiArt, Art-Images and Real-vs-AI-Art
# (research use), the local CryptoPunks / AI-art folders (unclear terms).
PERMISSIVE_SOURCES = {"nft_collections", "rapidata", "sdxl"}
PERM_MANIFEST = BACKEND_DIR / "data" / "permissive_extra" / "manifest.json"


def _load_perm(r):
    return Image.open(PERM_MANIFEST.parent / "images" / r["file"]).convert("RGB")


class Data:
    def __init__(self, permissive=False, extras=False):
        self.rw = load_manifest()
        self.oos = oos_manifest()
        self.split = np.array([r["split"] for r in self.rw])
        if permissive:  # fit + thresholds on permissive sources only; every test set stays complete
            fit = np.isin(self.split, ["train", "val"])
            ok = np.array([r["source"] in PERMISSIVE_SOURCES for r in self.rw])
            self.split = np.where(fit & ~ok, "excluded", self.split)
        # permissive mode adds the permissive genuine paintings / photos (train + val rows only)
        use = (permissive or extras) and PERM_MANIFEST.exists()
        self.extra = json.loads(PERM_MANIFEST.read_text()) if use else []
        self.n_rw = len(self.rw)
        if self.extra:
            self.rw = self.rw + self.extra
            self.split = np.r_[self.split, [r["split"] for r in self.extra]]
        self.y_rw = np.array([r["label"] == "ai" for r in self.rw])
        self.y_oos = np.array([r["label"] == "ai" for r in self.oos])
        self.dom = np.array([r["domain"] for r in self.oos])
        self.group = np.array([r["group"] for r in self.oos])
        self.sel = np.array([r["selection"] for r in self.oos])

    def feats(self, key):
        rw, oos = np.load(BENCH_DIR / f"{key}_rw.npz"), np.load(BENCH_DIR / f"{key}_oos.npz")
        assert list(rw["files"]) == [r["file"] for r in self.rw[: self.n_rw]], f"{key}: realworld manifest changed"
        assert list(oos["files"]) == [r["file"] for r in self.oos], f"{key}: run `features` again"
        x = rw["x"].astype(np.float32)
        if self.extra:
            perm = np.load(BENCH_DIR / f"{key}_perm.npz")
            assert list(perm["files"]) == [r["file"] for r in self.extra], f"{key}: run `features` (perm)"
            x = np.r_[x, perm["x"].astype(np.float32)]
        return x, oos["x"].astype(np.float32)


def primary(y, p, dom, review):
    """OOS balanced accuracy at `review`: (AI flagged + 1 - macro genuine false-alarm rate) / 2."""
    flag = p >= review
    tpr = flag[y].mean()
    fprs = [flag[~y & (dom == d)].mean() for d in GENUINE_DOMAINS if (~y & (dom == d)).any()]
    return float((tpr + 1 - np.mean(fprs)) / 2)


def oos_metrics(D, p, review, fail, mask):
    y, dom, grp = D.y_oos[mask], D.dom[mask], D.group[mask]
    p = p[mask]
    out = {
        "auc": stats.auc_ci(y, p),
        "balanced_acc_at_review": round(primary(y, p, dom, review), 4),
        "ai_flagged": stats.rate(p[y] >= review),
        "ai_failed": stats.rate(p[y] >= fail),
        "genuine_flagged_by_domain": {
            d: stats.rate(p[~y & (dom == d)] >= review) for d in GENUINE_DOMAINS if (~y & (dom == d)).any()
        },
        "genuine_failed_by_domain": {
            d: stats.rate(p[~y & (dom == d)] >= fail) for d in GENUINE_DOMAINS if (~y & (dom == d)).any()
        },
        "ai_flagged_by_generator": {
            g.split(":", 1)[1]: stats.rate(p[y & (grp == g)] >= review) for g in sorted(set(grp[y]))
        },
    }
    v1 = np.array([g.startswith("v1:") for g in grp])  # the v1 protocol, for continuity
    if v1.any() and y[v1].any() and (~y[v1]).any():
        out["v1_protocol"] = {
            "auc": stats.auc_ci(y[v1], p[v1]),
            "genuine_correct_at_0.5": stats.rate(p[v1 & ~y] < 0.5),
            "flux_detected_at_0.5": stats.rate(p[v1 & y] >= 0.5),
        }
    return out


def evaluate_candidate(D, name, p_rw, p_oos):
    tr, va, te, un = (D.split == s for s in ("train", "val", "test", "test_unseen"))
    review, fail = thresholds(p_rw[va & ~D.y_rw])
    te_auth = te & ~D.y_rw
    unseen_vs_auth = np.r_[np.flatnonzero(te_auth), np.flatnonzero(un)]
    res = {
        "thresholds": {"review": review, "fail": fail},
        "val_auc": round(stats.auc(D.y_rw[va], p_rw[va]), 4),
        "test": {
            "auc": stats.auc_ci(D.y_rw[te], p_rw[te]),
            "genuine_flagged": stats.rate(p_rw[te_auth] >= review),
            "ai_flagged": stats.rate(p_rw[te & D.y_rw] >= review),
        },
        "test_unseen_flux": {
            "flagged": stats.rate(p_rw[un] >= review),
            "auc_vs_test_genuine": stats.auc_ci(D.y_rw[unseen_vs_auth], p_rw[unseen_vs_auth]),
        },
        "oos_select": oos_metrics(D, p_oos, review, fail, D.sel == "select"),
        "oos_report": oos_metrics(D, p_oos, review, fail, D.sel == "report"),
        "oos_all": oos_metrics(D, p_oos, review, fail, np.ones(len(D.oos), bool)),
    }
    return res


def compare(D, p_new, p_old, th_new, th_old, mask):
    """Paired bootstrap on the same OOS images: primary metric and AUC, new - old."""
    y, dom = D.y_oos[mask], D.dom[mask]
    strata = np.array([f"{a}|{b}" for a, b in zip(y, dom)])
    both = np.stack([p_new[mask], p_old[mask]], 1)
    # each model keeps its own (val-fixed) threshold, so stats.paired_diff does not fit: bootstrap here
    rng = np.random.default_rng(0)
    groups = [np.flatnonzero(strata == s) for s in np.unique(strata)]
    d_prim, d_auc = [], []
    for _ in range(stats.N_BOOT):
        i = np.concatenate([rng.choice(g, len(g)) for g in groups])
        d_prim.append(primary(y[i], both[i, 0], dom[i], th_new) - primary(y[i], both[i, 1], dom[i], th_old))
        d_auc.append(stats.auc(y[i], both[i, 0]) - stats.auc(y[i], both[i, 1]))

    def summary(point, boots):
        lo, hi = np.percentile(boots, [2.5, 97.5])
        return {
            "diff": round(float(point), 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "p_not_better": round(float((np.array(boots) <= 0).mean()), 4),
        }

    return {
        "balanced_acc_at_review": summary(
            primary(y, both[:, 0], dom, th_new) - primary(y, both[:, 1], dom, th_old), d_prim
        ),
        "auc": summary(stats.auc(y, both[:, 0]) - stats.auc(y, both[:, 1]), d_auc),
    }


def latency():
    if LATENCY_FILE.exists():
        return json.loads(LATENCY_FILE.read_text())["p50_ms"]
    return {}


def evaluate(keys, permissive=False, extras=False):
    D = Data(permissive, extras)
    tr, va = D.split == "train", D.split == "val"
    probs, probes, results = {}, {}, {}
    for key in keys:
        if not (BENCH_DIR / f"{key}_oos.npz").exists():
            print(f"skip {key}: no features")
            continue
        Xrw, Xoos = D.feats(key)
        params, vauc = fit_probe(Xrw[tr], D.y_rw[tr], Xrw[va], D.y_rw[va])
        probs[key] = (predict(params, Xrw), predict(params, Xoos))
        probes[key] = params
        print(f"{key}: C={params['C']} val AUC {vauc:.4f}", flush=True)
    # fine-tuned candidates (training/lora_finetune.py): realworld rows only, so not with extras
    for f in [] if (permissive or extras) else sorted(BENCH_DIR.glob("probs_*.npz")):
        d = np.load(f)
        if list(d["oos_files"]) != [r["file"] for r in D.oos]:  # OOS set grew: rerun lora_finetune --score-only
            print(f"skip {f.stem}: scored on an older OOS manifest")
            continue
        probs[f.stem.removeprefix("probs_")] = (d["rw"], d["oos"])
    singles = list(probs)
    for a, b in itertools.combinations([k for k in singles if k in probes], 2):
        pa, pb = probs[a], probs[b]
        w = max(np.arange(0.0, 1.01, 0.1), key=lambda w: stats.auc(D.y_rw[va], w * pa[0][va] + (1 - w) * pb[0][va]))
        w = round(float(w), 1)
        if w in (0.0, 1.0):  # degenerate: same as a single backbone
            continue
        probs[f"{a}+{b}"] = (w * pa[0] + (1 - w) * pb[0], w * pa[1] + (1 - w) * pb[1])
        results.setdefault("_ensemble_weights", {})[f"{a}+{b}"] = w
    lat = latency()
    for name, (p_rw, p_oos) in probs.items():
        results[name] = evaluate_candidate(D, name, p_rw, p_oos)
        parts = name.split("+")
        if all(p in lat for p in parts):
            results[name]["cpu_ms_p50"] = round(sum(lat[p] for p in parts))
    base = results[BASELINE]
    sel = D.sel == "select"
    for name in probs:
        if name == BASELINE:
            continue
        r = results[name]
        r["vs_baseline_select"] = compare(
            D, probs[name][1], probs[BASELINE][1], r["thresholds"]["review"], base["thresholds"]["review"], sel
        )
        c = r["vs_baseline_select"]
        r["beats_baseline"] = bool(c["balanced_acc_at_review"]["ci95"][0] > 0 and c["auc"]["ci95"][1] >= 0)
    meta = {k: v for k, v in results.items() if k.startswith("_")}
    cands = {k: v for k, v in results.items() if not k.startswith("_")}
    out = OUT.with_name(
        "ai_detector_permissive.json" if permissive else "ai_detector_extras.json" if extras else OUT.name
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated": time.strftime("%Y-%m-%d %H:%M"),
                "permissive_only": permissive,
                **meta,
                "candidates": cands,
                "n_oos": {"select": int(sel.sum()), "report": int((~sel).sum())},
            },
            indent=1,
        )
    )
    if not (permissive or extras):
        BENCH_DIR.joinpath("probes.json").write_text(json.dumps(probes))
    table(cands, out.with_name(out.stem + "_table.md"))
    return cands


def table(cands, path=None):
    rows = []
    hdr = (
        "| Candidate | CPU ms | test AUC | Flux (test_unseen) flagged | OOS-select bal. acc @review | "
        "OOS-select AUC | OOS AI flagged | OOS genuine flagged (NFT / pixel / painting / photo) | "
        "Δ bal. acc vs B/32 [95 % CI] | beats B/32 |"
    )
    rows.append(hdr)
    rows.append("|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|")
    order = sorted(cands, key=lambda k: -cands[k]["oos_select"]["balanced_acc_at_review"])
    for k in order:
        r = cands[k]
        s = r["oos_select"]
        g = s["genuine_flagged_by_domain"]
        gf = " / ".join(f"{100 * g[d]['rate']:.1f}" if d in g else "-" for d in GENUINE_DOMAINS)
        c = r.get("vs_baseline_select", {}).get("balanced_acc_at_review")
        delta = f"{100 * c['diff']:+.1f} pp [{100 * c['ci95'][0]:+.1f}, {100 * c['ci95'][1]:+.1f}]" if c else "baseline"
        rows.append(
            f"| {k} | {r.get('cpu_ms_p50', '?')} | {r['test']['auc']['auc']:.3f} | "
            f"{100 * r['test_unseen_flux']['flagged']['rate']:.1f} % | {100 * s['balanced_acc_at_review']:.1f} % | "
            f"{s['auc']['auc']:.3f} [{s['auc']['ci95'][0]:.3f}, {s['auc']['ci95'][1]:.3f}] | "
            f"{100 * s['ai_flagged']['rate']:.1f} % | {gf} | {delta} | "
            f"{'yes' if r.get('beats_baseline') else ('-' if k == BASELINE else 'no')} |"
        )
    text = "\n".join(rows)
    (path or OUT.parent / "ai_detector_table.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "features", "evaluate", "ship"])
    ap.add_argument("--keys", default=",".join(REGISTRY))
    ap.add_argument("--permissive-only", action="store_true", help="fit on MIT / CDLA-Permissive sources only")
    ap.add_argument("--extras", action="store_true", help="all sources + the permissive genuine extras")
    ap.add_argument("--name", help="ship: the candidate to write to model/ai_detector.json")
    args = ap.parse_args()
    keys = [k for k in args.keys.split(",") if k]
    if args.step == "fetch":
        from detection.backbones import fetch_vision

        for k in keys:
            print(k, fetch_vision(k), flush=True)
    elif args.step == "features":
        features(keys)
    elif args.step == "ship":
        ship(args.name, args.permissive_only)
    else:
        evaluate(keys, args.permissive_only, args.extras)


def ship(name, permissive=False):
    """Write a probe candidate ("key" or "keyA+keyB") as model/ai_detector.json (format 2).
    The previous file is kept as model/benchmarks/ai_detector_previous.json."""
    import shutil

    from config import MODEL_DIR as _MD

    target = _MD / ("ai_detector.permissive.json" if permissive else "ai_detector.json")

    res = json.loads((OUT.with_name("ai_detector_permissive.json") if permissive else OUT).read_text())
    cand = res["candidates"][name]
    parts = name.split("+")
    w = res.get("_ensemble_weights", {}).get(name, 1.0)
    weights = {parts[0]: w} if len(parts) == 1 else {parts[0]: w, parts[1]: round(1 - w, 4)}
    D = Data(permissive)
    tr, va = D.split == "train", D.split == "val"
    probes = {}
    for k in parts:
        Xrw, _ = D.feats(k)
        probes[k], _ = fit_probe(Xrw[tr], D.y_rw[tr], Xrw[va], D.y_rw[va])
    if target.exists():
        shutil.copy(target, OUT.with_name(f"{target.stem}_previous.json"))
    rep = cand["oos_report"]
    target.write_text(
        json.dumps(
            {
                "format": 2,
                "version": time.strftime(f"ai-probe-{name}{'-permissive' if permissive else ''}-%Y%m%d"),
                "backbones": parts,
                "probes": probes,
                "weights": weights,
                "thresholds": cand["thresholds"],
                "licences": {k: REGISTRY[k][2] for k in parts},
                "permissive_only": permissive,
                "metrics": {
                    "test_auc": cand["test"]["auc"],
                    "test_unseen_flux": cand["test_unseen_flux"],
                    "oos_report": {
                        k: rep[k] for k in ("auc", "balanced_acc_at_review", "ai_flagged", "genuine_flagged_by_domain")
                    },
                },
            }
        )
    )
    print("shipped", name, "->", target)


if __name__ == "__main__":
    main()
