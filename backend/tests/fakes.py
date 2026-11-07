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


class FakeTruFor:
    """TruFor stand-in (REVIEW-only `manipulation` check): a flat map at `value`."""

    version = "fake-trufor"

    def __init__(self):
        self.value = 0.05

    def analyse(self, img):
        w, h = min(img.width, 64), min(img.height, 64)
        m = np.full((h, w), self.value, np.float32)
        return {"score": self.value, "map": m, "conf": np.ones_like(m), "size": (w, h), "ms": 0}


class FakeAIDetector:
    version = "fake-ai"
    metrics = {}

    def __init__(self):
        self.value = 0.05

    def predict(self, img, clip_emb=None):
        return {"probability": self.value, "probes": {"fake": self.value}}


class FakeClip:
    """Deterministic 512-d 'embedding' (CLIP's size, so it fits reference_items.embedding):
    a mean-centred, L2-normalised thumbnail. Identical pixels give similarity 1.0."""

    def encode_one(self, img):
        v = np.asarray(img.convert("RGB").resize((16, 11), Image.BILINEAR), dtype=np.float32).ravel()[:512]
        v = np.pad(v, (0, 512 - len(v)))
        v -= v.mean()
        return (v / (np.linalg.norm(v) + 1e-8)).astype(np.float32)


class FakeSSCD:
    """Stand-in for SSCD with the same contract (512-d, L2-normalised) built from a thumbnail."""

    name, dim = "fake-sscd", 512

    def encode(self, images, size=None):
        return np.stack([FakeClip().encode_one(im) for im in images]) if images else np.zeros((0, 512), np.float32)

    def encode_one(self, img):
        return self.encode([img])[0]


FAKE_COPY_PARAMS = {
    "version": "fake-copy-engine",
    "dino": None,
    "sscd": {"alpha": 1.0, "beta": 0.0},
    "fusion": {"w": 1.0, "mode": "sum"},
    "regions": None,
    "lightglue": False,
    "retrieve_k": 50,
    "crop_k": 5,
    "verify_k": 10,
    "pdq_max_bits": 31,
    "pdq_min_quality": 50,
    "evidence_min_inliers": 15,
    "member_exact_min_sscd": 0.98,
}


def copy_engine():
    """Real PDQ + scoring code, fake SSCD, no DINOv2 / LightGlue (no 100 MB weights in CI)."""
    from detection.copy_engine import CopyEngine

    bg = {"sscd": np.zeros((4, 512), np.float32)}
    return CopyEngine(FAKE_COPY_PARAMS, models=(FakeSSCD(), None, None, bg))


def tiny_copy_index(engine):
    """Two references: a generative-collection member and a protected painting."""
    from detection.copy_index import MemoryCopyIndex

    idx = MemoryCopyIndex()
    for name, member in (("punk.png", True), ("painting.png", False)):
        idx.add(name if member else "art/" + name, engine.fingerprint(fixture_image(name)), FIXTURES / name, member)
    idx.nn_sscd[:] = [0.99, 0.85]  # dense collection / distinct painting
    return idx


def make_pipeline():
    from detection.pipeline import MintGatePipeline
    from detection.policy import Policy

    p = MintGatePipeline.__new__(MintGatePipeline)
    p.policy = Policy()
    p.trufor = FakeTruFor()
    p.ai = FakeAIDetector()
    p.copy = copy_engine()
    p.index = tiny_copy_index(p.copy)
    p._lock = threading.Lock()
    return p
