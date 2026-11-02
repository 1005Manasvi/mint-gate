"""Create the example uploads shown on the website (backend/demo_samples/).

Every genuine example comes from held-out test data (never trained on, not in
the protected index); copies are derived from protected works. Each example
is checked against the live pipeline so its label matches what the app shows.

Run from backend/:   python -m training.make_demo_samples
"""

import io
import random

from config import DEMO_DIR
from detection.manipulations import copy_move, near_duplicate, splice
from detection.pipeline import MintGatePipeline
from detection.preprocessing import load_path
from training.realworld_data import load_manifest, open_image
from training.split import load_split, paths

# file name -> (expected decisions, candidate generator)
WANT = {
    "01_genuine_painting.png": ({"PASS"}, "human_art"),
    "02_genuine_photo.png": ({"PASS"}, "real_photo"),
    "03_genuine_nft_collection_art.png": ({"PASS"}, "nft_collection"),
    "04_ai_generated_art.png": ({"FAIL"}, "ai_modern"),
    "05_flux_unseen_generator.png": ({"FAIL", "REVIEW"}, "unseen"),
    "06_copy_of_protected_painting.png": ({"FAIL", "REVIEW"}, "painting_copy"),
    "07_exact_copy_of_minted_nft.png": ({"FAIL"}, "nft_duplicate"),
    "08_copy_move_forgery.png": ({"FAIL"}, "copy_move"),
    "09_spliced_forgery.png": ({"FAIL"}, "splice"),
}


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    rng = random.Random(2026)
    pipe = MintGatePipeline()
    man = load_manifest()
    test = lambda g: [r for r in man if r["split"] == "test" and r["generator"] in g]
    used = {
        r["file"].removeprefix("local_authentic_").removesuffix(".jpg") for r in man if r["generator"] == "cryptopunk"
    }
    pools = {
        "human_art": lambda: open_image(rng.choice(test({"human_art"}))),
        "real_photo": lambda: open_image(rng.choice(test({"real_photo"}))),
        "punk": lambda: load_path(
            rng.choice([p for p in paths(load_split(), "authentic", "test") if p.stem not in used])
        ),
        "ai_art": lambda: open_image(rng.choice(test({"mixed_art_generators"}))),
        "unseen": lambda: open_image(rng.choice([r for r in man if r["split"] == "test_unseen"])),
        "nft_collection": lambda: open_image(
            rng.choice([r for r in man if r["split"] == "test" and r["generator"].startswith("nft_")])
        ),
        "ai_modern": lambda: open_image(rng.choice(test({"dalle3", "midjourney", "sd3", "sdxl"}))),
        "painting_copy": lambda: near_duplicate(
            load_path(
                pipe.index.path(
                    rng.choice(
                        [
                            i
                            for i, f in enumerate(pipe.index.files)
                            if f.startswith("art/") and not f.startswith("art/nft_")
                        ]
                    )
                )
            ),
            rng,
        ),
        "nft_duplicate": lambda: load_path(
            pipe.index.path(rng.choice([i for i, f in enumerate(pipe.index.files) if "/" not in f]))
        ),
        "copy_move": lambda: copy_move(open_image(rng.choice(test({"human_art"}))), rng),
        "splice": lambda: splice(
            open_image(rng.choice(test({"human_art"}))), open_image(rng.choice(test({"human_art", "real_photo"}))), rng
        ),
    }
    DEMO_DIR.mkdir(exist_ok=True)
    for old in DEMO_DIR.glob("*.png"):
        old.unlink()
    for name, (expected, pool) in WANT.items():
        for attempt in range(40):
            img = pools[pool]()
            report, _ = pipe.analyse(png(img))
            if report["decision"] in expected:
                img.save(DEMO_DIR / name)
                print(f"{name:40s} {report['decision']:6s} score {report['score']:3d} (attempt {attempt + 1})")
                break
        else:
            print(f"{name}: no candidate matched {expected}")


if __name__ == "__main__":
    main()
