"""Phase 5b: the full pipeline on REAL copymint tokens (training/copymint_data.py), per collection.

For every copy (the original token file, as a copyminter would upload it): decision, the checks that
fired, and whether the report names the right original (`copy_exact.nearest` or the top similar NFT
is the indexed original). Misses are explained from the pair itself:
  original_not_indexed   the copied token is not in reference_items (held-out punk, index cap)
  pdq_far                the original is > pdq_max_bits away even over the 8 dihedral hashes
  member_pixel_rule      PDQ found the original, but the collection-member pixel-identity check
                         (128 px NEAREST, <= member_max_diff_px) rejected it; split by whether the
                         un-mirrored copy would pass it (mirror) or not even that (resampling)
  other                  anything else (reported with the values)

Output: model/copymint_eval.json (Wilson 95 % CIs, training/stats.py); resumable per image.
Run from backend/:   python -m training.copymint_eval [--only rrbayc,...] [--tag name] [--copy-params file]
"""

import argparse
import hashlib
import json
import time

import numpy as np

from config import MODEL_DIR
from detection import fingerprints as fp
from detection.copy_index import resolve_image
from detection.pipeline import MintGatePipeline
from detection.preprocessing import load_path, prepare
from training import stats
from training.copymint_data import COLLECTIONS, IMG_DIR, MANIFEST, MAPPING_MAX_MAD, unmirror

DECISIONS = ("PASS", "REVIEW", "FAIL")


def diagnose(rec, report, params):
    """Why a copy was not blocked as a copy of its original (only called for copies that were not)."""
    if not rec["original_ref"]:
        return "original_not_indexed", {}
    q = prepare((IMG_DIR / rec["file"]).read_bytes()).rgb
    ref = load_path(resolve_image(rec["original_source"], rec["original_uri"]))
    bits = int(fp.pdq_distances(fp.pdq_dihedral(q), np.frombuffer(fp.pdq(ref)[0], np.uint8)[None])[0])
    r64 = fp.rgb_small(ref)
    px_as_is = fp.differing_pixels(fp.rgb_small(q), r64)
    px_unmirrored = fp.differing_pixels(fp.rgb_small(unmirror(q, rec["transform"])), r64)
    info = {"pdq_bits": bits, "px_as_is": px_as_is, "px_unmirrored": px_unmirrored}
    if bits > params["pdq_max_bits"]:
        return "pdq_far", info
    budget = params.get("member_max_diff_px", 0)
    if px_as_is > budget:
        return ("member_pixel_rule:mirror" if px_unmirrored <= budget else "member_pixel_rule:resampling"), info
    return "other", info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(COLLECTIONS))
    ap.add_argument("--tag", default="", help="suffix for the output file (e.g. a candidate rule)")
    ap.add_argument("--copy-params", help="another copy_engine.json (e.g. the Phase 4 one from git) for a before/after")
    args = ap.parse_args()
    records = [r for r in json.loads(MANIFEST.read_text()) if r["collection"] in args.only.split(",")]
    pipe = MintGatePipeline()
    if args.copy_params:
        pipe.copy.p = json.loads(open(args.copy_params).read())
        pipe.copy.regions = pipe.copy.p.get("regions") if pipe.copy.profile == "gpu" else pipe.copy.p.get("regions_cpu")
    params = pipe.copy.p
    pkey = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    run_key = f"{pipe.policy.sha256}|{getattr(pipe.ai, 'version', None)}|{pipe.copy.version}|{pkey}"
    progress = MODEL_DIR / f"copymint_eval_progress{args.tag}.json"
    saved = json.loads(progress.read_text()) if progress.exists() else {}
    done = saved.get("rows", {}) if saved.get("key") == run_key else {}
    t0 = time.time()
    for i, rec in enumerate(records):
        if rec["file"] in done:
            continue
        report, _ = pipe.analyse((IMG_DIR / rec["file"]).read_bytes(), rec["file"])
        ch = report["checks"]
        named = {ch["copy_exact"].get("nearest"), (report["similar_nfts"] or [{}])[0].get("reference")}
        row = {
            "decision": report["decision"],
            "flags": sorted(f"{n}:{c['status']}" for n, c in ch.items() if c["status"] in ("REVIEW", "FAIL")),
            "copy_exact": ch["copy_exact"]["value"],
            "copy_exact_nearest": ch["copy_exact"].get("nearest"),
            "copy_semantic": ch["copy_semantic"]["value"],
            "copy_semantic_nearest": ch["copy_semantic"].get("nearest"),
            "top_similar": [m["reference"] for m in report["similar_nfts"][:3]],
            "ai_detector": ch["ai_detector"]["value"],
            "right_original_named": bool(rec["original_ref"]) and rec["original_ref"] in named,
        }
        blocked_as_copy = any(f.startswith(("copy_exact:", "copy_semantic:")) for f in row["flags"])
        if not (blocked_as_copy and row["right_original_named"]):
            row["miss_cause"], row["miss_info"] = diagnose(rec, report, params)
        done[rec["file"]] = row
        if i % 10 == 0:
            progress.write_text(json.dumps({"key": run_key, "rows": done}))
            print(f"{i + 1}/{len(records)} ({time.time() - t0:.0f}s)", flush=True)
    progress.write_text(json.dumps({"key": run_key, "rows": done}))

    def summarise(rs):
        rows = [done[r["file"]] for r in rs]
        dec = [x["decision"] for x in rows]
        copy_blocked = [any(f.startswith(("copy_exact:", "copy_semantic:")) for f in x["flags"]) for x in rows]
        causes = {}
        for x in rows:
            if "miss_cause" in x:
                causes[x["miss_cause"]] = causes.get(x["miss_cause"], 0) + 1
        return {
            "n": len(rows),
            **{d: stats.rate([x == d for x in dec]) for d in DECISIONS},
            "stopped (REVIEW or FAIL)": stats.rate([x != "PASS" for x in dec]),
            "stopped_by_copy_checks": stats.rate(copy_blocked),
            "right_original_named": stats.rate([x["right_original_named"] for x in rows]),
            "not_blocked_as_copy_of_right_original": dict(sorted(causes.items())),
            "check_flags": dict(
                sorted({f: sum(f in x["flags"] for x in rows) for x in rows for f in x["flags"]}.items())
            ),
        }

    per = {}
    for key in args.only.split(","):
        rs = [r for r in records if r["collection"] == key]
        idx = [r for r in rs if r["original_ref"] and (r.get("mapping_mad") or 0) <= MAPPING_MAX_MAD]
        spec = COLLECTIONS[key]
        per[key] = {
            "name": spec["name"],
            "chain": spec["chain"],
            "address": spec["address"],
            "copies": spec["copies"],
            "transform": spec["transform"],
            "all_sampled": summarise(rs),
            "original_indexed_and_pair_verified": summarise(idx),
        }
        a, b = per[key]["all_sampled"], per[key]["original_indexed_and_pair_verified"]
        print(
            f"{key:14s} all n={a['n']:3d} stopped {stats.fmt_rate(a['stopped (REVIEW or FAIL)'])}"
            f" | indexed n={b['n']:3d} FAIL {stats.fmt_rate(b['FAIL'])} right original"
            f" {stats.fmt_rate(b['right_original_named'])} misses {b['not_blocked_as_copy_of_right_original']}",
            flush=True,
        )
    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "policy_version": pipe.policy.version,
        "copy_engine": pipe.copy.version,
        "copy_params_sha": pkey,
        "member_max_diff_px": params.get("member_max_diff_px"),
        "member_dihedral": params.get("member_dihedral", False),
        "per_collection": per,
    }
    (MODEL_DIR / f"copymint_eval{args.tag}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
