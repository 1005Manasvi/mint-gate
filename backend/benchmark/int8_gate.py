"""Does the CPU int8 path keep the AI detector's accuracy? (hardware rule: int8 only when accuracy holds)

The shipped probes (model/ai_detector.json) were fit on GPU fp16 features. Here every backbone runs
on the CPU with dynamic int8 Linear layers (`Backbone(quantize=True)`), on
  - the genuine realworld VAL images (thresholds are re-derived exactly as for fp16), and
  - the whole out-of-source set,
and the ensemble is compared with the fp16 result on the SAME images: paired bootstrap of the
primary metric (training/ai_backbones.py) on both halves. int8 ships for the CPU profile only if
the 95 % CI of the change lies above -1 pp on the select half.

Output: model/benchmarks/ai_detector_int8.json.   Run from backend/:   python -m benchmark.int8_gate
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
NON_INFERIORITY = 0.01


def main():
    import numpy as np
    import torch

    from config import AI_DETECTOR_FILE, MODEL_DIR
    from detection.ai_detector import _LogReg
    from detection.backbones import Backbone
    from training import ai_backbones as ab
    from training.realworld_data import open_image

    torch.set_num_threads(2)
    det = json.loads(AI_DETECTOR_FILE.read_text())
    D = ab.Data()
    va = D.split == "val"
    val_gen = np.flatnonzero(va & ~D.y_rw)
    p16_rw, p16_oos, p8_val, p8_oos = 0, 0, 0, 0
    t0 = time.time()
    for k in det["backbones"]:
        w, probe = det["weights"][k], det["probes"][k]
        Xrw, Xoos = D.feats(k)
        p16_rw = p16_rw + w * ab.predict(probe, Xrw)
        p16_oos = p16_oos + w * ab.predict(probe, Xoos)
        bb = Backbone(k, device="cpu", quantize=True)
        lr = _LogReg(probe)

        def enc(recs, loader, bb=bb, k=k):
            out = []
            with ThreadPoolExecutor(2) as ex:
                for i in range(0, len(recs), 16):
                    out.append(bb.encode(list(ex.map(loader, recs[i : i + 16]))))
                    if (i // 16) % 25 == 0:
                        print(f"  {k} {i}/{len(recs)} {time.time() - t0:.0f}s", flush=True)
            return np.concatenate(out)

        Xv = enc([D.rw[i] for i in val_gen], open_image)
        Xo = enc(D.oos, ab._load_oos)
        p8_val = p8_val + w * np.array([lr(x) for x in Xv])
        p8_oos = p8_oos + w * np.array([lr(x) for x in Xo])
        del bb
    th16 = ab.thresholds(p16_rw[val_gen])
    th8 = ab.thresholds(p8_val)
    out = {"thresholds_fp16": th16, "thresholds_int8": th8,
           "max_abs_prob_diff_oos": round(float(np.abs(p8_oos - p16_oos).max()), 4),
           "mean_abs_prob_diff_oos": round(float(np.abs(p8_oos - p16_oos).mean()), 4)}
    for half in ("select", "report"):
        m = D.sel == half
        out[half] = {
            "int8_minus_fp16": ab.compare(D, p8_oos, p16_oos, th8[0], th16[0], m),
            "int8": {k: v for k, v in ab.oos_metrics(D, p8_oos, *th8, m).items() if k != "ai_flagged_by_generator"},
        }
    c = out["select"]["int8_minus_fp16"]["balanced_acc_at_review"]
    out["int8_holds_accuracy"] = bool(c["ci95"][0] > -NON_INFERIORITY)
    (MODEL_DIR / "benchmarks" / "ai_detector_int8.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("select", "report")}, indent=1))
    print("select:", out["select"]["int8_minus_fp16"], "\nreport:", out["report"]["int8_minus_fp16"])


if __name__ == "__main__":
    main()
