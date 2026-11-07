"""ONNX Runtime (fp32) export of the AI-detector backbones for the CPU profile.

fp32 ONNX computes the same function as the PyTorch model, so the gate is a parity check (cosine of
the embeddings on real images) plus the latency gain, both measured here, back to back, 2 threads.
Output: model/backbones/<key>/vision.onnx (git-ignored) + model/benchmarks/onnx_backbones.json.

Run from backend/:   python -m benchmark.onnx_backbones [--keys clip-b32,siglip2-b16] [--n 30]
"""

import argparse
import hashlib
import json
import os
import time

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"




def main():
    import numpy as np
    import onnxruntime as ort
    import torch

    from config import MODEL_DIR
    from detection.backbones import BACKBONE_DIR, Backbone
    from training.ai_backbones import _load_oos, oos_manifest

    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default="clip-b32,siglip2-b16")
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    torch.set_num_threads(2)
    recs = oos_manifest()[:: max(1, len(oos_manifest()) // args.n)][: args.n]
    imgs = [_load_oos(r) for r in recs]
    out = {}
    for key in args.keys.split(","):
        bb = Backbone(key, device="cpu")

        class M(torch.nn.Module):
            def __init__(self, b):
                super().__init__()
                self.b = b

            def forward(self, px):
                return torch.nn.functional.normalize(self.b.features_from_pixels(px).float(), dim=-1)

        m = M(bb).eval()
        px = bb.pixels(imgs[:1])
        path = BACKBONE_DIR / key / "vision.onnx"
        torch.onnx.export(m, (px,), str(path), input_names=["pixel_values"], output_names=["embedding"],
                          dynamic_axes={"pixel_values": {0: "n"}, "embedding": {0: "n"}}, opset_version=17,
                          dynamo=False)
        man_path = BACKBONE_DIR / key / "manifest.json"
        man = json.loads(man_path.read_text())
        man["onnx_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        man_path.write_text(json.dumps(man, indent=2))
        so = ort.SessionOptions()
        so.intra_op_num_threads, so.inter_op_num_threads = 2, 1
        sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        cos, t_pt, t_ort = [], [], []
        for im in imgs:
            p = bb.pixels([im])
            t = time.perf_counter()
            with torch.inference_mode():
                a = m(p).numpy()[0]
            t_pt.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            b = sess.run(None, {"pixel_values": p.numpy()})[0][0]
            t_ort.append((time.perf_counter() - t) * 1000)
            cos.append(float(a @ b))
        out[key] = {"min_cosine": round(min(cos), 6), "pytorch_ms_p50": round(float(np.median(t_pt[1:])), 1),
                    "onnx_ms_p50": round(float(np.median(t_ort[1:])), 1),
                    "pytorch_ms_p95": round(float(np.percentile(t_pt[1:], 95)), 1),
                    "onnx_ms_p95": round(float(np.percentile(t_ort[1:], 95)), 1)}
        print(key, out[key], flush=True)
        del bb, sess
    (MODEL_DIR / "benchmarks" / "onnx_backbones.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
