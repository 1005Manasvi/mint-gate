"""Manipulation-forensics benchmark (Phase 5 step 2): TruFor vs the v1 manipulation signals.

Signals compared, all on the same images:
  resnet18    v1 "forgery probability" (detector view), REVIEW-only in policy 4.0.0
  copy_move   v1 block matching (clone peak ratio) + its block mask
  trufor      TruFor image score + localisation map (detection/trufor.py)

Data
  real forgeries   `benchmark.build_forgery` (CASIA v2, Columbia, COVERAGE, IMD2020, CocoGlide)
  genuine art      realworld `val` authentic (thresholds: review / fail = 95th / 99th percentile,
                   as calibrate.py does) and `test` authentic + the out-of-source genuine set
                   (`training/oos_data.py`) for per-domain false-alarm rates (NFT art is not a photo:
                   a camera-trace detector may mis-fire on it)
Metrics (95 % CIs: training/stats.py)
  image level   ROC-AUC per dataset with real images; detection rate at the review threshold per
                dataset; genuine flagged per domain
  pixel level   F1 of the binarised map (TruFor: map >= 0.5; copy-move: detected blocks) vs the
                ground-truth mask, averaged over manipulated images (fixed threshold, no per-image
                oracle threshold, so the numbers are deployable, not optimistic)

Steps (run from backend/):
  python -m benchmark.forgery fetch      # TruFor weights -> model/trufor/ (SHA-256 manifest)
  python -m benchmark.forgery run        # scores -> data/benchmark/forgery/scores.npz (GPU)
  python -m benchmark.forgery evaluate   # model/benchmarks/forgery.json + forgery_table.md
"""

import argparse
import hashlib
import json
import time
import zipfile

import numpy as np
from PIL import Image

from config import BACKEND_DIR, MODEL_DIR
from training import stats

DATA = BACKEND_DIR / "data" / "benchmark" / "forgery"
SCORES = DATA / "scores.npz"
OUT = MODEL_DIR / "benchmarks" / "forgery.json"
GENUINE_DOMAINS = ("nft_collection", "pixel_art", "painting", "photo")
SIGNALS = ("resnet18", "copy_move", "trufor")


def fetch(zip_path=None):
    """Download TruFor_weights.zip (261 MB) unless a local zip / an extracted trufor.pth.tar exists,
    then convert it to safetensors (the only file kept)."""
    from detection.trufor import WEIGHTS, WEIGHTS_URL

    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    if zip_path is None and not (WEIGHTS.parent / "trufor.pth.tar").exists():
        import requests

        zip_path = WEIGHTS.parent / "TruFor_weights.zip"
        with requests.get(WEIGHTS_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    import numpy as np
    import torch
    from safetensors.torch import save_file

    raw = WEIGHTS.parent / "trufor.pth.tar"
    if zip_path is not None or not raw.exists():
        with zipfile.ZipFile(zip_path) as z:
            name = next(n for n in z.namelist() if n.endswith("trufor.pth.tar"))
            raw.write_bytes(z.read(name))
    src_digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    # The checkpoint is a pickle. It is loaded ONCE, with weights_only=True (no code execution; the
    # only non-tensor globals in it are numpy scalar / dtype, allow-listed here), and converted to
    # safetensors; the runtime only ever reads the safetensors file.
    allowed = [(np._core.multiarray.scalar, "numpy.core.multiarray.scalar"), np.dtype, type(np.dtype("float64"))]
    with torch.serialization.safe_globals(allowed):
        ckpt = torch.load(raw, map_location="cpu", weights_only=True)
    save_file({k: v.contiguous() for k, v in ckpt["state_dict"].items()}, str(WEIGHTS))
    raw.unlink()
    digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
    (WEIGHTS.parent / "manifest.json").write_text(
        json.dumps(
            {"file": WEIGHTS.name, "sha256": digest, "source": WEIGHTS_URL, "source_sha256": src_digest,
             "licence": "GRIP-UNINA non-commercial"},
            indent=2,
        )
    )
    print("TruFor weights", WEIGHTS, digest)


def items():
    """(key, kind, record): manipulated / pristine benchmark images, then genuine-art sets."""
    from training.oos_data import IMG_DIR as OOS_DIR
    from training.oos_data import MANIFEST as OOS_MAN
    from training.realworld_data import IMG_DIR as RW_DIR
    from training.realworld_data import load_manifest

    out = []
    for r in json.loads((DATA / "manifest.json").read_text()):
        out.append((r["file"], "bench", r, DATA / r["file"]))
    for r in load_manifest():
        if r["label"] == "authentic" and r["split"] in ("val", "test"):
            out.append((f"rw:{r['file']}", f"rw_{r['split']}", r, RW_DIR / r["file"]))
    for r in json.loads(OOS_MAN.read_text()):
        if r["label"] == "authentic":
            out.append((f"oos:{r['file']}", "oos", r, OOS_DIR / r["file"]))
    return out


def block_mask(cm, size):
    """Copy-move detected blocks as a mask at the image's size."""
    from detection.copy_move import BLOCK

    w, h = cm["size"]
    m = np.zeros((h, w), np.uint8)
    for x, y in cm["boxes"]:
        m[y : y + BLOCK, x : x + BLOCK] = 1
    return np.asarray(Image.fromarray(m * 255).resize(size, Image.NEAREST)) > 127


def f1(pred, gt):
    tp = float((pred & gt).sum())
    fp, fn = float((pred & ~gt).sum()), float((~pred & gt).sum())
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def run(limit=None):
    from detection import copy_move
    from detection.preprocessing import detector_view, load_path
    from detection.resnet_classifier import ResNetClassifier
    from detection.trufor import TruFor

    its = items()[:limit] if limit else items()
    resnet, tf = ResNetClassifier(), TruFor()
    keys, res = [], {s: [] for s in SIGNALS}
    pix = {"trufor_f1": [], "copy_move_f1": [], "trufor_ms": []}

    def save():
        np.savez(SCORES, keys=np.array(keys), **{k: np.array(v, float) for k, v in {**res, **pix}.items()})

    if SCORES.exists():  # resume: a crash (this laptop runs out of memory) must not cost the whole run
        d = np.load(SCORES)
        keys = list(d["keys"])
        for k in (*res, *pix):
            (res if k in res else pix)[k] = list(d[k])
        print(f"resuming after {len(keys)} images", flush=True)
    done = set(keys)
    t0 = time.time()
    for n, (key, kind, rec, path) in enumerate(its):
        if key in done:
            continue
        img = load_path(path)
        keys.append(key)
        res["resnet18"].append(resnet.predict(detector_view(img)))
        cm = copy_move.analyse(img)
        res["copy_move"].append(cm["score"])
        t = tf.analyse(img)
        res["trufor"].append(t["score"])
        pix["trufor_ms"].append(t["ms"])
        if kind == "bench" and rec.get("mask"):
            gt = np.asarray(Image.open(DATA / rec["mask"]).convert("L").resize(t["size"], Image.NEAREST)) > 127
            pix["trufor_f1"].append(f1(t["map"] >= 0.5, gt))
            pix["copy_move_f1"].append(f1(block_mask(cm, t["size"]), gt))
        else:
            pix["trufor_f1"].append(np.nan)
            pix["copy_move_f1"].append(np.nan)
        if n % 200 == 0:
            save()
            print(f"{n}/{len(its)} {time.time() - t0:.0f}s", flush=True)
    save()
    print("saved", SCORES)


def evaluate():
    d = np.load(SCORES)
    idx = {k: i for i, k in enumerate(d["keys"])}
    its = [x for x in items() if x[0] in idx]
    sel = lambda pred: np.array([idx[k] for k, kind, r, _ in its if pred(kind, r)], int)  # noqa: E731
    val = sel(lambda kind, r: kind == "rw_val")
    out = {"n_images": len(its), "signals": {}}
    for s in SIGNALS:
        x = d[s]
        neg = x[val]
        review = float(np.percentile(neg, 95))
        fail = float(max(np.percentile(neg, 99), review + 1e-3))
        if s != "copy_move":  # probabilities: same clipping as calibrate.py
            review = float(np.clip(review, 0.5, 0.95))
            fail = float(np.clip(fail, review + 0.02, 0.99))
        r = {"review": round(review, 4), "fail": round(fail, 4), "datasets": {}, "genuine": {}}
        datasets = sorted({rec["dataset"] for k, kind, rec, _ in its if kind == "bench"})
        for ds in datasets:
            fk = sel(lambda kind, rec, ds=ds: kind == "bench" and rec["dataset"] == ds and rec["label"] == "fake")
            rl = sel(lambda kind, rec, ds=ds: kind == "bench" and rec["dataset"] == ds and rec["label"] == "real")
            e = {"fake_flagged": stats.rate(x[fk] >= review), "fake_failed": stats.rate(x[fk] >= fail)}
            if len(rl):
                e["real_flagged"] = stats.rate(x[rl] >= review)
                y = np.r_[np.ones(len(fk)), np.zeros(len(rl))].astype(bool)
                e["auc"] = stats.auc_ci(y, np.r_[x[fk], x[rl]])
            f1k = f"{s}_f1"
            if f1k in d.files:
                v = d[f1k][fk]
                v = v[~np.isnan(v)]
                if len(v):
                    b = np.random.default_rng(0).choice(v, (stats.N_BOOT, len(v))).mean(1)
                    e["pixel_f1"] = {
                        "mean": round(float(v.mean()), 4),
                        "n": len(v),
                        "ci95": [round(float(np.percentile(b, 2.5)), 4), round(float(np.percentile(b, 97.5)), 4)],
                    }
            r["datasets"][ds] = e
        test = sel(lambda kind, rec: kind == "rw_test")
        r["genuine"]["realworld_test"] = stats.rate(x[test] >= review)
        for dom in GENUINE_DOMAINS:
            m = sel(lambda kind, rec, dom=dom: kind == "oos" and rec["domain"] == dom)
            if len(m):
                r["genuine"][f"oos_{dom}"] = {"flagged": stats.rate(x[m] >= review), "failed": stats.rate(x[m] >= fail)}
        out["signals"][s] = r
    ms = d["trufor_ms"]
    out["trufor_gpu_ms"] = {"p50": int(np.percentile(ms, 50)), "p95": int(np.percentile(ms, 95))}
    OUT.write_text(json.dumps(out, indent=1))
    table(out)
    return out


def table(out):
    rows = [
        "| Signal | Dataset | AUC [95 % CI] | manipulated flagged @review | pristine flagged | pixel F1 |",
        "|---|---|---|---|---|---|",
    ]
    for s, r in out["signals"].items():
        for ds, e in r["datasets"].items():
            auc = f"{e['auc']['auc']:.3f} [{e['auc']['ci95'][0]:.3f}, {e['auc']['ci95'][1]:.3f}]" if "auc" in e else "-"
            pf = e.get("pixel_f1")
            rows.append(
                f"| {s} | {ds} | {auc} | {stats.fmt_rate(e['fake_flagged'])} | "
                f"{stats.fmt_rate(e['real_flagged']) if 'real_flagged' in e else '-'} | "
                f"{pf['mean']:.3f} [{pf['ci95'][0]:.3f}, {pf['ci95'][1]:.3f}]"
                if pf
                else f"| {s} | {ds} | {auc} | {stats.fmt_rate(e['fake_flagged'])} | "
                f"{stats.fmt_rate(e['real_flagged']) if 'real_flagged' in e else '-'} | - |"
            )
    rows += [
        "",
        "| Signal | genuine flagged: realworld test | OOS NFT | OOS pixel art | OOS painting | OOS photo |",
        "|---|---|---|---|---|---|",
    ]
    for s, r in out["signals"].items():
        g = r["genuine"]
        cells = [stats.fmt_rate(g["realworld_test"])] + [
            stats.fmt_rate(g[f"oos_{d}"]["flagged"]) if f"oos_{d}" in g else "-" for d in GENUINE_DOMAINS
        ]
        rows.append(f"| {s} | " + " | ".join(cells) + " |")
    text = "\n".join(rows)
    (OUT.parent / "forgery_table.md").write_text(text + "\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "run", "evaluate"])
    ap.add_argument("--zip", default=None, help="local TruFor_weights.zip (skips the download)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.step == "fetch":
        fetch(args.zip)
    elif args.step == "run":
        run(args.limit)
    else:
        evaluate()


if __name__ == "__main__":
    main()
