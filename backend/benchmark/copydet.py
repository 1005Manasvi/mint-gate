"""Copy-detection benchmark: baseline (v1 pHash + CLIP + ORB) vs every Phase-4 candidate.

Dataset: `benchmark.build_copydet` (4000 references, 600 attacked copies, 6000 attacked distractors).
Every method produces, per query, its top-10 references with a score. Metrics (DISC21 protocol):

  micro-AP     all (query, reference, score) predictions pooled and ranked; a prediction is correct
               when the query is a copy of that reference. Distractors' predictions are false positives.
  recall@1/@10 share of copies whose source is the top-1 / in the top-10.
  TPR@FPR      a copy counts when its top-1 is the right reference and scores above the threshold at
               which 1 % (5 %) of the distractors' top-1 scores would be flagged.
Reported per domain (painting = one-off art, nft = generative collections) on the TEST split;
every hyper-parameter (score normalisation, fusion weight, crop penalty, inlier weight) is chosen
on the VAL split only. Latency: per-query feature extraction + search (+ verification), p50/p95,
GPU and CPU (2 threads, like a 2-vCPU container).

Run from backend/ (GPU jobs run one after another; features are cached and resumable):
  python -m benchmark.copydet features        # all embeddings / hashes
  python -m benchmark.copydet orb             # baseline ORB on the v1 candidate lists (CPU)
  python -m benchmark.copydet lightglue       # ALIKED+LightGlue on the top-10 of the fused retrieval
  python -m benchmark.copydet evaluate        # metrics -> model/benchmarks/copydet.json + table
  python -m benchmark.copydet latency         # p50/p95 per method, GPU + CPU
"""

import argparse
import json
import random
import sys
import time

import numpy as np

from benchmark.build_copydet import BENCH_DIR, MANIFEST, QUERY_DIR, source_path
from config import MODEL_DIR, REFERENCE_ART_DIR
from detection.preprocessing import load_path
from training.realworld_data import load_manifest

CACHE = BENCH_DIR / "cache"
OUT_DIR = MODEL_DIR / "benchmarks"
K = 10
N_BG = 2000  # background set for SSCD-style query score normalisation (AI images: never copies)
EMBEDDERS = ("clip", "sscd", "dinov2-small", "dinov2-base")
DENSE_NN = 0.95  # v1 ReferenceIndex constant
V1_POLICY = MODEL_DIR / "benchmarks" / "policy_v3.1.0.json"  # the baseline's thresholds, frozen


def log(*a):
    print(*a, flush=True)


# ---- data -------------------------------------------------------------------------------------


class Bench:
    def __init__(self):
        self.man = json.loads(MANIFEST.read_text())
        self.refs = self.man["refs"]
        self.queries = [q for q in self.man["queries"] if (QUERY_DIR / f"{q['id']}.jpg").exists()]
        self.ref_index = {r["name"]: i for i, r in enumerate(self.refs)}
        self.gt = np.array([self.ref_index[q["gt"]] if q["gt"] else -1 for q in self.queries])
        self.qdom = np.array([q["domain"] for q in self.queries])
        self.qsplit = np.array([q["split"] for q in self.queries])
        self.attack = np.array([q["attack"] for q in self.queries])
        self.rdom = np.array([r["domain"] for r in self.refs])
        self.collection = np.array([r["collection"] is not None for r in self.refs])

    def ref_image(self, i):
        return load_path(source_path(self.refs[i]["name"]))

    def query_image(self, i):
        return load_path(QUERY_DIR / f"{self.queries[i]['id']}.jpg")


def background_names():
    rng = random.Random(5)
    ai = [r["file"] for r in load_manifest() if r["label"] == "ai"]
    return rng.sample(ai, N_BG)


def _stream(loader, n, batch=64):
    for s in range(0, n, batch):
        yield [loader(i) for i in range(s, min(n, s + batch))]


def _cached(name, build):
    path = CACHE / f"{name}.npy"
    if path.exists():
        return np.load(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    t = time.time()
    arr = build()
    np.save(path, arr)
    log(f"[features] {name}: {arr.shape} in {time.time() - t:.0f}s")
    return arr


def encoder(name, device=None):
    from detection import fingerprints as fp

    kw = {} if device is None else {"device": device}
    if name == "clip":
        from detection.clip_similarity import ClipEncoder

        return ClipEncoder()
    if name == "sscd":
        return fp.SSCD(**kw)
    return fp.DinoV2("facebook/" + name, **kw)


def build_features(b: Bench):
    from detection import fingerprints as fp
    from detection.hashing import phash_int

    nr, nq = len(b.refs), len(b.queries)
    _cached("phash_refs", lambda: np.array([phash_int(b.ref_image(i)) for i in range(nr)], dtype=np.uint64))
    _cached("phash_queries", lambda: np.array([phash_int(b.query_image(i)) for i in range(nq)], dtype=np.uint64))
    _cached("pdq_refs", lambda: np.stack([np.frombuffer(fp.pdq(b.ref_image(i))[0], np.uint8) for i in range(nr)]))
    _cached("pdq_queries", lambda: np.stack([fp.pdq_dihedral(b.query_image(i)) for i in range(nq)]))
    bg = background_names()
    for name in EMBEDDERS:
        if all((CACHE / f"{name}_{s}.npy").exists() for s in ("refs", "queries", "bg")):
            continue
        enc = encoder(name)

        def run(loader, n, enc=enc):
            return np.concatenate([enc.encode(batch) for batch in _stream(loader, n)]).astype(np.float32)

        _cached(f"{name}_refs", lambda: run(b.ref_image, nr))
        _cached(f"{name}_queries", lambda: run(b.query_image, nq))
        _cached(f"{name}_bg", lambda: run(lambda i: load_path(REFERENCE_ART_DIR / bg[i]), len(bg)))
        del enc
        _free_gpu()
    # Region descriptors (collage): 18 windows per query, SSCD.
    if not (CACHE / "sscd_crops_queries.npy").exists():
        enc = encoder("sscd")

        def crops():
            out = np.zeros((nq, 18, 512), np.float16)
            for i in range(nq):
                out[i] = enc.encode(fp.region_crops(b.query_image(i)))
                if i % 1000 == 0:
                    log(f"[features] crops {i}/{nq}")
            return out

        _cached("sscd_crops_queries", crops)


# Region-crop variants: (window sizes, grid steps, SSCD input px). CPU cost ~ windows * (px / 320)^2.
CROP_VARIANTS = {
    "18x320": ((0.5, 0.7), 3, 320),
    "18x224": ((0.5, 0.7), 3, 224),
    "9x224_s05": ((0.5,), 3, 224),
    "9x224_s07": ((0.7,), 3, 224),
    "9x160_s05": ((0.5,), 3, 160),
    "4x224_s06": ((0.6,), 2, 224),
}


def crop_cache(variant):
    return "sscd_crops" if variant == "18x320" else f"sscd_crops_{variant}"


def crop_cost(variant):
    sizes, steps, px = CROP_VARIANTS[variant]
    return len(sizes) * steps * steps * (px / 320) ** 2  # in units of one full SSCD forward pass


def build_crop_variants(b: Bench):
    from detection import fingerprints as fp

    enc = None
    for v, (sizes, steps, px) in CROP_VARIANTS.items():
        if (CACHE / f"{crop_cache(v)}_queries.npy").exists():
            continue
        enc = enc or encoder("sscd")
        n = len(sizes) * steps * steps

        def run(enc=enc, sizes=sizes, steps=steps, px=px, n=n):
            out = np.zeros((len(b.queries), n, 512), np.float16)
            for i in range(len(b.queries)):
                out[i] = enc.encode(fp.region_crops(b.query_image(i), sizes=sizes, steps=steps), size=px)
            return out

        _cached(f"{crop_cache(v)}_queries", run)


def _free_gpu():
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load(name):
    return np.load(CACHE / f"{name}.npy")


# ---- scoring ----------------------------------------------------------------------------------


def nn_within(emb):
    from detection.reference_index import nearest_neighbour_sims

    return nearest_neighbour_sims(emb)


def topk(S, k=K):
    idx = np.argpartition(-S, k, axis=1)[:, :k]
    sc = np.take_along_axis(S, idx, 1)
    o = np.argsort(-sc, axis=1)
    return np.take_along_axis(idx, o, 1), np.take_along_axis(sc, o, 1)


def popcount_distances(qbits, rbits):
    """qbits (nq, h, 32) or (nq, 32) uint8; rbits (nr, 32) -> (nq, nr) min Hamming distance."""
    from detection.fingerprints import _POPCOUNT

    q = qbits if qbits.ndim == 3 else qbits[:, None, :]
    out = np.full((len(q), len(rbits)), 10**6, np.int32)
    for h in range(q.shape[1]):
        for s in range(0, len(q), 256):
            x = np.bitwise_xor(q[s : s + 256, h, None, :], rbits[None])
            out[s : s + 256] = np.minimum(out[s : s + 256], _POPCOUNT[x].sum(-1, dtype=np.int32))
    return out


def phash_distances(qh, rh):
    x = np.bitwise_xor(qh[:, None], rh[None])
    return np.unpackbits(x.view(np.uint8).reshape(len(qh), len(rh), 8), axis=2).sum(-1)


def cosine(name):
    return load(f"{name}_queries") @ load(f"{name}_refs").T


def normalised(name, alpha, beta, k_bg=10, S=None):
    """cos(q, r) - alpha * nn(r) - beta * mean top-k cos(q, background).

    alpha: v1's density-relative term (reference's own nearest neighbour inside the DB).
    beta : SSCD's query normalisation (how 'generic' the query is against a background set).
    """
    q, r, bg = load(f"{name}_queries"), load(f"{name}_refs"), load(f"{name}_bg")
    S = q @ r.T if S is None else S
    if alpha:
        S = S - alpha * nn_within(r)[None, :]
    if beta:
        qb = q @ bg.T
        S = S - beta * np.sort(qb, axis=1)[:, -k_bg:].mean(1, keepdims=True)
    return S.astype(np.float32)


def region_scores(name="sscd", variant="18x320"):
    crops = load(f"{crop_cache(variant)}_queries").astype(np.float32)
    r = load(f"{name}_refs")
    out = np.empty((len(crops), len(r)), np.float32)
    for s in range(0, len(crops), 256):
        out[s : s + 256] = np.einsum("qcd,rd->qcr", crops[s : s + 256], r).max(1)
    return out


# ---- v1 baseline --------------------------------------------------------------------------------


def _risk(c, v):
    t = (v - c["review"]) / (c["fail"] - c["review"])
    return np.clip(0.5 + 0.5 * t, 0, 1)


def v1_parts(b: Bench):
    """The v1 measurements, exactly as ReferenceIndex / the pipeline computes them on this DB."""
    clip_q, clip_r = load("clip_queries"), load("clip_refs")
    sims = clip_q @ clip_r.T
    nn = nn_within(clip_r)
    sparse = (nn < DENSE_NN) & ~b.collection
    dists = phash_distances(load("phash_queries"), load("phash_refs"))
    return sims, nn, sparse, dists


def v1_candidates(sims, dists, sparse):
    """ReferenceIndex.search: top-5 CLIP U top-3 sparse by CLIP U top-3 sparse by pHash."""
    sp = np.flatnonzero(sparse)
    out = []
    for i in range(len(sims)):
        c = list(np.argsort(-sims[i])[:5])
        c += list(sp[np.argsort(-sims[i, sp])[:3]])
        c += list(sp[np.argsort(dists[i, sp], kind="stable")[:3]])
        out.append(list(dict.fromkeys(int(x) for x in c)))
    return out


def run_orb(b: Bench):
    from detection import feature_match

    path = CACHE / "orb_v1.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    sims, _, sparse, dists = v1_parts(b)
    cands = v1_candidates(sims, dists, sparse)
    ref_cache = {}
    t = time.time()
    for i, q in enumerate(b.queries):
        if q["id"] in done:
            continue
        d = feature_match.describe(b.query_image(i))
        res = {}
        for j in cands[i]:
            if not sparse[j]:
                continue  # v1 only evaluates ORB against sparse references
            if j not in ref_cache:
                ref_cache[j] = feature_match.describe(b.ref_image(j))
            res[j] = feature_match.match(d, ref_cache[j])[0]
        done[q["id"]] = res
        if i % 250 == 0:
            path.write_text(json.dumps(done))
            log(f"[orb] {i}/{len(b.queries)} ({time.time() - t:.0f}s)")
    path.write_text(json.dumps(done))


def v1_scores(b: Bench):
    """Per-pair v1 risk = max(pHash, CLIP copy score, ORB) risk under policy 3.1.0 (any check failing
    the upload fails it); CLIP similarity breaks ties so recall@k stays meaningful."""
    pol = json.loads(V1_POLICY.read_text())["checks"]
    sims, nn, sparse, dists = v1_parts(b)
    margin = sims - nn[None, :]
    exact = sims >= 0.9999
    clip_r = np.where(b.collection[None, :], np.where(exact, 1.0, 0.0), _risk(pol["clip"], margin))
    clip_r = np.where(exact, 1.0, clip_r)
    ph_r = np.where(sparse[None, :], _risk(pol["phash"], dists), 0.0)
    orb = json.loads((CACHE / "orb_v1.json").read_text())
    orb_r = np.zeros_like(sims)
    for i, q in enumerate(b.queries):
        for j, inl in orb.get(q["id"], {}).items():
            orb_r[i, int(j)] = _risk(pol["orb"], inl)
    return (np.maximum.reduce([clip_r, ph_r, orb_r]) + 1e-3 * sims).astype(np.float32)


# ---- LightGlue re-ranking --------------------------------------------------------------------------


def run_lightglue(b: Bench, S, tag="fused"):
    """Inliers for the top-10 of retrieval `S`, resumable (cache/lightglue_<tag>.json)."""
    import torch

    from detection.local_match import LocalMatcher

    path = CACHE / f"lightglue_{tag}.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    idx, _ = topk(S)
    lm = LocalMatcher()
    ref_cache = {}
    t = time.time()
    for i, q in enumerate(b.queries):
        if q["id"] in done:
            continue
        img = b.query_image(i)
        dq, dm = lm.describe(img), lm.describe(img, mirrored=True)
        res = {}
        for j in idx[i]:
            j = int(j)
            if j not in ref_cache:
                f = lm.describe(b.ref_image(j))
                f.pop("gray")
                ref_cache[j] = f
            res[j] = lm.verify(dq, dm, ref_cache[j])[0]
        done[q["id"]] = res
        if i % 200 == 0:
            path.write_text(json.dumps(done))
            log(f"[lightglue] {i}/{len(b.queries)} ({time.time() - t:.0f}s, {len(ref_cache)} refs cached)")
            torch.cuda.empty_cache()
    path.write_text(json.dumps(done))
    return done


def inlier_matrix(b, done):
    idx = np.zeros((len(b.queries), K), np.int64)
    inl = np.zeros((len(b.queries), K), np.float32)
    for i, q in enumerate(b.queries):
        items = sorted(done[q["id"]].items(), key=lambda kv: -kv[1])
        for k, (j, n) in enumerate(items[:K]):
            idx[i, k], inl[i, k] = int(j), n
    return idx, inl


# ---- metrics ------------------------------------------------------------------------------------


def metrics(b: Bench, idx, sc, mask):
    """idx / sc: (nq, K) ranked predictions. mask: queries to include."""
    gt = b.gt[mask]
    idx, sc = idx[mask], sc[mask]
    pos = gt >= 0
    correct = idx == gt[:, None]
    flat_s, flat_c = sc.ravel(), correct.ravel()
    o = np.argsort(-flat_s, kind="stable")
    tp = flat_c[o]
    n_gt = int(pos.sum())
    prec = np.cumsum(tp) / np.arange(1, len(tp) + 1)
    ap = float((prec * tp).sum() / max(n_gt, 1))
    top1_ok = correct[:, 0]
    dis_top = sc[~pos, 0]
    out = {
        "n_copies": n_gt,
        "n_distractors": int((~pos).sum()),
        "micro_ap": round(ap, 4),
        "recall_at_1": round(float(top1_ok[pos].mean()), 4),
        "recall_at_10": round(float(correct[pos].any(1).mean()), 4),
    }
    for fpr in (0.01, 0.05):
        t = np.quantile(dis_top, 1 - fpr)
        out[f"tpr_at_{int(fpr * 100)}pct_fpr"] = round(float((top1_ok[pos] & (sc[pos, 0] > t)).mean()), 4)
    return out


def report(b: Bench, idx, sc, split="test"):
    res = {}
    for dom in ("painting", "nft"):
        res[dom] = metrics(b, idx, sc, (b.qdom == dom) & (b.qsplit == split))
    res["all"] = metrics(b, idx, sc, b.qsplit == split)
    pos = (b.gt >= 0) & (b.qsplit == split)
    res["recall_at_1_by_attack"] = {
        a: round(float((idx[pos & (b.attack == a), 0] == b.gt[pos & (b.attack == a)]).mean()), 3)
        for a in sorted(set(b.attack[pos]))
    }
    return res


def objective(b, S_or_topk):
    idx, sc = S_or_topk if isinstance(S_or_topk, tuple) else topk(S_or_topk)
    return metrics(b, idx, sc, b.qsplit == "val")["micro_ap"]


def tune(b, label, build, grid):
    """Pick the parameter set with the best VAL micro-AP."""
    best = None
    for params in grid:
        v = objective(b, build(**params))
        if best is None or v > best[0]:
            best = (v, params)
    log(f"[tune] {label}: best val micro-AP {best[0]:.4f} with {best[1]}")
    return best[1]


def evaluate():
    b = Bench()
    log(f"[evaluate] {len(b.refs)} refs, {len(b.queries)} queries ({int((b.gt >= 0).sum())} copies)")
    results, params = {}, {}

    def add(name, S_or_topk, desc, **p):
        idx, sc = S_or_topk if isinstance(S_or_topk, tuple) else topk(S_or_topk)
        results[name] = {"description": desc, "params": p, **report(b, idx, sc)}
        a, pa, nf = results[name]["all"], results[name]["painting"], results[name]["nft"]
        log(
            f"{name:28s} AP {a['micro_ap']:.3f} | painting AP {pa['micro_ap']:.3f} TPR@1% {pa['tpr_at_1pct_fpr']:.3f}"
            f" R@1 {pa['recall_at_1']:.3f} | nft AP {nf['micro_ap']:.3f} TPR@1% {nf['tpr_at_1pct_fpr']:.3f}"
        )
        return idx, sc

    # Baseline: v1 components and the v1 engine (policy 3.1.0)
    ph = phash_distances(load("phash_queries"), load("phash_refs"))
    add("v1_phash", (64 - ph).astype(np.float32) / 64, "pHash 64-bit, 1 - distance/64")
    add("v1_clip_raw", cosine("clip"), "CLIP ViT-B/32 cosine")
    add("v1_clip_density", normalised("clip", 1.0, 0), "CLIP cosine - reference nn similarity (v1 copy score)")
    if (CACHE / "orb_v1.json").exists():
        add("v1_engine", v1_scores(b), "v1: max policy-3.1.0 risk of pHash / CLIP copy score / ORB (sparse refs)")
    # PDQ
    pd = popcount_distances(load("pdq_queries")[:, :1], load("pdq_refs"))
    add("pdq", (256 - pd).astype(np.float32) / 256, "PDQ 256-bit, 1 - distance/256")
    pd8 = popcount_distances(load("pdq_queries"), load("pdq_refs"))
    add("pdq_dihedral", (256 - pd8).astype(np.float32) / 256, "PDQ, min distance over the 8 dihedral query hashes")
    # Global descriptors, each with its normalisation tuned on val
    grid = [{"alpha": a, "beta": bb} for a in (0, 0.5, 1.0) for bb in (0, 0.5, 1.0)]
    for name in EMBEDDERS:
        add(f"{name}_raw", cosine(name), f"{name} cosine")
        p = tune(b, name, lambda alpha, beta, n=name: normalised(n, alpha, beta), grid)
        params[name] = p
        add(f"{name}_norm", normalised(name, **p), f"{name}, density + query normalisation (val-tuned)", **p)
    # Fusion SSCD + DINOv2: each size tuned on val; ViT-B only if it beats ViT-S by MIN_GAIN in the
    # fusion itself (hardware budget: ViT-S is 4x cheaper on CPU; fewer / smaller wins ties).
    Ss = normalised("sscd", **params["sscd"])
    fusion_grid = [{"w": w, "mode": "sum"} for w in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)] + [{"w": 0, "mode": "max"}]
    cands = {}
    for d in ("dinov2-small", "dinov2-base"):
        Sd_ = normalised(d, **params[d])
        p_ = tune(b, f"fusion {d}", lambda w, mode, Sd_=Sd_: fuse(Ss, Sd_, {"w": w, "mode": mode}), fusion_grid)
        cands[d] = (objective(b, fuse(Ss, Sd_, p_)), p_)
    dino = "dinov2-base" if cands["dinov2-base"][0] - cands["dinov2-small"][0] >= MIN_GAIN else "dinov2-small"
    log(f"[gate] DINOv2 size: small {cands['dinov2-small'][0]:.4f} vs base {cands['dinov2-base'][0]:.4f} -> {dino}")
    pf = {**cands[dino][1], "dino": dino}
    Sd = normalised(dino, **params[dino])
    add("fusion", fuse(Ss, Sd, pf), f"SSCD + {dino} (val-tuned)", **pf)
    gates = {"dino": gate("DINOv2 fusion", objective(b, Ss), objective(b, fuse(Ss, Sd, pf)))}
    Sf = fuse(Ss, Sd, pf) if gates["dino"] else Ss
    # Region crops (collage), SSCD, same normalisation as the full image. Every crop variant is
    # tuned on val; the cheapest one within MIN_GAIN of the best ships (CPU budget: <= 1.5 s/upload).
    variants = {}
    for v in CROP_VARIANTS:
        if not (CACHE / f"{crop_cache(v)}_queries.npy").exists():
            continue
        Rv = normalised("sscd", **params["sscd"], S=region_scores("sscd", v))
        pv = tune(
            b, f"regions {v}", lambda d, Rv=Rv: np.maximum(Sf, Rv - d), [{"d": d} for d in (0.0, 0.05, 0.1, 0.15, 0.2)]
        )
        variants[v] = (objective(b, np.maximum(Sf, Rv - pv["d"])), pv)
    best = max(x[0] for x in variants.values())
    chosen = min((v for v, x in variants.items() if x[0] >= best - MIN_GAIN), key=crop_cost)
    log(
        f"[gate] crop variant: { ({v: round(x[0], 4) for v, x in variants.items()}) } -> {chosen} (cost {crop_cost(chosen):.2f})"
    )
    sizes, steps, px = CROP_VARIANTS[chosen]
    pr = {**variants[chosen][1], "variant": chosen, "sizes": list(sizes), "steps": steps, "px": px}
    pr["cpu"] = cpu_crop_variant(variants)
    R = normalised("sscd", **params["sscd"], S=region_scores("sscd", chosen))
    Sfr = np.maximum(Sf, R - pr["d"])
    add("retrieval_regions", Sfr, "gated fusion, max with SSCD region crops minus a penalty (val-tuned)", **pr)
    gates["regions"] = gate("region crops", objective(b, Sf), objective(b, Sfr))
    cfg = {**params, "fusion": pf, "regions": pr, "gates": gates}
    Sx = retrieval(cfg)
    add("retrieval_pdq", Sx, "gated retrieval, PDQ <= 31 bits forced to the top")
    # LightGlue re-ranking of the top-10 (cache built by `lightglue` for exactly this retrieval)
    lg_path = CACHE / "lightglue_fused.json"
    gates["lightglue"] = False
    lg_done = json.loads(lg_path.read_text()) if lg_path.exists() else {}
    if len(lg_done) < len(b.queries):
        log(f"[evaluate] LightGlue cache incomplete ({len(lg_done)}/{len(b.queries)}): run `lightglue` first")
    else:
        idx_lg, inl = inlier_matrix(b, lg_done)
        base = np.take_along_axis(Sx, idx_lg, 1)

        def rerank(g, cap):
            s = base + g * np.minimum(inl, cap) / cap
            o = np.argsort(-s, 1)
            return np.take_along_axis(idx_lg, o, 1), np.take_along_axis(s, o, 1)

        pg = tune(
            b, "lightglue", rerank, [{"g": g, "cap": c} for g in (0.1, 0.2, 0.3, 0.5, 1.0) for c in (25, 50, 100, 200)]
        )
        add("final_engine", rerank(**pg), "gated retrieval + PDQ, LightGlue inliers re-rank the top-10", **pg)
        o = np.argsort(-inl, 1)
        add(
            "lightglue_only",
            (np.take_along_axis(idx_lg, o, 1), np.take_along_axis(inl, o, 1)),
            "inliers only, same top-10",
        )
        gate("LightGlue re-rank (pooled micro-AP, informational)", objective(b, Sx), objective(b, rerank(**pg)))
        cfg["lightglue"] = pg
        # In the engine LightGlue is an independent check on one-off works (collection members are
        # excluded, as for v1's ORB). Gate it in that role: share of val copies flagged by
        # semantic OR geometric (best threshold pair) vs semantic alone, same <= 1 % val genuine budget.
        cfg["lightglue_check"] = lightglue_check(b, cfg, idx_lg, inl)
        gates["lightglue"] = cfg["lightglue_check"]["adopt"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "dataset": {
            "refs": len(b.refs),
            "queries": len(b.queries),
            "copies": int((b.gt >= 0).sum()),
            "distractors": int((b.gt < 0).sum()),
            "split": "metrics on test, tuning on val",
            "attacks": sorted(set(b.attack)),
        },
        "results": results,
        "tuned": cfg,
    }
    (OUT_DIR / "copydet.json").write_text(json.dumps(out, indent=2))
    write_table(out)
    write_engine_params(cfg)


MIN_GAIN = 0.005  # val micro-AP a component must add to ship (CLAUDE.md: fewer models wins ties)


CPU_BUDGET_MS = 1500  # CLAUDE.md hardware budget: <= 1.5 s per upload on a 2-vCPU container
CPU_OVERHEAD_MS = 300  # decode, canonical PNG, report hash, ~12 pgvector queries: measured ~280 ms (latency.py)


def cpu_crop_variant(variants):
    """The crop variant the CPU profile can afford, from benchmark.latency's component timings
    (p50, 2 threads). None if even the cheapest one would break the budget."""
    path = OUT_DIR / "latency_components_cpu.json"
    if not path.exists():
        log("[gate] CPU crop budget: no latency_components_cpu.json yet (run benchmark.latency components)")
        return None
    t = json.loads(path.read_text())
    fixed = sum(t[k]["p50_ms"] for k in ("resnet18", "clip_ai_probe", "copy_move", "pdq_dihedral", "sscd_full"))
    fixed += t["dinov2_small"]["p50_ms"] + CPU_OVERHEAD_MS
    left = CPU_BUDGET_MS - fixed
    fits = {v: x for v, x in variants.items() if t.get(f"crops_{v}", {}).get("p50_ms", 1e9) <= left}
    choice = max(fits, key=lambda v: fits[v][0]) if fits else None
    log(f"[gate] CPU crop budget: {fixed:.0f} ms fixed, {left:.0f} ms left -> {choice or 'no region crops'}")
    if choice is None:
        return None
    sizes, steps, px = CROP_VARIANTS[choice]
    return {**fits[choice][1], "variant": choice, "sizes": list(sizes), "steps": steps, "px": px}


def lightglue_check(b, cfg, idx_lg, inl, fpr=0.01):
    S = np.where(b.collection[None, :], -np.inf, retrieval(cfg, pin=False)).max(1)
    G = np.where(b.collection[idx_lg], 0, inl).max(1)
    gen, val = b.gt < 0, b.qsplit == "val"

    def rates(flag, split):
        m = b.qsplit == split
        return float(flag[m & ~gen].mean()), float(flag[m & gen].mean())

    t = np.quantile(S[val & gen], 1 - fpr)
    sem = S >= t
    best = (rates(sem, "val")[0], None, sem)
    for ts in np.quantile(S[val & gen], np.linspace(1 - fpr, 1, 11)):
        for tg in range(5, 301, 5):
            f = (S >= ts) | (G >= tg)
            tpr, fp = rates(f, "val")
            if fp <= fpr and tpr > best[0]:
                best = (tpr, (float(ts), tg), f)
    out = {
        "semantic_only": {"val": rates(sem, "val"), "test": rates(sem, "test")},
        "semantic_or_geometric": {"thresholds": best[1], "val": rates(best[2], "val"), "test": rates(best[2], "test")},
        "note": "(copies flagged, genuine flagged); thresholds chosen on val",
    }
    out["adopt"] = bool(out["semantic_or_geometric"]["val"][0] - out["semantic_only"]["val"][0] >= 0.005)
    log(f"[gate] LightGlue as a check: {json.dumps(out)} -> {'ADOPT' if out['adopt'] else 'REJECT'}")
    return out


def gate(label, without, with_):
    keep = with_ - without >= MIN_GAIN
    log(f"[gate] {label}: val micro-AP {without:.4f} -> {with_:.4f}: {'ADOPT' if keep else 'REJECT'}")
    return bool(keep)


def fuse(Ss, Sd, pf):
    return np.maximum(Ss, Sd) if pf["mode"] == "max" else pf["w"] * Ss + (1 - pf["w"]) * Sd


def retrieval(cfg, pin=True):
    """The gated retrieval score matrix (+ PDQ pin): what LightGlue re-ranks and the engine runs.
    pin=False: the engine's copy_semantic score itself."""
    S = normalised("sscd", **cfg["sscd"])
    if cfg["gates"]["dino"]:
        d = cfg["fusion"]["dino"]
        S = fuse(S, normalised(d, **cfg[d]), cfg["fusion"])
    if cfg["gates"]["regions"]:
        v = cfg["regions"].get("variant", "18x320")
        S = np.maximum(S, normalised("sscd", **cfg["sscd"], S=region_scores("sscd", v)) - cfg["regions"]["d"])
    if not pin:
        return S.astype(np.float32)
    pd8 = popcount_distances(load("pdq_queries"), load("pdq_refs"))
    return np.where(pd8 <= 31, np.maximum(S, 1.0 + (31 - pd8) / 310), S).astype(np.float32)


def write_engine_params(cfg):
    """model/copy_engine.json + the background set the engine needs at run time."""
    g = cfg["gates"]
    dino = cfg["fusion"]["dino"] if g["dino"] else None
    params = {
        "version": "copy-engine-1.0.0",
        "sscd": cfg["sscd"],
        "dino": dino,
        "fusion": {k: cfg["fusion"][k] for k in ("w", "mode")} if dino else {"w": 1.0, "mode": "sum"},
        "regions": {k: v for k, v in cfg["regions"].items() if k != "cpu"} if g["regions"] else None,
        # CPU profile (no GPU): only what fits the 1.5 s budget (cpu_crop_variant); None = no crops.
        "regions_cpu": cfg["regions"].get("cpu") if g["regions"] else None,
        "lightglue": bool(g.get("lightglue", True)),
        "bg_k": 10,
        "retrieve_k": 50,
        "crop_k": 5,
        "verify_k": 10,
        "pdq_max_bits": 31,
        "pdq_min_quality": 50,
        "evidence_min_inliers": 15,
        "collection_semantic": False,
        "collection_geometric": False,
        "benchmark": "model/benchmarks/copydet.json",
    }
    if dino:
        params[dino] = cfg[dino]
    (MODEL_DIR / "copy_engine.json").write_text(json.dumps(params, indent=2))
    bg = {"sscd": load("sscd_bg").astype(np.float16)}
    if dino:
        bg[dino] = load(f"{dino}_bg").astype(np.float16)
    (MODEL_DIR / "copydet").mkdir(exist_ok=True)
    np.savez_compressed(MODEL_DIR / "copydet" / "background.npz", **bg)
    log(f"[evaluate] wrote {MODEL_DIR / 'copy_engine.json'} (dino={dino}, regions={g['regions']})")


def write_table(out):
    rows = [
        "| method | painting micro-AP | painting TPR@1%FPR | painting R@1 | nft micro-AP | nft TPR@1%FPR | nft R@1 | all micro-AP |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in out["results"].items():
        p, n, a = r["painting"], r["nft"], r["all"]
        rows.append(
            f"| {name} | {p['micro_ap']:.3f} | {p['tpr_at_1pct_fpr']:.3f} | {p['recall_at_1']:.3f} | "
            f"{n['micro_ap']:.3f} | {n['tpr_at_1pct_fpr']:.3f} | {n['recall_at_1']:.3f} | {a['micro_ap']:.3f} |"
        )
    (OUT_DIR / "copydet_table.md").write_text("\n".join(rows) + "\n")
    log("\n".join(rows))


# ---- latency -----------------------------------------------------------------------------------


def _latency_on(device, imgs, refs, ref_imgs) -> dict:
    from detection import clip_similarity, feature_match
    from detection import fingerprints as fp
    from detection.hashing import phash_int
    from detection.local_match import LocalMatcher

    clip_similarity.DEVICE = device  # ClipEncoder reads the module-level device
    clip = encoder("clip")
    sscd, dino = fp.SSCD(device=device), fp.DinoV2("facebook/dinov2-small", device=device)
    lm = LocalMatcher(device=device)
    lm_refs = [lm.describe(im) for im in ref_imgs]
    orb_refs = [feature_match.describe(im) for im in ref_imgs]

    def v1(im):
        phash_int(im)
        refs["clip"] @ clip.encode_one(im)
        q = feature_match.describe(im)
        for r in orb_refs:  # v1 runs ORB on ~10 candidates
            feature_match.match(q, r)

    def fused(im):
        fp.pdq_dihedral(im)
        refs["sscd"] @ sscd.encode_one(im)
        refs["dinov2-small"] @ dino.encode_one(im)

    def fused_regions(im):
        fused(im)
        refs["sscd"] @ sscd.encode(fp.region_crops(im)).T

    def final(im):
        fused_regions(im)
        dq, dm = lm.describe(im), lm.describe(im, mirrored=True)
        for r in lm_refs:
            lm.verify(dq, dm, r)

    tests = {
        "v1_engine": v1,
        "pdq": fp.pdq_dihedral,
        "sscd": lambda im: refs["sscd"] @ sscd.encode_one(im),
        "fusion": fused,
        "fusion_regions": fused_regions,
        "final_engine (top-10 LightGlue)": final,
    }
    out = {}
    for name, fn in tests.items():
        fn(imgs[0])  # warm-up
        ts = []
        for im in imgs:
            t = time.perf_counter()
            fn(im)
            ts.append((time.perf_counter() - t) * 1000)
        out[name] = {"p50_ms": round(float(np.percentile(ts, 50)), 1), "p95_ms": round(float(np.percentile(ts, 95)), 1)}
        log(f"[latency] {device} {name}: {out[name]}")
    return out


def latency(n=40):
    """Per-query extraction + brute-force search (+ verification) on a 4000-reference DB.
    CPU = 2 torch threads (a 2-vCPU container); PyTorch eager, not ONNX (see export_onnx)."""
    import torch

    b = Bench()
    rng = random.Random(3)
    imgs = [b.query_image(i) for i in rng.sample(range(len(b.queries)), n)]
    refs = {name: load(f"{name}_refs") for name in ("clip", "sscd", "dinov2-small")}
    ref_imgs = [b.ref_image(j) for j in rng.sample(range(len(b.refs)), 10)]
    out = {}
    for device in ("cuda", "cpu"):
        if device == "cuda" and not torch.cuda.is_available():
            continue
        if device == "cpu":
            torch.set_num_threads(2)
        for name, v in _latency_on(device, imgs, refs, ref_imgs).items():
            out.setdefault(name, {})[device] = v
        _free_gpu()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "copydet_latency.json").write_text(json.dumps(out, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("features", "crops", "orb", "lightglue", "evaluate", "latency"))
    step = ap.parse_args().step
    if step == "features":
        build_features(Bench())
    elif step == "crops":
        build_crop_variants(Bench())
    elif step == "orb":
        run_orb(Bench())
    elif step == "lightglue":
        b = Bench()
        run_lightglue(b, fused_for_rerank(b))
    elif step == "evaluate":
        evaluate()
    else:
        latency()


def fused_for_rerank(b):
    """The retrieval whose top-10 LightGlue verifies: the gated config from copydet.json."""
    return retrieval(json.loads((OUT_DIR / "copydet.json").read_text())["tuned"])


if __name__ == "__main__":
    sys.exit(main())
