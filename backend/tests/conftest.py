"""Shared test setup.

The environment is pinned *before* `config` is imported anywhere, so tests never
touch the real storage directory, Pinata, or a blockchain node, whatever
backend/.env contains (`config` only uses .env values for unset variables).
"""

import os
import shutil
import tempfile
import threading
from pathlib import Path

import numpy as np
import pytest

_TMP = tempfile.mkdtemp(prefix="mintgate-tests-")
os.environ.update(
    {
        "APP_ENV": "test",
        "STORAGE_DIR": _TMP,
        "REVIEWER_KEY": "test-reviewer-key-0123456789",
        "PINATA_JWT": "",  # never pin to IPFS from tests
        "VERIFIER_PRIVATE_KEY": "",
        "RPC_URL": "http://127.0.0.1:1",  # unreachable on purpose
        "PUBLIC_BASE_URL": "http://testserver",
    }
)

from PIL import Image  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


def fixture_image(name) -> Image.Image:
    with Image.open(FIXTURES / name) as im:
        return im.convert("RGB")


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


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
    """Deterministic 'embedding': a mean-centred, L2-normalised 8x8 RGB thumbnail.
    Identical pixels give similarity 1.0, unrelated images a low one, like CLIP."""

    def encode_one(self, img):
        v = np.asarray(img.convert("RGB").resize((8, 8), Image.BILINEAR), dtype=np.float32).ravel()
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


def make_pipeline(cls=None):
    from detection.pipeline import MintGatePipeline
    from detection.policy import Policy

    cls = cls or MintGatePipeline
    p = cls.__new__(cls)
    p.policy = Policy()
    p.resnet = FakeResNet()
    p.ai = FakeAIDetector()
    p.clip = FakeClip()
    p.index = tiny_index(p.clip)
    p._lock = threading.Lock()
    return p


# ---- in-memory chain that enforces the same rules as MintGateNFT.sol -------------------


class FakeChain:
    ok = True
    error = None

    def __init__(self):
        self.verifications, self.tokens, self.image_token = {}, {}, {}
        self.block = 0

    def _tx(self):
        self.block += 1
        return {"tx_hash": f"0x{self.block:064x}", "block": self.block, "gas_used": 50_000}

    @staticmethod
    def _revert(name):
        raise RuntimeError(f"execution reverted: custom error {name}")

    def status(self):
        return {"connected": True, "network": "fake", "total_verifications": len(self.verifications)}

    def record(self, report):
        if report["report_hash"] in self.verifications:
            self._revert("AlreadyRecorded")
        self.verifications[report["report_hash"]] = {
            "image_sha256": report["image_sha256"],
            "policy_sha256": report["policy_sha256"],
            "score": report["score"],
            "decision": report["decision"],
            "timestamp": 0,
            "verifier": "0xverifier",
            "token_id": 0,
            "policy_version": report["policy_version"],
        }
        return self._tx()

    def resolve_review(self, report_hash, approved):
        v = self.verifications.get(report_hash)
        if not v or v["decision"] != "REVIEW":
            self._revert("NotUnderReview")
        v["decision"] = "REVIEW_APPROVED" if approved else "REVIEW_REJECTED"
        return self._tx()

    def mint(self, to, report_hash, token_uri):
        v = self.verifications.get(report_hash)
        if not v:
            self._revert("UnknownReport")
        if v["decision"] not in ("PASS", "REVIEW_APPROVED"):
            self._revert("NotMintable")
        if v["token_id"]:
            self._revert("ReportAlreadyUsed")
        if v["image_sha256"] in self.image_token:
            self._revert("ImageAlreadyMinted")
        token_id = len(self.tokens) + 1
        v["token_id"] = token_id
        self.image_token[v["image_sha256"]] = token_id
        self.tokens[token_id] = {"owner": to, "token_uri": token_uri, "report_hash": report_hash}
        return {**self._tx(), "token_id": token_id}

    def verification(self, report_hash):
        empty = {
            "image_sha256": "00" * 32,
            "policy_sha256": "00" * 32,
            "score": 0,
            "decision": "NONE",
            "timestamp": 0,
            "verifier": "0x" + "00" * 20,
            "token_id": 0,
            "policy_version": "",
        }
        return dict(self.verifications.get(report_hash, empty))

    def minted_tokens(self):
        return [{"token_id": i, **t} for i, t in self.tokens.items()]

    def token(self, token_id):
        t = self.tokens.get(token_id)
        return t and {"token_id": token_id, **t, "verification": self.verification(t["report_hash"])}


@pytest.fixture(scope="session")
def server_module():
    """Import server.py with the fake pipeline and chain (its import has side effects)."""
    import chain.client
    import detection.pipeline

    mp = pytest.MonkeyPatch()
    mp.setattr(chain.client, "ChainClient", FakeChain)
    real = detection.pipeline.MintGatePipeline
    mp.setattr(detection.pipeline, "MintGatePipeline", lambda: make_pipeline(real))
    import server

    yield server
    mp.undo()


@pytest.fixture
def server(server_module):
    """Fresh chain, index, fake model outputs and rate limits for every test."""
    s = server_module
    with s._chain_lock:
        s._chain = FakeChain()
    s.pipeline.index = tiny_index(s.pipeline.clip)
    s.pipeline.resnet.value = s.pipeline.ai.value = 0.05
    s._hits.clear()
    return s


@pytest.fixture
def client(server):
    return server.app.test_client()
