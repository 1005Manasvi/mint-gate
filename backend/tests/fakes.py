"""Stand-ins for the learned models, shared by the pytest suite and the browser E2E stack
(e2e/stack.py). Importing this module has no side effects (conftest.py pins the environment)."""

import threading
from pathlib import Path

import numpy as np
from PIL import Image

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_image(name) -> Image.Image:
    with Image.open(FIXTURES / name) as im:
        return im.convert("RGB")


# ---- tiny stand-ins for the learned models -------------------------------------------


class FakeResNet:
    version = "fake-resnet"

    def __init__(self):
        self.value = 0.05

    def predict(self, img):
        return self.value


class FakeAIDetector:
    version = "fake-ai"
    metrics = {}

    def __init__(self):
        self.value = 0.05

    def predict(self, img, clip_emb=None):
        return {"spectral": self.value, "clip_probe": self.value, "probability": self.value}


class FakeClip:
    """Deterministic 512-d 'embedding' (CLIP's size, so it fits reference_items.embedding):
    a mean-centred, L2-normalised thumbnail. Identical pixels give similarity 1.0."""

    def encode_one(self, img):
        v = np.asarray(img.convert("RGB").resize((16, 11), Image.BILINEAR), dtype=np.float32).ravel()[:512]
        v = np.pad(v, (0, 512 - len(v)))
        v -= v.mean()
        return (v / (np.linalg.norm(v) + 1e-8)).astype(np.float32)


def tiny_index(clip):
    """Two references: a generative-collection member and a protected painting."""
    from detection import reference_index
    from detection.hashing import phash_int

    reference_index.ORIGINALS_DIR = FIXTURES  # "<file>"     seed collection
    reference_index.REFERENCE_ART_DIR = FIXTURES  # "art/<file>" protected artworks
    idx = reference_index.ReferenceIndex.__new__(reference_index.ReferenceIndex)
    idx.files = ["punk.png", "art/painting.png"]
    imgs = [fixture_image("punk.png"), fixture_image("painting.png")]
    idx.phash = np.array([phash_int(im) for im in imgs], dtype=np.uint64)
    idx.clip = np.stack([clip.encode_one(im) for im in imgs])
    idx.nn_sim = np.array([0.99, 0.85], dtype=np.float32)  # dense collection / distinct painting
    return idx


def make_pipeline():
    from detection.pipeline import MintGatePipeline
    from detection.policy import Policy

    p = MintGatePipeline.__new__(MintGatePipeline)
    p.policy = Policy()
    p.resnet = FakeResNet()
    p.ai = FakeAIDetector()
    p.clip = FakeClip()
    p.index = tiny_index(p.clip)
    p._lock = threading.Lock()
    return p
