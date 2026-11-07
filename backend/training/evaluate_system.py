"""End-to-end evaluation of the Mint-Gate decision (pipeline + policy), with 95 % CIs.

Every category draws its images WITHOUT replacement (n = min(--n, pool size), the pool size is
reported), so no image is counted twice and the Wilson intervals (training/stats.py) are honest.

Genuine uploads (should PASS), in-source (held-out test split of the training sources):
  genuine_human_art, genuine_photo, genuine_new_punk, genuine_nft_collection
Genuine uploads, OUT-OF-SOURCE (training/oos_data.py, "report" half + "select" half; no source
of these was trained on):
  oos_nft_collection   7 HF collections + 40 on-chain collections never trained on
  oos_pixel_art        on-chain pixel-art collections (Moonbirds, Nouns, ...)
  oos_painting         Rijksmuseum paintings
  oos_photo            COCO + DOCCI photographs
Forged / plagiarised uploads (should NOT pass):
  ai_modern_generators  DALL-E 3, Midjourney, SD3, SDXL test split
  ai_art_generators     AI art (Ideogram, Civitai, ...) test split
  ai_older_generators   GenImage generators test split (SD 1.5, ADM, GLIDE, ...)
  ai_unseen_flux        Flux (Rapidata): held out of training
  oos_ai_2025           out-of-source 2025-26 generators (OpenFake: z-image, Flux.2, GPT-image-1.5,
                        Midjourney 7, Illustrious, Recraft, ...) + Flux.1-dev from another publisher
  exact_duplicate, edited_copy_nft, edited_copy_painting, exact_onchain_nft, edited_onchain_nft,
  copy_move_human_art, splice_human_art   (as in policy 4.0.0)

Output: model/system_eval.json (+ per-domain false-alarm table) and assets/system_evaluation.png
Run from backend/:   python -m training.evaluate_system --n 300
"""

import argparse
import io
import json
import random
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
from sqlalchemy import text

import db
from config import INDEX_FILES, MODEL_DIR, PROJECT_DIR
from detection.copy_index import resolve_image
from detection.manipulations import copy_move, near_duplicate, splice
from detection.pipeline import MintGatePipeline
from detection.preprocessing import load_path
from ingest.seed import is_collection_member, seed_path
from training import stats
from training.oos_data import IMG_DIR as OOS_DIR
from training.oos_data import MANIFEST as OOS_MANIFEST
from training.realworld_data import load_manifest, open_image
from training.split import load_split, paths

DECISIONS = ("PASS", "REVIEW", "FAIL")
ONCHAIN_DUP_BITS = 10
GENUINE = (
    "genuine_human_art",
    "genuine_photo",
    "genuine_new_punk",
    "genuine_nft_collection",
    "oos_nft_collection",
    "oos_pixel_art",
    "oos_painting",
    "oos_photo",
)
DOMAIN = {  # per-domain false-alarm table
    "genuine_nft_collection": "collection NFTs",
    "oos_nft_collection": "collection NFTs",
    "genuine_new_punk": "pixel art",
    "oos_pixel_art": "pixel art",
    "genuine_human_art": "paintings",
    "oos_painting": "paintings",
    "genuine_photo": "photos",
    "oos_photo": "photos",
}
V1_COMPARABLE = (
    "ai_modern_generators",
    "ai_art_generators",
    "ai_older_generators",
    "ai_unseen_flux",
    "exact_duplicate",
    "edited_copy_nft",
    "edited_copy_painting",
    "copy_move_human_art",
    "splice_human_art",
)


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--only", default="", help="comma-separated categories (default: all)")
    args = ap.parse_args()
    rng = random.Random(99)
    pipe = MintGatePipeline()
    man = load_manifest()
    oos = json.loads(OOS_MANIFEST.read_text())

    def pick(pred):
        return [r for r in man if pred(r)]

    human_art = pick(lambda r: r["split"] == "test" and r["generator"] == "human_art")
    photos = pick(lambda r: r["split"] == "test" and r["generator"] == "real_photo")
    ai_art = pick(lambda r: r["split"] == "test" and r["generator"] in ("mixed_art_generators", "local_ai_art"))
    ai_photo = pick(lambda r: r["split"] == "test" and r["source"] == "genimage" and r["label"] == "ai")
    ai_modern = pick(lambda r: r["split"] == "test" and r["generator"] in ("dalle3", "midjourney", "sd3", "sdxl"))
    nft_art = pick(lambda r: r["split"] == "test" and r["generator"].startswith("nft_"))
    unseen = pick(lambda r: r["split"] == "test_unseen")
    used_punks = {
        r["file"].removeprefix("local_authentic_").removesuffix(".jpg") for r in man if r["generator"] == "cryptopunk"
    }
    new_punks = [p for p in paths(load_split(), "authentic", "test") if p.stem not in used_punks]
    oos_by = lambda pred: [r for r in oos if pred(r)]  # noqa: E731
    seed = [f for f in json.loads(INDEX_FILES.read_text()) if not f.startswith("minted/")]
    art_refs = [f for f in seed if f.startswith("art/") and not is_collection_member(f)]
    nft_refs = [f for f in seed if is_collection_member(f)]

    def ref(name):
        return load_path(seed_path("seed/" + name))

    with db.session() as s:
        onchain = [
            r[0]
            for r in s.execute(
                text(
                    "SELECT image_uri FROM reference_items WHERE source = 'alchemy' AND status = 'ready' "
                    "ORDER BY md5(image_uri) LIMIT 3000"
                )
            )
        ]
    oos_img = lambda r: Image.open(OOS_DIR / r["file"]).convert("RGB")  # noqa: E731

    # (pool, loader) per category; generated forgeries draw distinct source images too
    pools = {
        "genuine_human_art": (human_art, open_image),
        "genuine_photo": (photos, open_image),
        "genuine_new_punk": (new_punks, load_path),
        "genuine_nft_collection": (nft_art, open_image),
        "oos_nft_collection": (oos_by(lambda r: r["domain"] == "nft_collection"), oos_img),
        "oos_pixel_art": (oos_by(lambda r: r["domain"] == "pixel_art"), oos_img),
        "oos_painting": (oos_by(lambda r: r["domain"] == "painting"), oos_img),
        "oos_photo": (oos_by(lambda r: r["domain"] == "photo"), oos_img),
        "ai_modern_generators": (ai_modern, open_image),
        "ai_art_generators": (ai_art, open_image),
        "ai_older_generators": (ai_photo, open_image),
        "ai_unseen_flux": (unseen, open_image),
        "oos_ai_2025": (oos_by(lambda r: r["label"] == "ai"), oos_img),
        "exact_duplicate": (seed, ref),
        "edited_copy_nft": (nft_refs, lambda f: near_duplicate(ref(f), rng)),
        "edited_copy_painting": (art_refs, lambda f: near_duplicate(ref(f), rng)),
        "copy_move_human_art": (human_art, lambda r: copy_move(open_image(r), rng)),
        "splice_human_art": (
            human_art,
            lambda r: splice(open_image(r), open_image(rng.choice(human_art + photos)), rng),
        ),
    }
    if onchain:
        thumb = lambda u: load_path(resolve_image("alchemy", u))  # noqa: E731
        pools["exact_onchain_nft"] = (onchain, thumb)
        pools["edited_onchain_nft"] = (onchain, lambda u: near_duplicate(thumb(u), rng))
    only = [c for c in args.only.split(",") if c]
    if only:
        pools = {k: v for k, v in pools.items() if k in only}

    # resumable per category (a crash must not cost the finished ones); keyed by policy + models + n
    progress = MODEL_DIR / "system_eval_progress.json"
    run_key = f"{pipe.policy.sha256}|{getattr(pipe.ai, 'version', None)}|{pipe.copy.version}|{args.n}"
    saved = json.loads(progress.read_text()) if progress.exists() else {}
    results = saved.get("results", {}) if saved.get("key") == run_key else {}
    t0 = time.time()
    for cat, (pool, load) in pools.items():
        if cat in results:
            print(f"{cat:24s} (done earlier)", flush=True)
            continue
        items = random.Random(f"eval-{cat}").sample(pool, min(args.n, len(pool)))
        dec, scores, onchain_dup, flags = [], [], 0, {}
        for it in items:
            report, _ = pipe.analyse(png(load(it)))
            dec.append(report["decision"])
            scores.append(report["score"])
            for name, c in report["checks"].items():  # which checks drove the decision
                if c["status"] in ("REVIEW", "FAIL"):
                    flags[f"{name}:{c['status']}"] = flags.get(f"{name}:{c['status']}", 0) + 1
            ex = report["checks"]["copy_exact"]
            # The upload IS an indexed on-chain token (the HF collection datasets are real tokens):
            # blocking it is correct, so it is counted, not hidden.
            if report["decision"] != "PASS" and ex["value"] <= ONCHAIN_DUP_BITS and ":" in str(ex.get("nearest")):
                onchain_dup += 1
        n = len(dec)
        results[cat] = {
            "n": n,
            "pool": len(pool),
            **{d: stats.rate([x == d for x in dec]) for d in DECISIONS},
            "not_pass": stats.rate([x != "PASS" for x in dec]),
            "mean_score": round(sum(scores) / n, 1),
            "blocked_as_existing_onchain_token": stats.rate([True] * onchain_dup + [False] * (n - onchain_dup)),
            "check_flags": dict(sorted(flags.items())),
        }
        progress.write_text(json.dumps({"key": run_key, "results": results}))
        r = results[cat]
        print(
            f"{cat:24s} n={n:4d}  PASS {stats.fmt_rate(r['PASS'])}  FAIL {stats.fmt_rate(r['FAIL'])}"
            f"  ({time.time() - t0:.0f}s)",
            flush=True,
        )

    def pooled(cats, key):
        ks = [results[c][key]["k"] for c in cats if c in results]
        ns = [results[c][key]["n"] for c in cats if c in results]
        k, n = sum(ks), sum(ns)
        lo, hi = stats.wilson(k, n)
        return {"rate": round(k / n, 4) if n else None, "k": k, "n": n, "ci95": [round(lo, 4), round(hi, 4)]}

    genuine = [c for c in results if c in GENUINE]
    forged = [c for c in results if c not in GENUINE]
    by_domain = {}
    for c, dom in DOMAIN.items():
        if c in results:
            by_domain.setdefault(dom, []).append(c)
    summary = {
        "n_per_category": args.n,
        "sampling": "without replacement, n = min(n, pool)",
        "policy_version": pipe.policy.version,
        "copy_engine": pipe.copy.version,
        "ai_detector": getattr(pipe.ai, "version", None),
        "genuine_pass_rate": pooled(genuine, "PASS"),
        "genuine_wrongly_failed": pooled(genuine, "FAIL"),
        "forgery_stopped_rate (REVIEW or FAIL)": pooled(forged, "not_pass"),
        "forgery_auto_failed_rate": pooled(forged, "FAIL"),
        "v1_comparable_forgery_stopped": pooled([c for c in V1_COMPARABLE if c in results], "not_pass"),
        "false_alarm_by_domain": {
            dom: {"flagged (REVIEW or FAIL)": pooled(cs, "not_pass"), "failed": pooled(cs, "FAIL"), "categories": cs}
            for dom, cs in by_domain.items()
        },
        "per_category": results,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "per_category"}, indent=1))
    out = MODEL_DIR / ("system_eval.json" if not only else "system_eval_partial.json")
    out.write_text(json.dumps(summary, indent=2))

    cats = list(results)
    fig, ax = plt.subplots(figsize=(11, 0.38 * len(cats) + 1.5))
    left = [0.0] * len(cats)
    colors = {"PASS": "#2a9d8f", "REVIEW": "#e9c46a", "FAIL": "#e76f51"}
    for d in DECISIONS:
        vals = [results[c][d]["rate"] * 100 for c in cats]
        ax.barh(cats, vals, left=left, color=colors[d], label=d)
        left = [a + b for a, b in zip(left, vals)]
    ax.invert_yaxis()
    ax.set_xlabel("% of uploads")
    ax.set_title(f"Mint-Gate decisions (n <= {args.n} per category, policy {pipe.policy.version})")
    ax.legend(loc="lower right")
    fig.tight_layout()
    if not only:
        fig.savefig(Path(PROJECT_DIR) / "assets" / "system_evaluation.png", dpi=130)


if __name__ == "__main__":
    main()
