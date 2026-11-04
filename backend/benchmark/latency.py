"""Deployment latency + on-chain index quality (Phase 4 acceptance numbers).

  index    pgvector HNSW over every ready reference: p50 / p95 of one fused-vector KNN (k = 50),
           one SSCD KNN, one PDQ Hamming lookup, and recall@10 of HNSW vs exact search (sequential
           scan with the index disabled) for 200 benchmark queries.
  pipeline the whole MintGatePipeline.analyse (ResNet-18, CLIP probe, copy-move, copy engine against
           the real pgvector index) per upload, on GPU and on CPU with 2 torch threads (2-vCPU box).

Run from backend/ (needs Postgres with embedded references):
  python -m benchmark.latency index|pipeline|components|copy_recall [--n 40] [--device cpu|cuda]
Results -> model/benchmarks/latency_<step>[_<device>].json
"""

import os
import sys

if "--device" in sys.argv and sys.argv[sys.argv.index("--device") + 1] == "cpu" or "components" in sys.argv:
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # hide the GPU before anything imports torch

import argparse  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402

import numpy as np
from sqlalchemy import text

import db
from benchmark import copydet as cd


def pct(ts):
    return {"p50_ms": round(float(np.percentile(ts, 50)), 1), "p95_ms": round(float(np.percentile(ts, 95)), 1)}


def index(n=200):
    from pgvector import Bit, HalfVector

    b = cd.Bench()
    rng = random.Random(4)
    sample = rng.sample(range(len(b.queries)), n)
    eng = json.loads((cd.MODEL_DIR / "copy_engine.json").read_text())
    w = eng["fusion"]["w"]
    sscd, dino = cd.load("sscd_queries")[sample], cd.load(f"{eng['dino']}_queries")[sample]
    fused = np.hstack([np.sqrt(w) * sscd, np.sqrt(1 - w) * dino])
    pdq = cd.load("pdq_queries")[sample][:, 0]
    with db.session() as s:
        size = s.execute(text("SELECT count(*) FROM reference_items WHERE copy_vec IS NOT NULL")).scalar()
        idx_mb = {
            r[0]: round(r[1] / 1e6, 1)
            for r in s.execute(
                text(
                    "SELECT indexrelname, pg_relation_size(indexrelid) FROM pg_stat_user_indexes "
                    "WHERE indexrelname IN ('ix_reference_items_copy_vec','ix_reference_items_sscd','ix_reference_items_pdq')"
                )
            )
        }
    out = {"references": int(size), "index_mb": idx_mb}

    def run(sql, params, exact=False, ef=100):
        with db.session() as s:
            s.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
            if exact:
                s.execute(text("SET LOCAL enable_indexscan = off"))
            t = time.perf_counter()
            rows = s.execute(text(sql), params).all()
            return (time.perf_counter() - t) * 1000, rows

    knn = (
        "SELECT id, -({c} <#> CAST(:q AS halfvec)) FROM reference_items WHERE {c} IS NOT NULL "
        "ORDER BY {c} <#> CAST(:q AS halfvec) LIMIT {k}"
    )
    for name, col, vecs in (("fused_knn50", "copy_vec", fused), ("sscd_knn50", "sscd", sscd)):
        ts, id_hits, dist_hits = [], [], []
        for i, v in enumerate(vecs):
            q = {"q": HalfVector(v).to_text()}
            dt, ann = run(knn.format(c=col, k=50), q)
            ts.append(dt)
            if i < 50:  # exact search is a full scan: 50 queries are enough for recall@10
                _, ex = run(knn.format(c=col, k=10), q, exact=True)
                id_hits.append(len({r[0] for r in ann[:10]} & {r[0] for r in ex}) / 10)
                # Ties: placeholder collections hold thousands of identical vectors, so any 10 of
                # them are a correct top-10. Count an ANN hit when it is as similar as exact's 10th.
                tenth = ex[-1][1]
                dist_hits.append(sum(r[1] >= tenth - 1e-3 for r in ann[:10]) / 10)
        out[name] = {
            **pct(ts),
            "recall_at_10_ids": round(float(np.mean(id_hits)), 4),
            "recall_at_10_distance": round(float(np.mean(dist_hits)), 4),
        }
        print(f"[index] {name}: {out[name]}", flush=True)
    ts = []
    for h in pdq:
        bits = Bit(np.unpackbits(h).astype(bool)).to_text()
        dt, _ = run(
            "SELECT id FROM reference_items WHERE pdq IS NOT NULL ORDER BY pdq <~> CAST(:h AS bit(256)) LIMIT 10",
            {"h": bits},
        )
        ts.append(dt * 8)  # the engine looks up all 8 dihedral hashes
    out["pdq_8_lookups"] = pct(ts)
    print(f"[index] {out}", flush=True)
    (cd.OUT_DIR / "latency_index.json").write_text(json.dumps(out, indent=2))


def pipeline(n=40, device="cpu"):
    import torch

    if device == "cpu":
        assert not torch.cuda.is_available(), "run with --device cpu from the start (hides the GPU)"
        torch.set_num_threads(2)
    from detection.pipeline import MintGatePipeline

    b = cd.Bench()
    rng = random.Random(8)
    pipe = MintGatePipeline()
    raws = []
    for i in rng.sample(range(len(b.queries)), n):
        buf = io.BytesIO()
        b.query_image(i).save(buf, format="PNG")
        raws.append(buf.getvalue())
    pipe.analyse(raws[0])  # warm-up
    total, copy = [], []
    for raw in raws:
        t = time.perf_counter()
        rep, _ = pipe.analyse(raw)
        total.append((time.perf_counter() - t) * 1000)
        copy.append(rep["timing_ms"])
    out = {"device": device, "threads": torch.get_num_threads(), "uploads": n, "end_to_end": pct(total)}
    print(f"[pipeline] {out}", flush=True)
    (cd.OUT_DIR / f"latency_pipeline_{device}.json").write_text(json.dumps(out, indent=2))


def copy_recall():
    """Is a benchmark copy's source in the top-50 of the live index? Exact rank (references scoring
    above the source) vs HNSW at ef_search 100 / 400, for every copy whose source is a seed row.
    The engine's one-off partial index (migration 0005) is what fixes the HNSW painting number."""
    from pgvector import HalfVector

    b = cd.Bench()
    w = json.loads((cd.MODEL_DIR / "copy_engine.json").read_text())["fusion"]["w"]
    with db.session() as s:
        seed = dict(s.execute(text("SELECT image_uri, id FROM reference_items WHERE source = 'seed'")).all())
    pos = [i for i in range(len(b.queries)) if b.gt[i] >= 0 and "seed/" + b.queries[i]["gt"] in seed]
    S, D = cd.load("sscd_queries"), cd.load("dinov2-small_queries")
    top = "SELECT id FROM reference_items WHERE copy_vec IS NOT NULL {w} ORDER BY copy_vec <#> CAST(:q AS halfvec) LIMIT 50"
    res = {}
    for i in pos:
        q = HalfVector(np.concatenate([np.sqrt(w) * S[i], np.sqrt(1 - w) * D[i]])).to_text()
        g = int(seed["seed/" + b.queries[i]["gt"]])
        dom = str(b.qdom[i])
        with db.session() as s:
            v = s.execute(
                text("SELECT -(copy_vec <#> CAST(:q AS halfvec)) FROM reference_items WHERE id = :g"), {"q": q, "g": g}
            ).scalar()
            better = s.execute(
                text(
                    "SELECT count(*) FROM reference_items WHERE copy_vec IS NOT NULL AND -(copy_vec <#> CAST(:q AS halfvec)) > :v"
                ),
                {"q": q, "v": v},
            ).scalar()
        res.setdefault("exact_top50", {}).setdefault(dom, []).append(better < 50)
        for name, ef, where in (
            ("hnsw_shared_graph_ef100", 100, ""),
            ("hnsw_shared_graph_ef400", 400, ""),
            ("hnsw_oneoff_partial_index_ef100", 100, "AND NOT collection_member"),
        ):
            if where and dom != "painting":
                continue  # the partial index holds one-off works only
            with db.session() as s:
                s.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
                hit = g in {int(r[0]) for r in s.execute(text(top.format(w=where)), {"q": q})}
            res.setdefault(name, {}).setdefault(dom, []).append(hit)
    out = {"copies": len(pos), **{k: {d: round(float(np.mean(x)), 4) for d, x in v.items()} for k, v in res.items()}}
    print(f"[copy_recall] {out}", flush=True)
    (cd.OUT_DIR / "index_copy_recall.json").write_text(json.dumps(out, indent=2))


def components(n=30):
    """CPU (2 threads) time of every per-upload step, to size the region-crop budget."""
    import torch

    from detection import copy_move
    from detection import fingerprints as fp
    from detection.ai_detector import AIDetector
    from detection.clip_similarity import ClipEncoder
    from detection.preprocessing import detector_view
    from detection.resnet_classifier import ResNetClassifier

    assert not torch.cuda.is_available(), "run with --device cpu"
    torch.set_num_threads(2)
    b = cd.Bench()
    rng = random.Random(9)
    imgs = [b.query_image(i) for i in rng.sample(range(len(b.queries)), n)]
    eng = json.loads((cd.MODEL_DIR / "copy_engine.json").read_text())
    resnet, ai, clip = ResNetClassifier(), AIDetector(), ClipEncoder()
    sscd, dino = fp.SSCD(device="cpu"), fp.DinoV2("facebook/" + eng["dino"], device="cpu")
    steps = {
        "resnet18": lambda im: resnet.predict(detector_view(im)),
        "clip_ai_probe": lambda im: ai.predict(detector_view(im), clip.encode_one(detector_view(im))),
        "copy_move": copy_move.analyse,
        "pdq_dihedral": fp.pdq_dihedral,
        "sscd_full": sscd.encode_one,
        "dinov2_small": dino.encode_one,
    }
    for v, (sizes, st, px) in cd.CROP_VARIANTS.items():
        steps[f"crops_{v}"] = lambda im, sizes=sizes, st=st, px=px: sscd.encode(fp.region_crops(im, sizes, st), size=px)
    out = {}
    for name, fn in steps.items():
        fn(imgs[0])
        ts = []
        for im in imgs:
            t = time.perf_counter()
            fn(im)
            ts.append((time.perf_counter() - t) * 1000)
        out[name] = pct(ts)
        print(f"[components] {name}: {out[name]}", flush=True)
    (cd.OUT_DIR / "latency_components_cpu.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("index", "pipeline", "components", "copy_recall"))
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    if a.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # every model runs on the CPU (set before torch loads)
    if a.step == "index":
        index()
    elif a.step == "components":
        components(a.n)
    elif a.step == "copy_recall":
        copy_recall()
    else:
        pipeline(a.n, a.device)
