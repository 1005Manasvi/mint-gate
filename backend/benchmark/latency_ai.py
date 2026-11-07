"""CPU cost (2 threads) of the Phase 5 per-upload steps, in a process of their own.

(`benchmark.latency components` loads every model of the pipeline into one process; on this laptop
that combination crashes inside MKL under memory pressure, so the new steps are timed here.)

  backbone_<key>_fp32 / _int8   frozen AI-detector backbones on the detector view; int8 = PyTorch
                                dynamic quantisation of every nn.Linear (weights int8, activations fp32)
  provenance                    detection.provenance.analyse on the upload bytes (PNG, as the
                                pipeline benchmark sends them)

Run from backend/:   python -m benchmark.latency_ai [--n 30]
"""

import argparse
import io
import json
import os
import random
import time

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"


def pct(ts):
    import numpy as np

    return {"p50_ms": round(float(np.percentile(ts, 50)), 1), "p95_ms": round(float(np.percentile(ts, 95)), 1)}


def int8(model):
    import torch

    return torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)


def main():
    import torch

    from benchmark import copydet as cd
    from detection import provenance
    from detection.backbones import Backbone
    from detection.preprocessing import detector_view

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    torch.set_num_threads(2)
    b = cd.Bench()
    rng = random.Random(8)  # same queries as benchmark.latency pipeline
    imgs = [b.query_image(i) for i in rng.sample(range(len(b.queries)), args.n)]
    raws = []
    for im in imgs:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        raws.append(buf.getvalue())
    out = {"sizes": sorted({im.size for im in imgs})[:10]}

    def time_it(name, fn, items):
        fn(items[0])
        ts = []
        for it in items:
            t = time.perf_counter()
            fn(it)
            ts.append((time.perf_counter() - t) * 1000)
        out[name] = pct(ts)
        print(name, out[name], flush=True)

    views = [detector_view(im) for im in imgs]
    for key in ("clip-b32", "siglip2-b16"):
        bb = Backbone(key, device="cpu")
        time_it(f"backbone_{key}_fp32", lambda v, bb=bb: bb.encode_one(v), views)
        bb.model = int8(bb.model)
        time_it(f"backbone_{key}_int8", lambda v, bb=bb: bb.encode_one(v), views)
        del bb
    time_it("provenance", lambda i: provenance.analyse(raws[i], imgs[i]), list(range(len(imgs))))
    (cd.OUT_DIR / "latency_ai_cpu.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
