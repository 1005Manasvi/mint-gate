"""Retraining gate: add samples of a new generator, retrain the probe, ship ONLY if nothing regresses.

  python -m training.retrain_gate --images DIR --generator NAME [--apply]
  python -m training.retrain_gate --hf REPO --hf-column image --generator NAME --n 400 [--apply]

1. The new images get the training normalisation and are split 80 / 10 / 10 into train / val /
   test (recorded in data/realworld/manifest.json only with --apply).
2. The shipped detector's backbone(s) (model/ai_detector.json) encode them; the probe(s) are refit
   on train + new train, C on val (same recipe as `training.ai_backbones`).
3. Gate, against the shipped probe on the SAME images:
     a. out-of-source "select" half (`training/oos_data.py`): paired bootstrap of the primary metric
        (balanced accuracy at the review threshold); ship only if the 95 % CI lower bound of the
        change is >= -NON_INFERIORITY (1 pp): the new data may not cost generalisation
     b. genuine false alarms per out-of-source domain may not rise by more than 2 pp (point estimate)
     c. the new generator's held-out test images must be detected at least as well as before
   The new generator must not appear in the out-of-source set (it would no longer be "unseen").
4. --apply writes the new probe to model/ai_detector.json (previous one kept in
   model/benchmarks/ai_detector_previous.json) and the new images to the manifest. Without it,
   the script only reports. Re-run `training.calibrate` and `training.evaluate_system` afterwards.

How to add a new generator: docs/models.md, "Adding a new generator".
"""

import argparse
import io
import json
import random
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from config import AI_DETECTOR_FILE
from training import stats
from training.ai_backbones import OUT, Data, compare, fit_probe, oos_metrics, predict, primary, thresholds
from training.fetch_realworld import IMG_DIR, MANIFEST, normalise

NON_INFERIORITY = 0.01
MAX_FPR_RISE = 0.02


def load_new(args):
    if args.images:
        files = sorted(p for p in Path(args.images).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        return [(f.stem, f.read_bytes()) for f in files]
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem, list_repo_files

    fs = HfFileSystem()
    out = []
    for f in sorted(x for x in list_repo_files(args.hf, repo_type="dataset") if x.endswith(".parquet")):
        pf = pq.ParquetFile(fs.open(f"datasets/{args.hf}/{f}", "rb", block_size=1 << 24))
        for rg in range(pf.num_row_groups):
            for i, r in enumerate(pf.read_row_group(rg, columns=[args.hf_column]).to_pylist()):
                v = r[args.hf_column]
                out.append((f"{Path(f).stem}_{rg}_{i}", v["bytes"] if isinstance(v, dict) else v))
                if len(out) >= args.n:
                    return out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images")
    ap.add_argument("--hf")
    ap.add_argument("--hf-column", default="image")
    ap.add_argument("--generator", required=True)
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from detection.ai_detector import AIDetector

    det = AIDetector()
    D = Data()
    if any(args.generator.lower() in g.lower() for g in set(D.group)):
        raise SystemExit(f"{args.generator} is part of the out-of-source set: it cannot be both trained on and unseen")
    rng = random.Random(f"gate-{args.generator}")
    new = []
    for key, raw in load_new(args)[: args.n]:
        try:
            view = Image.open(io.BytesIO(normalise(Image.open(io.BytesIO(raw)))))
            view.load()
        except Exception:
            continue
        x = rng.random()
        new.append((f"gate_{args.generator}_{key}", view, "test" if x < 0.1 else "val" if x < 0.2 else "train"))
    print(f"{len(new)} new images of {args.generator}", flush=True)
    sp = np.array([s for _, _, s in new])

    tr, va = D.split == "train", D.split == "val"
    p_old_rw, p_old_oos, p_new_rw, p_new_oos, new_probes = 0, 0, 0, 0, {}
    p_old_new, p_new_new = 0, 0
    for k in det.keys:
        Xrw, Xoos = D.feats(k)
        Xn = det.backbones[k].encode([v for _, v, _ in new])
        w = det.weights[k]
        o = det.probes[k]  # the shipped probe (detection.ai_detector._LogReg)
        old = {"mean": o.mean.tolist(), "scale": o.scale.tolist(), "coef": o.coef.tolist(), "intercept": o.intercept}

        def po(X, old=old):
            return predict(old, X)

        Xtr = np.r_[Xrw[tr], Xn[sp == "train"]]
        ytr = np.r_[D.y_rw[tr], np.ones((sp == "train").sum(), bool)]
        Xva = np.r_[Xrw[va], Xn[sp == "val"]]
        yva = np.r_[D.y_rw[va], np.ones((sp == "val").sum(), bool)]
        params, _ = fit_probe(Xtr, ytr, Xva, yva)
        new_probes[k] = params
        p_old_rw, p_old_oos = p_old_rw + w * po(Xrw), p_old_oos + w * po(Xoos)
        p_new_rw, p_new_oos = p_new_rw + w * predict(params, Xrw), p_new_oos + w * predict(params, Xoos)
        p_old_new, p_new_new = p_old_new + w * po(Xn), p_new_new + w * predict(params, Xn)
    th_old = thresholds(p_old_rw[va & ~D.y_rw])
    th_new = thresholds(p_new_rw[va & ~D.y_rw])
    sel = D.sel == "select"
    cmp = compare(D, p_new_oos, p_old_oos, th_new[0], th_old[0], sel)
    m_old, m_new = oos_metrics(D, p_old_oos, *th_old, sel), oos_metrics(D, p_new_oos, *th_new, sel)
    fpr_rise = {
        d: round(m_new["genuine_flagged_by_domain"][d]["rate"] - m_old["genuine_flagged_by_domain"][d]["rate"], 4)
        for d in m_old["genuine_flagged_by_domain"]
    }
    te = sp == "test"
    det_old = stats.rate(p_old_new[te] >= th_old[0])
    det_new = stats.rate(p_new_new[te] >= th_new[0])
    checks = {
        "a_oos_non_inferior": cmp["balanced_acc_at_review"]["ci95"][0] >= -NON_INFERIORITY,
        "b_genuine_fpr_rise_ok": all(v <= MAX_FPR_RISE for v in fpr_rise.values()),
        "c_new_generator_not_worse": (det_new["rate"] or 0) >= (det_old["rate"] or 0),
    }
    report = {
        "generator": args.generator,
        "n_new": len(new),
        "thresholds_old": th_old,
        "thresholds_new": th_new,
        "oos_select_primary": {
            "old": round(primary(D.y_oos[sel], p_old_oos[sel], D.dom[sel], th_old[0]), 4),
            "new": round(primary(D.y_oos[sel], p_new_oos[sel], D.dom[sel], th_new[0]), 4),
            "paired": cmp["balanced_acc_at_review"],
        },
        "genuine_fpr_rise_by_domain": fpr_rise,
        "new_generator_test_detected": {"old": det_old, "new": det_new},
        "checks": checks,
        "ship": all(checks.values()),
        "applied": False,
    }
    if report["ship"] and args.apply:
        cur = json.loads(AI_DETECTOR_FILE.read_text())
        shutil.copy(AI_DETECTOR_FILE, OUT.with_name("ai_detector_previous.json"))
        cur.update(
            {
                "probes": new_probes,
                "thresholds": {"review": th_new[0], "fail": th_new[1]},
                "version": cur["version"] + time.strftime(f"+{args.generator}-%Y%m%d"),
            }
        )
        cur.setdefault("added_generators", []).append(args.generator)
        AI_DETECTOR_FILE.write_text(json.dumps(cur))
        man = json.loads(MANIFEST.read_text())
        for key, view, split in new:
            view.convert("RGB").save(IMG_DIR / f"{key}.jpg", quality=95)
            man.append(
                {"file": f"{key}.jpg", "label": "ai", "source": "gate", "generator": args.generator, "split": split}
            )
        MANIFEST.write_text(json.dumps(man))
        report["applied"] = True
        print("applied: re-run `training.ai_backbones features` (manifest changed), calibrate, evaluate_system")
    out = OUT.with_name(f"retrain_gate_{args.generator}.json")
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
