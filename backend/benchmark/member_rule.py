"""Phase 5b gate for the collection-member exact-copy rule (detection.copy_engine._exact).

A PDQ-close collection member only counts as an exact copy if it is also pixel-identical:
<= member_max_diff_px differing pixels at 128 px. Real copymints showed two gaps in the Phase 4 rule
(NEAREST sampling, query as uploaded): mirrored copies (CryptoPhunks, PHAYC) and every full-size
re-upload, because the indexed reference is a LANCZOS thumbnail and NEAREST sampling of a different
resolution differs at every edge. Candidates = resample filter x dihedral (min over the 8 rotations /
mirrors, the group PDQ already searches) x per-pixel level (a pixel "differs" when a channel moves by
more than it; 48 hid CryptoPunk #2771's dark beard against its skin once resampling noise was gone). Each candidate gets its budget exactly as Phase 4 did
(training.calibrate.member_pixel_budget: half the closest genuine VAL sibling of the benchmark), then:

  A  copy benchmark (data/benchmark/copydet), TEST split: member copies accepted (right member) /
     wrong member accepted, genuine distractors falsely accepted
  B  live index (284k references), genuine uploads that are NOT indexed: the 1,000 held-out
     CryptoPunks + OOS NFT / pixel-art images. A match to a *different* token is a false alarm;
     a match to the image's own token (OOS on-chain tokens are indexed: a full-size re-upload) is a
     correct duplicate and counted separately; huggingnft images (token id unknown) are reported apart
  C  real copymints (training/copymint_data.py) whose original is indexed: original accepted

Gate: a candidate replaces the Phase 4 rule only if its genuine false acceptances (A test + B) are not
above the Phase 4 rule's counts and it accepts more copies. Output: model/benchmarks/member_rule.json
Run from backend/:   python -m benchmark.member_rule [--limit-live N]
"""

import argparse
import json
import time

import numpy as np

from config import MODEL_DIR
from detection import fingerprints as fp
from detection.copy_index import PgCopyIndex
from detection.preprocessing import load_path, prepare
from training import stats

CANDIDATES = [(r, d, lv) for r in ("nearest", "lanczos", "box") for d in (False, True) for lv in (48, 24, 16)]
PER_QUERY = 5  # the engine loads at most 5 member images per upload
MAX_BITS = 31  # copy_engine.json pdq_max_bits
OUT = MODEL_DIR / "benchmarks" / "member_rule.json"


def cname(c):
    return f"{c[0]}{'+dihedral' if c[1] else ''}@{c[2]}"


def variants(img, c):
    s = fp.rgb_small(img, c[0])
    return fp.dihedral_small(s) if c[1] else [s]


def diffs_for(q_img, refs):
    """{candidate: [diff per ref]} for one query against a list of reference images."""
    out = {}
    for c in CANDIDATES:
        qv = variants(q_img, c)
        out[cname(c)] = [fp.member_diff(qv, fp.rgb_small(r, c[0]), c[2]) for r in refs]
    return out


# ---- A: copy benchmark ----------------------------------------------------------------------------


def bench_part():
    from benchmark import copydet as cd
    from benchmark.build_copydet import source_path

    cache = cd.CACHE / "member_rule_diffs.json"
    b = cd.Bench()
    pd = cd.popcount_distances(cd.load("pdq_queries"), cd.load("pdq_refs"))
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        rows, ref_img = [], {}
        t0 = time.time()
        for i in range(len(b.queries)):
            close = np.flatnonzero(b.collection & (pd[i] <= MAX_BITS))
            close = close[np.argsort(pd[i, close], kind="stable")][:PER_QUERY]
            if not len(close):
                continue
            imgs = []
            for j in close:
                if j not in ref_img:
                    ref_img[j] = load_path(source_path(b.refs[j]["name"]))
                imgs.append(ref_img[j])
            rows.append({"q": i, "refs": [int(j) for j in close], "diffs": diffs_for(b.query_image(i), imgs)})
            if len(rows) % 200 == 0:
                print(f"  bench {i}/{len(b.queries)} ({time.time() - t0:.0f}s)", flush=True)
        cache.write_text(json.dumps(rows))
    return b, rows


def budgets(b, rows):
    """Phase 4 rule: half the closest genuine VAL sibling (training.calibrate.member_pixel_budget)."""
    out = {}
    for c in map(cname, CANDIDATES):
        worst = min((min(r["diffs"][c]) for r in rows if b.gt[r["q"]] < 0 and b.qsplit[r["q"]] == "val"), default=4096)
        out[c] = {"budget": max(0, worst // 2), "closest_genuine_val_sibling": int(worst)}
    return out


def bench_metrics(b, rows, c, budget):
    test_q = np.flatnonzero(b.qsplit == "test")
    by_q = {r["q"]: r for r in rows}
    member_copies = [i for i in test_q if b.gt[i] >= 0 and b.collection[b.gt[i]]]
    genuine = [i for i in test_q if b.gt[i] < 0 and b.qdom[i] in set(b.qdom[member_copies])]
    hit, wrong, false_acc = [], [], []
    for i in member_copies:
        r = by_q.get(i)
        acc = [j for j, d in zip(r["refs"], r["diffs"][c]) if d <= budget] if r else []
        hit.append(int(b.gt[i]) in acc)
        wrong.append(any(j != b.gt[i] for j in acc))
    for i in genuine:
        r = by_q.get(i)
        false_acc.append(bool(r) and min(r["diffs"][c]) <= budget)
    return {
        "member_copies_accepted": stats.rate(hit),
        "member_copies_wrong_member": stats.rate(wrong),
        "genuine_falsely_accepted": stats.rate(false_acc),
    }


# ---- B / C: live index ----------------------------------------------------------------------------


def live_rows(items, index, limit=None):
    """items: (key, image, self_names) -> rows with the member names + diffs per candidate."""
    rows = []
    t0 = time.time()
    for n, (key, img, selves, group) in enumerate(items[:limit] if limit else items):
        cands = [(r, d) for r, d in index.exact(fp.pdq_dihedral(img), MAX_BITS) if r.collection]
        cands = sorted(cands, key=lambda x: x[1])[:PER_QUERY]
        refs, names = [], []
        for r, _d in cands:
            try:
                refs.append(index.load_image(r))
                names.append(r.name)
            except (OSError, ValueError, TypeError):
                continue
        rows.append({"key": key, "group": group, "names": names, "self": list(selves), "diffs": diffs_for(img, refs)})
        if n % 200 == 0:
            print(f"  live {n}/{len(items)} ({time.time() - t0:.0f}s)", flush=True)
    return rows


def genuine_items():
    from ingest.collections import ETHEREUM
    from training.evaluate_system import OOS_DIR, OOS_MANIFEST
    from training.realworld_data import load_manifest
    from training.split import load_split, paths

    wpunks = next(a for a, n in ETHEREUM if n == "Wrapped Cryptopunks")
    man = load_manifest()
    used = {
        r["file"].removeprefix("local_authentic_").removesuffix(".jpg") for r in man if r["generator"] == "cryptopunk"
    }
    items = []
    for p in paths(load_split(), "authentic", "test"):
        if p.stem not in used:  # the held-out punks (genuine_new_punk); wrapped twin = its own token
            pid = p.stem.removeprefix("cryptopunk")
            items.append((p.name, load_path(p), {f"1:{wpunks}:{pid}"}, "new_punk"))
    for r in json.loads(OOS_MANIFEST.read_text()):
        if r["label"] != "authentic" or r["domain"] not in ("nft_collection", "pixel_art"):
            continue
        img = load_path(OOS_DIR / r["file"])
        if r["group"].startswith("onchain:"):
            contract = r["source"].split()[-1]
            tid = r["file"].removesuffix(".jpg").rsplit("_", 1)[1]
            items.append((r["file"], img, {f"1:{contract}:{tid}"}, "oos_onchain"))
        else:
            items.append((r["file"], img, set(), "oos_huggingnft"))
    return items


def copymint_items():
    from training.copymint_data import IMG_DIR, MANIFEST, MAPPING_MAX_MAD

    out = []
    for r in json.loads(MANIFEST.read_text()):
        if r.get("original_ref") and (r.get("mapping_mad") or 0) <= MAPPING_MAX_MAD:
            img = prepare((IMG_DIR / r["file"]).read_bytes()).rgb
            out.append((r["file"], img, {r["original_ref"]}, f"copymint:{r['collection']}"))
    return out


def live_metrics(rows, c, budget):
    by = {}
    for r in rows:
        acc = [nm for nm, d in zip(r["names"], r["diffs"][c]) if d <= budget]
        g = by.setdefault(r["group"], {"self_accepted": [], "other_accepted": []})
        g["self_accepted"].append(any(nm in r["self"] for nm in acc))
        g["other_accepted"].append(any(nm not in r["self"] for nm in acc))
    return {g: {k: stats.rate(v) for k, v in d.items()} for g, d in by.items()}


# ---- the SSCD member margin (copy_member) --------------------------------------------------------

SEM_K = 10


def semantic_live(items, index):
    """Per query: the top-SEM_K members by SSCD for the upload and its mirror, with cos and nn_sscd."""
    from PIL import Image

    sscd = fp.SSCD()
    out = []
    for i in range(0, len(items), 32):
        chunk = items[i : i + 32]
        e = sscd.encode([x[1] for x in chunk])
        em = sscd.encode([x[1].transpose(Image.FLIP_LEFT_RIGHT) for x in chunk])
        for (key, _img, selves, group), v, vm in zip(chunk, e, em):
            cands = []
            for tag, vec in (("as_is", v), ("mirror", vm)):
                for r in index.knn_members(vec, SEM_K):
                    cands.append((tag, r.name, round(float(r.sscd @ vec), 4), round(r.nn_sscd, 4)))
            out.append({"key": key, "group": group, "self": list(selves), "cands": cands})
        print(f"  semantic {i + len(chunk)}/{len(items)}", flush=True)
    return out


def _half(key: str) -> int:
    import hashlib

    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2


def _margin(r, self_, mirror=True):
    v = [c[2] - c[3] for c in r["cands"] if (c[1] in r["self"]) == self_ and (mirror or c[0] == "as_is")]
    return max(v) if v else -1.0


def semantic_gate(live_sem):
    """Threshold = max(99.8th percentile of the benchmark's genuine VAL member margins, the largest
    margin of the live calibration half + 0.01), rounded up to 0.01; every rate below is measured on
    data the threshold did not see (benchmark TEST, the live report half, the copymints)."""
    from benchmark import copydet as cd

    b = cd.Bench()
    Q, R = cd.load("sscd_queries").astype(np.float32), cd.load("sscd_refs").astype(np.float32)
    RR = R @ R.T
    np.fill_diagonal(RR, -1)
    M = np.where(b.collection[None, :], Q @ R.T - RR.max(1)[None, :], -9.0)
    best = M.max(1)
    member_copy = (b.gt >= 0) & np.isin(b.gt, np.flatnonzero(b.collection))
    gen = (b.gt < 0) & np.isin(b.qdom, np.unique(b.qdom[member_copy]))
    neg_live = [r for r in live_sem if r["group"] in ("new_punk", "oos_onchain") or r["group"].startswith("copymint:")]
    calib = [_margin(r, False) for r in neg_live if _half(r["key"]) == 0]
    t = max(float(np.quantile(best[gen & (b.qsplit == "val")], 0.998)), max(calib) + 0.01)
    t = float(np.ceil(t * 100) / 100)
    te = b.qsplit == "test"
    report_half = [r for r in neg_live if _half(r["key"]) == 1 and not r["group"].startswith("copymint")]
    out = {
        "threshold": t,
        "rule": "max(q99.8 benchmark genuine VAL, live calibration-half max + 0.01), rounded up",
        "benchmark_test": {
            "member_copies_flagged": stats.rate(best[te & member_copy] >= t),
            "member_copies_right_member": stats.rate([M[i, b.gt[i]] >= t for i in np.flatnonzero(te & member_copy)]),
            "genuine_flagged": stats.rate(best[te & gen] >= t),
        },
        "live_report_half_genuine_flagged": stats.rate([_margin(r, False) >= t for r in report_half]),
        "copies": {},
    }
    for g in sorted({r["group"] for r in live_sem}):
        rs = [r for r in live_sem if r["group"] == g]
        if g == "oos_huggingnft":  # the huggingnft CrypToadz are the indexed tokens themselves (id unknown)
            out["copies"][g] = stats.rate([_margin(r, False) >= t for r in rs])
        elif g != "new_punk":
            out["copies"][g + " (own token)"] = stats.rate([_margin(r, True) >= t for r in rs])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-live", type=int, default=None)
    ap.add_argument("--ship", action="store_true", help="write the winner into copy_engine.json + policies")
    args = ap.parse_args()
    b, rows = bench_part()
    bud = budgets(b, rows)
    index = PgCopyIndex()
    live_cache = MODEL_DIR / "benchmarks" / "member_rule_live.json"
    live = json.loads(live_cache.read_text()) if live_cache.exists() else {}
    for part, build in (("genuine", genuine_items), ("copymint", copymint_items)):
        if part not in live:
            live[part] = live_rows(build(), index, args.limit_live)
            live_cache.write_text(json.dumps(live))
    sem_cache = MODEL_DIR / "benchmarks" / "member_semantic_live.json"
    if not sem_cache.exists():
        sem_cache.write_text(json.dumps(semantic_live(genuine_items() + copymint_items(), index)))
    live_sem = json.loads(sem_cache.read_text())

    res = {}
    for c in map(cname, CANDIDATES):
        k = bud[c]["budget"]
        res[c] = {
            **bud[c],
            "benchmark_test": bench_metrics(b, rows, c, k),
            "live_genuine": live_metrics(live["genuine"], c, k),
            "copymint": live_metrics(live["copymint"], c, k),
        }
        bt, lg, cm = res[c]["benchmark_test"], res[c]["live_genuine"], res[c]["copymint"]
        # huggingnft images are indexed tokens with unknown ids: a match may be the token itself
        false_live = sum(g["other_accepted"]["k"] for name, g in lg.items() if name != "oos_huggingnft")
        own = sum(g["self_accepted"]["k"] for g in cm.values()) + lg["oos_onchain"]["self_accepted"]["k"]
        res[c]["genuine_false_live"] = false_live
        res[c]["genuine_false_total"] = bt["genuine_falsely_accepted"]["k"] + false_live
        res[c]["copies_accepted_total"] = bt["member_copies_accepted"]["k"] + own
        print(
            f"{c:20s} budget {k:4d} | bench copies {bt['member_copies_accepted']['k']}/"
            f"{bt['member_copies_accepted']['n']} genuine false {bt['genuine_falsely_accepted']['k']}"
            f" | live false {false_live} | copies accepted {res[c]['copies_accepted_total']}",
            flush=True,
        )
    # The Phase 4 rule matched 45 genuine live uploads to a sibling (subtle traits under level 48, see
    # docs/models.md) and accepted ~no real copy, so the pixel rule's job is to be safe: fewest genuine
    # false acceptances, none on the benchmark; ties -> more copies accepted.
    safe = [c for c in res if res[c]["benchmark_test"]["genuine_falsely_accepted"]["k"] == 0]
    winner = min(safe, key=lambda c: (res[c]["genuine_false_total"], -res[c]["copies_accepted_total"]))
    sem = semantic_gate(live_sem)
    print("pixel rule winner:", winner, "| copy_member:", json.dumps(sem, indent=1))
    OUT.write_text(
        json.dumps(
            {
                "generated": time.strftime("%Y-%m-%d %H:%M"),
                "baseline": "nearest@48",
                "pixel_rule_winner": winner,
                "pixel_rule_candidates": res,
                "copy_member": sem,
            },
            indent=1,
        )
    )
    if args.ship:
        ship(winner, res[winner]["budget"], sem)


ENGINE_VERSION = "copy-engine-1.1.0"
POLICY_VERSION = "5.1.0"
COPY_MEMBER_LABEL = "SSCD margin vs collection members (closer than the token's own nearest sibling; REVIEW-only)"


def ship(winner, budget, sem):
    from config import BACKEND_DIR

    rule, level = winner.split("@")
    eng_file = MODEL_DIR / "copy_engine.json"
    eng = json.loads(eng_file.read_text())
    eng["version"] = ENGINE_VERSION
    eng["member_rule"] = {
        "resample": rule.split("+")[0],
        "dihedral": "+dihedral" in rule,
        "level": int(level),
        "benchmark": "model/benchmarks/member_rule.json",
    }
    eng["member_max_diff_px"] = int(budget)
    eng["member_semantic"] = {"k": SEM_K, "mirror": True, "threshold": sem["threshold"]}
    eng_file.write_text(json.dumps(eng, indent=2))
    for name in ("policy.json", "policy.permissive.json"):
        f = BACKEND_DIR / name
        pol = json.loads(f.read_text())
        pol["version"] = POLICY_VERSION if name == "policy.json" else POLICY_VERSION + "-permissive"
        pol["weights"]["copy_member"] = 0.0  # acts through its REVIEW status, like provenance
        pol["checks"]["copy_member"] = {
            "label": COPY_MEMBER_LABEL,
            "review": sem["threshold"],
            "fail": round(sem["threshold"] + 0.2, 4),
            "max_status": "REVIEW",
        }
        pol.setdefault("calibration", {})["copy_member"] = {"source": "benchmark/member_rule.py", **sem}
        f.write_text(json.dumps(pol, indent=2))
    print(f"shipped {ENGINE_VERSION} ({winner}, budget {budget}) + policy {POLICY_VERSION}")


if __name__ == "__main__":
    main()
