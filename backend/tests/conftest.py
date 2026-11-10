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

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

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
        "DISCORD_WEBHOOK_URL": "",  # never post to Discord from tests
        "VERIFIER_PRIVATE_KEY": "",
        "PUBLIC_BASE_URL": "http://testserver",
    }
)

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
from web3 import Web3  # noqa: E402

from tests.fakes import FIXTURES, fixture_image, make_pipeline  # noqa: E402,F401

ARTIFACTS = ROOT / "Blockchain" / "artifacts" / "contracts" / "v2"
REAL_DEPLOYMENT = ROOT / "backend" / "chain" / "deployments" / "baseSepolia.json"
# Hardhat account #0: the backend's verifier key on chainId 31337 (public test data).
DEV_VERIFIER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
# Throwaway test keys: the artist and a second wallet sign in with SIWE and sign their uploads.
ARTIST_KEY = "0x" + "a1" * 32
OTHER_KEY = "0x" + "b2" * 32
ARTIST = Account.from_key(ARTIST_KEY).address
OTHER = Account.from_key(OTHER_KEY).address
SIWE_DOMAIN = "testserver"  # host of PUBLIC_BASE_URL


# Phase 6: owners of the test deployment's governance Safe (throwaway keys)
OWNER_KEYS = ["0x" + "c3" * 32, "0x" + "d4" * 32, "0x" + "e5" * 32]
OWNERS = [Account.from_key(k).address for k in OWNER_KEYS]
SAFE_VENDOR = ROOT / "Blockchain" / "vendor" / "safe-1.4.1"


def governance_abis() -> dict:
    """Resolver / policy registry / timelock ABIs: from a real deployment file, else compiled artifacts."""
    for f in sorted(REAL_DEPLOYMENT.parent.glob("*.json")):
        gov = json.loads(f.read_text()).get("governance")
        if gov and gov.get("disputeResolver"):
            return {k: gov[k]["abi"] for k in ("disputeResolver", "policyRegistry", "timelock")}
    names = {"disputeResolver": "DisputeResolver", "policyRegistry": "PolicyRegistry", "timelock": "MintGateTimelock"}
    try:
        return {k: artifact(n)["abi"] for k, n in names.items()}
    except FileNotFoundError:
        return {k: [] for k in names}


def _write_test_deployment():
    """A chainId-31337 deployment with the real v2 ABIs; nothing is deployed at the addresses."""
    real = json.loads(REAL_DEPLOYMENT.read_text())
    d = Path(os.environ["DEPLOYMENTS_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    abis = governance_abis()
    dep = {
        "version": 2,
        "network": TEST_NETWORK,
        "chainId": 31337,
        "disputeBond": str(10**15),
        "registry": {"address": "0x" + "11" * 20, "deployBlock": 1, "abi": real["registry"]["abi"]},
        "nft": {"address": "0x" + "22" * 20, "deployBlock": 1, "abi": real["nft"]["abi"]},
        "governance": {
            "safe": {
                "address": Web3.to_checksum_address("0x" + "66" * 20),
                "owners": OWNERS,
                "threshold": 2,
                "deployBlock": 1,
                "abi": json.loads((SAFE_VENDOR / "Safe.json").read_text())["abi"],
            },
            "timelock": {"address": "0x" + "55" * 20, "deployBlock": 1, "minDelay": 3600, "abi": abis["timelock"]},
            "policyRegistry": {"address": "0x" + "44" * 20, "deployBlock": 1, "abi": abis["policyRegistry"]},
            "disputeResolver": {"address": "0x" + "33" * 20, "deployBlock": 1, "abi": abis["disputeResolver"]},
        },
    }
    (d / f"{TEST_NETWORK}.json").write_text(json.dumps(dep))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


# ---- in-memory registry that enforces the MintGateRegistry rules the backend relies on -----


class Reverted(RuntimeError):
    pass


class FakeRegistry:
    def __init__(self, net):
        self.net = net
        self.verdicts, self.nonces, self.txs = {}, set(), []
        self.down = False
        self.roles = {"REVIEWER_ROLE": set()}  # tests add reviewer wallets here

    def has_role(self, role, account):
        self._check()
        return account in self.roles.get(role, set())

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
    "audit_log, reference_items, nft_collections, safe_proposals, disputes, dispute_evidence, tokens, chain_events, indexer_cursors, attestations, reports, "
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
    """Anonymous browser (no wallet session)."""
    return server.app.test_client()


# ---- wallets: SIWE sign-in + signed uploads ----------------------------------------------------


def sign_text(key: str, text: str) -> str:
    """EIP-191 personal_sign, as a wallet does it."""
    return "0x" + bytes(Account.sign_message(encode_defunct(text=text), private_key=key).signature).hex()


def now_iso(offset_s: int = 0) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=offset_s)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def siwe_text(address: str, nonce: str, chain_id: int = 31337, domain: str = SIWE_DOMAIN, **fields) -> str:
    from datetime import datetime

    import auth

    issued = fields.pop("issued_at", now_iso())
    return auth.SiweMessage(
        domain=domain,
        address=address,
        statement=fields.pop("statement", auth.STATEMENT),
        uri=fields.pop("uri", f"http://{domain}"),
        version=fields.pop("version", "1"),
        chain_id=chain_id,
        nonce=nonce,
        issued_at=datetime.fromisoformat(issued),
        **fields,
    ).text()


class WalletClient:
    """A browser with a wallet: Flask test client + SIWE session + CSRF header on every POST."""

    def __init__(self, app, key: str):
        self.http = app.test_client()
        self.key = key
        self.address = Account.from_key(key).address
        self.csrf = ""

    def __getattr__(self, name):  # get(), delete(), ... go straight to the test client
        return getattr(self.http, name)

    def post(self, path, headers=None, **kw):
        return self.http.post(path, headers={"X-CSRF-Token": self.csrf, **(headers or {})}, **kw)

    def sign(self, text: str) -> str:
        return sign_text(self.key, text)

    def login(self, **fields):
        nonce = self.http.get("/api/auth/nonce").get_json()["nonce"]
        text = siwe_text(self.address, nonce, **fields)
        resp = self.http.post("/api/auth/verify", json={"message": text, "signature": self.sign(text)})
        if resp.status_code == 200:
            self.csrf = resp.get_json()["csrf"]
        return resp

    def upload(self, raw: bytes, name: str = "upload.png", issued_at: str | None = None, key: str | None = None):
        """POST /api/analyze with the claim signature (by `key`, default: this wallet)."""
        import hashlib

        import auth

        issued = issued_at or now_iso()
        claim = auth.claim_message(SIWE_DOMAIN, self.address, hashlib.sha256(raw).hexdigest(), issued)
        return self.post(
            "/api/analyze",
            data={
                "image": (io.BytesIO(raw), name),
                "claim_issued_at": issued,
                "claim_signature": sign_text(key or self.key, claim),
            },
            content_type="multipart/form-data",
        )


@pytest.fixture
def wallet(server):
    """Factory: wallet(key) -> signed-in WalletClient."""

    def make(key=ARTIST_KEY, login=True):
        w = WalletClient(server.app, key)
        if login:
            assert w.login().status_code == 200
        return w

    return make


@pytest.fixture
def artist_client(wallet):
    return wallet(ARTIST_KEY)


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


_write_test_deployment()
