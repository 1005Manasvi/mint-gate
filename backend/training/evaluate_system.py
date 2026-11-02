"""End-to-end evaluation of the Mint-Gate decision (pipeline + policy) on held-out data.

Genuine uploads (should PASS):
  genuine_human_art     paintings / drawings from the real-world test split
  genuine_photo         real photographs (GenImage test split)
  genuine_new_punk      pixel-art NFTs never minted and never used in training
  genuine_nft_collection  art from real NFT collections (BAYC, Azuki, ...) test split
Forged / plagiarised uploads (should NOT pass):
  ai_modern_generators  DALL-E 3, Midjourney, SD3, SDXL test split
  ai_art_generators     AI art (Ideogram, Civitai, ...) test split
  ai_older_generators   GenImage generators test split (SD 1.5, ADM, GLIDE, ...)
  ai_unseen_flux        Flux - a generator NEVER seen in training
  exact_duplicate       a protected work (NFT or painting) re-uploaded
  edited_copy_nft       minted NFT cropped / recoloured / rotated / text + JPEG
  edited_copy_painting  protected museum/WikiArt painting, same edits
  copy_move_human_art   clone forgery of genuine human art
  splice_human_art      genuine human art with a region pasted from another image

Run from backend/:   python -m training.evaluate_system --n 50
"""

import argparse
import io
import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import MODEL_DIR, PROJECT_DIR
from detection.manipulations import copy_move, near_duplicate, splice
from detection.pipeline import MintGatePipeline
from detection.preprocessing import load_path
from training.realworld_data import load_manifest, open_image
from training.split import load_split, paths

DECISIONS = ("PASS", "REVIEW", "FAIL")
GENUINE = ("genuine_human_art", "genuine_photo", "genuine_new_punk", "genuine_nft_collection")


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()
    rng = random.Random(99)
    pipe = MintGatePipeline()
    man = load_manifest()

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
    minted = range(len(pipe.index))
    art_refs = [i for i, f in enumerate(pipe.index.files) if f.startswith("art/") and not f.startswith("art/nft_")]
    nft_refs = [
        i for i, f in enumerate(pipe.index.files) if f.startswith("art/nft_") or not f.startswith(("art/", "minted/"))
    ]
    n = args.n

    gens = {
        "genuine_human_art": lambda: open_image(rng.choice(human_art)),
        "genuine_photo": lambda: open_image(rng.choice(photos)),
        "genuine_new_punk": lambda: load_path(rng.choice(new_punks)),
        "genuine_nft_collection": lambda: open_image(rng.choice(nft_art)),
        "ai_modern_generators": lambda: open_image(rng.choice(ai_modern)),
        "ai_art_generators": lambda: open_image(rng.choice(ai_art)),
        "ai_older_generators": lambda: open_image(rng.choice(ai_photo)),
        "ai_unseen_flux": lambda: open_image(rng.choice(unseen)),
        "exact_duplicate": lambda: load_path(pipe.index.path(rng.choice(minted))),
        "edited_copy_nft": lambda: near_duplicate(load_path(pipe.index.path(rng.choice(nft_refs))), rng),
        "edited_copy_painting": lambda: near_duplicate(load_path(pipe.index.path(rng.choice(art_refs))), rng),
        "copy_move_human_art": lambda: copy_move(open_image(rng.choice(human_art)), rng),
        "splice_human_art": lambda: splice(
            open_image(rng.choice(human_art)), open_image(rng.choice(human_art + photos)), rng
        ),
    }

    results = {}
    for cat, gen in gens.items():
        counts = {d: 0 for d in DECISIONS}
        scores = []
        for _ in range(n):
            report, _ = pipe.analyse(png(gen()))
            counts[report["decision"]] += 1
            scores.append(report["score"])
        results[cat] = {d: round(counts[d] / n, 3) for d in DECISIONS}
        results[cat]["mean_score"] = round(sum(scores) / n, 1)
        print(
            f"{cat:22s} "
            + "  ".join(f"{d} {results[cat][d]:6.1%}" for d in DECISIONS)
            + f"  mean score {results[cat]['mean_score']}",
            flush=True,
        )

    forged = [c for c in gens if c not in GENUINE]
    summary = {
        "n_per_category": n,
        "policy_version": pipe.policy.version,
        "genuine_pass_rate": round(sum(results[c]["PASS"] for c in GENUINE) / len(GENUINE), 3),
        "genuine_wrongly_failed": round(sum(results[c]["FAIL"] for c in GENUINE) / len(GENUINE), 3),
        "forgery_stopped_rate (REVIEW or FAIL)": round(sum(1 - results[c]["PASS"] for c in forged) / len(forged), 3),
        "forgery_auto_failed_rate": round(sum(results[c]["FAIL"] for c in forged) / len(forged), 3),
        "per_category": results,
    }
    print(json.dumps(summary, indent=2))
    (MODEL_DIR / "system_eval.json").write_text(json.dumps(summary, indent=2))

    cats = list(gens)
    fig, ax = plt.subplots(figsize=(11, 5))
    left = [0.0] * len(cats)
    colors = {"PASS": "#2a9d8f", "REVIEW": "#e9c46a", "FAIL": "#e76f51"}
    for d in DECISIONS:
        vals = [results[c][d] * 100 for c in cats]
        ax.barh(cats, vals, left=left, color=colors[d], label=d)
        left = [a + b for a, b in zip(left, vals)]
    ax.invert_yaxis()
    ax.set_xlabel("% of uploads")
    ax.set_title(f"Mint-Gate decisions on held-out data (n={n} per category, policy {pipe.policy.version})")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(Path(PROJECT_DIR) / "assets" / "system_evaluation.png", dpi=130)


if __name__ == "__main__":
    main()
