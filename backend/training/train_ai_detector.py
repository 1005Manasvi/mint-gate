"""Train the AI-generated-content ensemble (spectral LR + CLIP linear probe) on real-world data.

Features are cached in model/ai_features.npz so re-training is instant.
Regularisation strength C is chosen on the validation split.

Run from backend/:   python -m training.train_ai_detector
"""

import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

from config import AI_DETECTOR_FILE, MODEL_DIR
from training.fetch_realworld import MANIFEST
from training.spectral_worker import spectral_of

CACHE = MODEL_DIR / "ai_features.npz"
SPLITS = ("train", "val", "test", "test_unseen")
WORKERS = 4  # each worker is light (no torch), but keep memory use modest


def featurise():
    if CACHE.exists():
        d = np.load(CACHE, allow_pickle=False)
        return {k: d[k] for k in d.files}
    # Heavy imports only in the main process (workers import spectral_worker only).
    from detection.clip_similarity import ClipEncoder
    from training.realworld_data import open_image

    manifest = json.loads(MANIFEST.read_text())
    enc = ClipEncoder()
    out = {}
    for split in SPLITS:
        recs = [r for r in manifest if r["split"] == split]
        with ProcessPoolExecutor(WORKERS) as ex:
            out[f"{split}_spec"] = np.array(
                list(
                    tqdm(
                        ex.map(spectral_of, [r["file"] for r in recs], chunksize=32),
                        total=len(recs),
                        desc=f"spectral {split}",
                    )
                )
            )
        embs = []
        for i in tqdm(range(0, len(recs), 64), desc=f"clip {split}"):
            embs.append(enc.encode([open_image(r) for r in recs[i : i + 64]]))
        out[f"{split}_clip"] = np.concatenate(embs)
        out[f"{split}_y"] = np.array([1 if r["label"] == "ai" else 0 for r in recs])
    np.savez(CACHE, **out)
    return out


def fit(Xtr, ytr, Xva, yva):
    scaler = StandardScaler().fit(Xtr)
    best = None
    for C in (0.01, 0.03, 0.1, 0.3, 1.0):
        clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced").fit(scaler.transform(Xtr), ytr)
        auc = roc_auc_score(yva, clf.predict_proba(scaler.transform(Xva))[:, 1])
        if best is None or auc > best[0]:
            best = (auc, C, clf)
    auc, C, clf = best
    print(f"  best C={C} (val AUC {auc:.4f})")
    params = {
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "C": C,
    }
    return params, lambda X: clf.predict_proba(scaler.transform(X))[:, 1]


def report(p, y):
    pred = (p >= 0.5).astype(int)
    m = {"n": int(len(y)), "accuracy": round(float(accuracy_score(y, pred)), 4)}
    if len(set(y)) > 1:
        m["f1"] = round(float(f1_score(y, pred)), 4)
        m["roc_auc"] = round(float(roc_auc_score(y, p)), 4)
    else:
        m["detection_rate"] = round(float(pred.mean()), 4)
    return m


def main():
    f = featurise()
    print("training spectral model")
    spec_params, spec_pred = fit(f["train_spec"], f["train_y"], f["val_spec"], f["val_y"])
    print("training CLIP linear probe")
    clip_params, clip_pred = fit(f["train_clip"], f["train_y"], f["val_clip"], f["val_y"])

    # Ensemble weight w (prob = w*clip + (1-w)*spectral) chosen on the validation split.
    vs, vc = spec_pred(f["val_spec"]), clip_pred(f["val_clip"])
    w = max(np.arange(0.0, 1.01, 0.05), key=lambda w: roc_auc_score(f["val_y"], w * vc + (1 - w) * vs))
    w = round(float(w), 2)
    print(f"ensemble weight on CLIP probe: {w}")
    combine = lambda ps, pc: w * pc + (1 - w) * ps

    metrics = {"ensemble_weight_clip": w}
    for split in ("test", "test_unseen"):
        ps, pc = spec_pred(f[f"{split}_spec"]), clip_pred(f[f"{split}_clip"])
        y = f[f"{split}_y"]
        metrics[split] = {
            "spectral": report(ps, y),
            "clip_probe": report(pc, y),
            "ensemble": report(combine(ps, pc), y),
        }
    # Unseen generator (Flux) vs the authentic test images -> a proper ROC-AUC.
    auth = f["test_y"] == 0
    y = np.concatenate([np.zeros(auth.sum()), np.ones(len(f["test_unseen_y"]))]).astype(int)
    ps = np.concatenate([spec_pred(f["test_spec"][auth]), spec_pred(f["test_unseen_spec"])])
    pc = np.concatenate([clip_pred(f["test_clip"][auth]), clip_pred(f["test_unseen_clip"])])
    metrics["unseen_vs_authentic"] = {
        "spectral": report(ps, y),
        "clip_probe": report(pc, y),
        "ensemble": report(combine(ps, pc), y),
    }
    print(json.dumps(metrics, indent=2))
    AI_DETECTOR_FILE.write_text(
        json.dumps(
            {
                "version": time.strftime("ai-ensemble-%Y%m%d-%H%M"),
                "spectral": spec_params,
                "clip_probe": clip_params,
                "weight_clip": w,
                "metrics": metrics,
            }
        )
    )
    print("saved", AI_DETECTOR_FILE)


if __name__ == "__main__":
    main()
