"""Deterministic 80/10/10 train/val/test split, shared by every script.

The test split of the *originals* doubles as the pool of "new, never minted"
artworks: those images are excluded from the reference index so they can be
used to demonstrate a genuine PASS.
"""
import json
import random

from config import AI_DIR, ORIGINALS_DIR, SPLIT_FILE

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
SEED = 42


def _split(files, rng):
    files = sorted(files)
    rng.shuffle(files)
    n = len(files)
    n_test, n_val = n // 10, n // 10
    return {
        "test": files[:n_test],
        "val": files[n_test:n_test + n_val],
        "train": files[n_test + n_val:],
    }


def make_split():
    rng = random.Random(SEED)
    split = {}
    for name, folder in (("authentic", ORIGINALS_DIR), ("ai", AI_DIR)):
        files = [f for f in folder.iterdir() if f.suffix.lower() in IMG_EXT]
        split[name] = _split([f.name for f in files], rng)
    SPLIT_FILE.write_text(json.dumps(split))
    return split


def load_split():
    if not SPLIT_FILE.exists():
        return make_split()
    return json.loads(SPLIT_FILE.read_text())


def paths(split, cls, part):
    folder = ORIGINALS_DIR if cls == "authentic" else AI_DIR
    return [folder / f for f in split[cls][part]]


if __name__ == "__main__":
    s = make_split()
    for cls, parts in s.items():
        print(cls, {k: len(v) for k, v in parts.items()})
