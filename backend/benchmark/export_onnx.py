"""Export SSCD (and DINOv2 if the engine uses it) to ONNX, fp32 + dynamic int8, and check parity.

Deployment runs uploads on CPU while the reference vectors in Postgres were computed by PyTorch on
the GPU, so the check is exactly that mix: test-split queries embedded with ONNX Runtime (2 threads)
against the cached PyTorch reference vectors. A variant is usable only if its micro-AP stays within
0.005 of PyTorch on the same queries. Results -> model/benchmarks/onnx_parity.json.

Run from backend/:   python -m benchmark.export_onnx [--n 600]
"""

import argparse
import json
import random
import time

import numpy as np
import torch

from benchmark import copydet as cd
from config import MODEL_DIR
from detection import fingerprints as fp

ONNX_DIR = MODEL_DIR / "copydet"


def export(name, module, size):
    path = ONNX_DIR / f"{name}.onnx"
    if not path.exists():
        x = torch.randn(1, 3, size, size)
        torch.onnx.export(
            module,
            x,
            str(path),
            input_names=["pixels"],
            output_names=["embedding"],
            dynamic_axes={"pixels": {0: "batch"}, "embedding": {0: "batch"}},
            opset_version=17,
        )
    q8 = ONNX_DIR / f"{name}.int8.onnx"
    if not q8.exists():
        from onnxruntime.quantization import QuantType, quantize_dynamic

        quantize_dynamic(str(path), str(q8), weight_type=QuantType.QInt8)
    return path, q8


class DinoCLS(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, x):
        return self.m(pixel_values=x).pooler_output


def session(path):
    import onnxruntime as ort

    opt = ort.SessionOptions()
    opt.intra_op_num_threads = 2  # a 2-vCPU container
    opt.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), opt, providers=["CPUExecutionProvider"])


def embed(sess, images, size):
    out, ts = [], []
    for im in images:
        x = fp._to_tensor([im], size).numpy()
        t = time.perf_counter()
        e = sess.run(None, {"pixels": x})[0][0]
        ts.append((time.perf_counter() - t) * 1000)
        out.append(e / np.linalg.norm(e))
    return np.array(out, np.float32), ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    n = ap.parse_args().n
    params = json.loads((MODEL_DIR / "copy_engine.json").read_text())
    b = cd.Bench()
    rng = random.Random(11)
    test = [i for i in range(len(b.queries)) if b.qsplit[i] == "test"]
    pos = [i for i in test if b.gt[i] >= 0]
    sample = sorted(pos + rng.sample([i for i in test if b.gt[i] < 0], max(0, n - len(pos))))
    imgs = [b.query_image(i) for i in sample]

    models = {"sscd": (fp.SSCD(device="cpu").model.float(), fp.SSCD.size)}
    if params.get("dino"):
        d = fp.DinoV2("facebook/" + params["dino"], device="cpu")
        models[params["dino"]] = (DinoCLS(d.model.float()), d.size)
    ONNX_DIR.mkdir(parents=True, exist_ok=True)
    report = {"queries": len(sample), "copies": len(pos), "threads": 2}
    for name, (module, size) in models.items():
        ref = cd.load(f"{name}_refs")
        torch_q = cd.load(f"{name}_queries")[sample]
        S = torch_q @ ref.T
        mask = np.zeros(len(b.queries), bool)
        mask[sample] = True
        sub = cd.Bench.__new__(cd.Bench)
        sub.__dict__.update(b.__dict__)
        sub.gt = b.gt[sample]
        base_ap = cd.metrics(sub, *cd.topk(S), np.ones(len(sample), bool))["micro_ap"]
        res = {"torch_micro_ap": base_ap}
        for variant, path in zip(("fp32", "int8"), export(name, module, size)):
            e, ts = embed(session(path), imgs, size)
            cos = (e * torch_q).sum(1)
            ap_v = cd.metrics(sub, *cd.topk(e @ ref.T), np.ones(len(sample), bool))["micro_ap"]
            res[variant] = {
                "file_mb": round(path.stat().st_size / 1e6, 1),
                "cos_vs_torch_mean": round(float(cos.mean()), 5),
                "cos_vs_torch_min": round(float(cos.min()), 5),
                "micro_ap": ap_v,
                "usable": bool(base_ap - ap_v <= 0.005),
                "p50_ms": round(float(np.percentile(ts, 50)), 1),
                "p95_ms": round(float(np.percentile(ts, 95)), 1),
            }
            print(f"[onnx] {name} {variant}: {res[variant]}", flush=True)
        report[name] = res
    (cd.OUT_DIR / "onnx_parity.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
