"""Shared access to the real-world dataset produced by training/fetch_realworld.py."""

import io
import json
import random

from PIL import Image
from torch.utils.data import Dataset

from detection.manipulations import random_forgery
from training.fetch_realworld import IMG_DIR, MANIFEST

AUTH, FORGED = 0, 1


def load_manifest():
    return json.loads(MANIFEST.read_text())


def records(split_name, label=None):
    return [r for r in load_manifest() if r["split"] == split_name and (label is None or r["label"] == label)]


def open_image(rec) -> Image.Image:
    return Image.open(IMG_DIR / rec["file"]).convert("RGB")


def rejpeg(img, rng, lo=88, hi=96):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=rng.randint(lo, hi))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


class ForgeryDataset(Dataset):
    """authentic -> 0;  AI-generated -> 1;  authentic + synthetic copy-move/splice -> 1.

    Manipulated items are re-encoded as JPEG like every other image, so the
    manipulation - not a missing JPEG trace - is what distinguishes them.
    For non-training splits the RNG is seeded by index (reproducible metrics).
    """

    def __init__(self, split_names, transform, train, manip_fraction=0.35, seed=0):
        recs = [r for s in split_names for r in records(s)]
        auth = [r for r in recs if r["label"] == "authentic"]
        rng = random.Random(seed)
        manip = rng.sample(auth, int(len(auth) * manip_fraction)) if manip_fraction else []
        self.items = (
            [(r, AUTH, False) for r in auth]
            + [(r, FORGED, False) for r in recs if r["label"] == "ai"]
            + [(r, FORGED, True) for r in manip]
        )
        self.kind = [("manipulated" if m else r["generator"]) for r, _, m in self.items]
        self.source = [r["source"] for r, _, _ in self.items]
        self.donors = recs
        self.transform, self.train = transform, train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        rec, label, manip = self.items[i]
        img = open_image(rec)
        if manip:
            rng = random.Random() if self.train else random.Random(i)
            img = rejpeg(random_forgery(img, open_image(rng.choice(self.donors)), rng), rng)
        return self.transform(img), label
