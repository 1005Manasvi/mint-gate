"""Shared test setup.

The environment is pinned *before* `config` is imported anywhere, so tests never touch the
real storage directory, database, Pinata, or a blockchain node, whatever backend/.env contains
(`config` only uses .env values for unset variables).

Data services:
  * Postgres: TEST_DATABASE_URL (default: the `mintgate_test` database of docker-compose.yml).
    The schema is rebuilt with Alembic (downgrade base + upgrade head) once per session and
    truncated before every test. Without a reachable database the DB tests are skipped,
    unless REQUIRE_DB=1 (CI), where that is an error.
  * Redis: fakeredis (RQ jobs are run with an in-process burst worker).
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

_TMP = tempfile.mkdtemp(prefix="mintgate-tests-")
ROOT = Path(__file__).resolve().parents[2]
TEST_NETWORK = "localtest"
os.environ.update(
    {
        "APP_ENV": "test",
        "STORAGE_DIR": _TMP,
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://mintgate:mintgate@127.0.0.1:5432/mintgate_test"
        ),
        "REDIS_URL": "redis://127.0.0.1:1/0",  # unreachable on purpose: tests use fakeredis
        "RATELIMIT_STORAGE_URI": "memory://",
        "DEPLOYMENTS_DIR": str(Path(_TMP) / "deployments"),
        "CHAIN_NETWORKS": TEST_NETWORK,
        "DEFAULT_NETWORK": TEST_NETWORK,
        "REVIEWER_KEY": "test-reviewer-key-0123456789",
        "PINATA_JWT": "",  # never pin to IPFS from tests
        "VERIFIER_PRIVATE_KEY": "",
        "PUBLIC_BASE_URL": "http://testserver",
    }
)

from PIL import Image  # noqa: E402
from web3 import Web3  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARTIFACTS = ROOT / "Blockchain" / "artifacts" / "contracts" / "v2"
REAL_DEPLOYMENT = ROOT / "backend" / "chain" / "deployments" / "baseSepolia.json"
# Hardhat account #0: the backend's verifier key on chainId 31337 (public test data).
DEV_VERIFIER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
ARTIST = "0x00000000000000000000000000000000000000A1"


def _write_test_deployment():
    """A chainId-31337 deployment with the real v2 ABIs; nothing is deployed at the addresses."""
    real = json.loads(REAL_DEPLOYMENT.read_text())
    d = Path(os.environ["DEPLOYMENTS_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    dep = {
        "version": 2,
        "network": TEST_NETWORK,
        "chainId": 31337,
        "registry": {"address": "0x" + "11" * 20, "deployBlock": 1, "abi": real["registry"]["abi"]},
        "nft": {"address": "0x" + "22" * 20, "deployBlock": 1, "abi": real["nft"]["abi"]},
    }
    (d / f"{TEST_NETWORK}.json").write_text(json.dumps(dep))


_write_test_deployment()


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


# ---- in-memory registry that enforces the MintGateRegistry rules the backend relies on -----


class Reverted(RuntimeError):
    pass


class FakeRegistry:
    def __init__(self, net):
        self.net = net
        self.verdicts, self.nonces, self.txs = {}, set(), []
        self.down = False

    def _tx(self, kind):
        self.txs.append(kind)
        return {"tx_hash": f"0x{len(self.txs):064x}", "block": len(self.txs), "gas_used": 50_000}

    def _check(self):
        if self.down:
            from chain.registry import ChainUnavailable

            raise ChainUnavailable("node down")

    def verdict_of(self, report_hash):
        self._check()
        v = self.verdicts.get(report_hash.removeprefix("0x"))
        if v is None:
            return {"image_sha256": "00" * 32, "score": 0, "decision": "NONE", "artist": "0x" + "00" * 20}
        return dict(v)

    def submit_verdict(self, verdict, signature):
        from chain.eip712 import recover_signer

        self._check()
        signer = recover_signer(verdict, signature, self.net.chain_id, self.net.registry.address)
        if signer != DEV_VERIFIER:
            raise Reverted(f"UnauthorizedSigner({signer})")
        if (signer, verdict.nonce) in self.nonces:
            raise Reverted("NonceAlreadyUsed")
        rh = verdict.report_hash.hex()
        if rh in self.verdicts:
            raise Reverted("AlreadyRecorded")
        self.nonces.add((signer, verdict.nonce))
        names = {1: "PASS", 2: "REVIEW", 3: "FAIL"}
        self.verdicts[rh] = {
            "image_sha256": verdict.image_hash.hex(),
            "score": verdict.score,
            "decision": names[verdict.decision],
            "artist": verdict.artist,
        }
        return self._tx("submitVerdict")

    def resolve_review(self, report_hash, approved):
        self._check()
        v = self.verdicts.get(report_hash)
        if not v or v["decision"] != "REVIEW":
            raise Reverted("NotUnderReview")
        v["decision"] = "REVIEW_APPROVED" if approved else "REVIEW_REJECTED"
        return self._tx("resolveReview")


# ---- database + queue ----------------------------------------------------------------------


@pytest.fixture(scope="session")
def database():
    """Fresh schema in the test database (also exercises the migrations' downgrade path)."""
    from alembic import command
    from alembic.config import Config

    import db

    if not db.ping():
        msg = f"PostgreSQL not reachable at {os.environ['DATABASE_URL']} (docker compose up -d)"
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return db


TABLES = (
    "audit_log, reference_items, disputes, tokens, chain_events, indexer_cursors, attestations, reports, "
    "submissions, artists"
)


@pytest.fixture
def clean_db(database):
    from sqlalchemy import text

    with database.engine().begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    return database


@pytest.fixture
def fake_redis():
    import fakeredis

    from jobs import queue as jq

    conn = fakeredis.FakeRedis()
    jq.set_connection(conn)
    yield conn
    jq.set_connection(None)


@pytest.fixture
def run_jobs(fake_redis):
    """Run every queued job (analysis first, then chain) in-process, like `python -m jobs.worker --burst`."""
    from rq import SimpleWorker

    from jobs import queue as jq

    def run(*queues):
        names = queues or (jq.ANALYSIS, jq.CHAIN)
        SimpleWorker([jq.queue(q) for q in names], connection=fake_redis).work(burst=True)

    return run


@pytest.fixture
def worker_pipeline():
    from jobs import tasks

    p = make_pipeline()
    tasks.set_pipeline(p)
    yield p
    tasks.set_pipeline(None)


@pytest.fixture
def fake_registry(monkeypatch):
    from chain import networks, registry

    fake = FakeRegistry(networks.get(TEST_NETWORK))
    monkeypatch.setattr(registry, "client", lambda net, redis=None: fake)
    return fake


@pytest.fixture
def server(clean_db, fake_redis, worker_pipeline, fake_registry):
    import server as s

    s.limiter.reset()
    return s


@pytest.fixture
def client(server):
    return server.app.test_client()


# ---- local Hardhat node ---------------------------------------------------------------------


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hardhat_node():
    npx = shutil.which("npx")
    if not npx or not (ARTIFACTS / "MintGateRegistry.sol" / "MintGateRegistry.json").exists():
        pytest.skip("needs Node and compiled contracts (cd Blockchain; npm ci; npx hardhat compile)")
    port = _free_port()
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        [npx, "hardhat", "node", "--port", str(port)],
        cwd=ROOT / "Blockchain",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        for _ in range(120):
            if w3.is_connected():
                break
            time.sleep(0.5)
        else:
            pytest.fail("hardhat node did not start")
        yield w3
    finally:
        if os.name == "nt":  # npx spawns node as a child: kill the whole tree
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)


def artifact(name):
    return json.loads((ARTIFACTS / f"{name}.sol" / f"{name}.json").read_text())
