"""Central configuration: filesystem paths and runtime settings.

Settings come from environment variables; for local development they can be
put in backend/.env (KEY=value lines, never committed). Every module imports
paths from here so nothing depends on the current working directory.
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path):
    """Minimal .env loader (no extra dependency). Real env vars take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BACKEND_DIR / ".env")
env = os.environ.get

APP_ENV = env("APP_ENV", "development")  # "production" enables strict checks
IS_PRODUCTION = APP_ENV == "production"

# ---- Dataset (training only) --------------------------------------------------
DATA_DIR = BACKEND_DIR / "data" / "merged"
ORIGINALS_DIR = Path(env("REFERENCE_DIR", str(DATA_DIR / "originals_cleaned")))  # seed collection
AI_DIR = DATA_DIR / "AI_cleaned"

# ---- Model artefacts -----------------------------------------------------------
MODEL_DIR = BACKEND_DIR / "model"
SPLIT_FILE = MODEL_DIR / "split.json"
RESNET_WEIGHTS = MODEL_DIR / "best_model.pt"
RESNET_META = MODEL_DIR / "model_meta.json"
# Phase 5 model profile. MINTGATE_PERMISSIVE_ONLY=1: no non-commercial / research-only weights or
# training data anywhere (TruFor off; the AI probe trained on permissive sources only, with its own
# calibrated policy). MINTGATE_TRUFOR: "auto" (default) = on when a GPU is present and permitted;
# "0" / "1" force it (TruFor needs 2-36 s per image on a CPU, see docs/models.md).
PERMISSIVE_ONLY = env("MINTGATE_PERMISSIVE_ONLY", "0") == "1"
TRUFOR_MODE = env("MINTGATE_TRUFOR", "auto")
AI_DETECTOR_FILE = (
    Path(env("AI_DETECTOR_FILE", ""))
    if env("AI_DETECTOR_FILE")
    else (MODEL_DIR / ("ai_detector.permissive.json" if PERMISSIVE_ONLY else "ai_detector.json"))
)
POLICY_FILE = BACKEND_DIR / ("policy.permissive.json" if PERMISSIVE_ONLY else "policy.json")

# ---- Reference index (the "already minted" collection) -------------------------
INDEX_DIR = BACKEND_DIR / "index"
INDEX_FILES = INDEX_DIR / "files.json"
INDEX_PHASH = INDEX_DIR / "phash.npy"
INDEX_CLIP = INDEX_DIR / "clip.npy"
INDEX_NN = INDEX_DIR / "nn_sim.npy"
# Protected artworks (museum / WikiArt paintings) that are part of the reference collection.
REFERENCE_ART_DIR = Path(env("REFERENCE_ART_DIR", str(BACKEND_DIR / "data" / "realworld" / "images")))
CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"

# ---- Runtime storage -------------------------------------------------------------
STORAGE_DIR = Path(env("STORAGE_DIR", str(BACKEND_DIR / "storage")))
UPLOADS_DIR = STORAGE_DIR / "uploads"  # canonical PNGs, <report_id>.png
INCOMING_DIR = STORAGE_DIR / "incoming"  # raw uploads waiting for a worker, <submission_id>
EVIDENCE_DIR = STORAGE_DIR / "evidence"  # heatmaps / match images, <report_id>.json
REPORTS_DIR = STORAGE_DIR / "reports"  # legacy v1 JSON reports (imported by db.migrate_json)
METADATA_DIR = STORAGE_DIR / "metadata"
NFT_THUMBS_DIR = STORAGE_DIR / "nft" / "thumbs"  # 128 px thumbnails of indexed on-chain NFTs
DEMO_DIR = BACKEND_DIR / "demo_samples"

# ---- Database + queue ---------------------------------------------------------------
# docker-compose.yml provides both locally.
DATABASE_URL = env("DATABASE_URL", "postgresql+psycopg://mintgate:mintgate@127.0.0.1:5432/mintgate")
REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379/0")
JOB_TIMEOUT_S = int(env("JOB_TIMEOUT_S", "120"))  # analysis; also the crash-detection delay (+60 s)

# ---- Blockchain (contracts v2) -----------------------------------------------------
# One deployments/<network>.json per network (written by Blockchain/scripts/deploy-v2.js).
DEPLOYMENTS_DIR = Path(env("DEPLOYMENTS_DIR", str(BACKEND_DIR / "chain" / "deployments")))
# Networks the indexer follows, and the one used for backend-submitted (REVIEW / FAIL) verdicts.
CHAIN_NETWORKS = [n.strip() for n in env("CHAIN_NETWORKS", "baseSepolia").split(",") if n.strip()]
DEFAULT_NETWORK = env("DEFAULT_NETWORK", CHAIN_NETWORKS[0] if CHAIN_NETWORKS else "baseSepolia")
# Per-network overrides: RPC_URL_BASESEPOLIA, CONFIRMATIONS_SEPOLIA, ...
DEFAULT_RPC = {
    "localhost": "http://127.0.0.1:8545",
    "sepolia": "https://ethereum-sepolia-rpc.publicnode.com",
    "baseSepolia": "https://sepolia.base.org",
}
DEFAULT_CONFIRMATIONS = {"localhost": 0, "sepolia": 3, "baseSepolia": 5}
EXPLORERS = {"sepolia": "https://sepolia.etherscan.io", "baseSepolia": "https://sepolia.basescan.org"}
# Anchor FAIL verdicts on chain too (costs gas per failed upload; REVIEW is always anchored).
ANCHOR_FAILED_VERDICTS = env("ANCHOR_FAILED_VERDICTS", "1") == "1"
# Hardhat's account #0 key is public test data; only used on the local chain (chainId 31337).
HARDHAT_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
VERIFIER_PRIVATE_KEY = env("VERIFIER_PRIVATE_KEY", "")


def network_setting(prefix: str, network: str, default):
    return env(f"{prefix}_{network.upper()}", default)


# RPC endpoints handed to browsers (PUBLIC_RPC_<NET>): never RPC_URL_<NET>, which may embed an API key.
NATIVE_CURRENCY = {"name": "Ether", "symbol": "ETH", "decimals": 18}
CHAIN_LABELS = {"localhost": "Hardhat (local)", "sepolia": "Sepolia", "baseSepolia": "Base Sepolia"}


# ---- IPFS (Pinata) -----------------------------------------------------------------
PINATA_JWT = env("PINATA_JWT", "")
IPFS_GATEWAY = env("IPFS_GATEWAY", "https://gateway.pinata.cloud/ipfs/")

# ---- Notifications (Phase 6) -----------------------------------------------------
DISCORD_WEBHOOK_URL = env("DISCORD_WEBHOOK_URL", "")  # secret: never log it
NOTIFY_DISPUTES = env("NOTIFY_DISPUTES", "1") == "1"
NOTIFY_REVIEWS = env("NOTIFY_REVIEWS", "1") == "1"
# Disputes: largest original file a reporter may attach, and the reason length a reviewer must write.
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MIN_REASON_CHARS = 20

# ---- Web server -----------------------------------------------------------------------
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
PORT = int(env("PORT", "5000"))
REVIEWER_KEY = env("REVIEWER_KEY", "reviewer-demo-key")
# Phase 6: reviewers are wallets holding REVIEWER_ROLE (SIWE); 1 turns the shared key off entirely.
REVIEWER_KEY_DISABLED = env("REVIEWER_KEY_DISABLED", "0") == "1"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
RATE_ANALYZE_PER_HOUR = int(env("RATE_ANALYZE_PER_HOUR", "60"))  # per client IP
RATE_ATTEST_PER_DAY = int(env("RATE_ATTEST_PER_DAY", "20"))  # per client IP (attestations pin to IPFS)
# Flask-Limiter storage; shared by every API instance. "memory://" only for tests.
RATELIMIT_STORAGE_URI = env("RATELIMIT_STORAGE_URI", REDIS_URL)
TRUSTED_PROXIES = int(env("TRUSTED_PROXIES", "0"))  # 1 behind HF Spaces / nginx

# ---- Wallet sign-in (SIWE, EIP-4361) ---------------------------------------------------------
SESSION_TTL_S = int(env("SESSION_TTL_S", "3600"))
SIWE_NONCE_TTL_S = 300
SIWE_MAX_AGE_S = 600  # a signed SIWE message / submission claim is accepted for 10 minutes
# Secure cookies also work on http://localhost and http://127.0.0.1 in current browsers.
COOKIE_SECURE = env("COOKIE_SECURE", "1") == "1"


def _siwe_domains() -> list[str]:
    """Hosts (host[:port]) a SIWE message may name; default: PUBLIC_BASE_URL's host (+ its loopback alias)."""
    if env("SIWE_DOMAINS"):
        return [d.strip() for d in env("SIWE_DOMAINS").split(",") if d.strip()]
    host = PUBLIC_BASE_URL.split("://", 1)[-1].split("/", 1)[0]
    out = [host]
    for a, b in (("127.0.0.1", "localhost"), ("localhost", "127.0.0.1")):
        if host == a or host.startswith(a + ":"):
            out.append(b + host[len(a) :])
    return out


SIWE_DOMAINS = _siwe_domains()

if IS_PRODUCTION:
    _problems = []
    if REVIEWER_KEY == "reviewer-demo-key" or len(REVIEWER_KEY) < 16:
        _problems.append("REVIEWER_KEY must be set to a strong secret (>= 16 chars)")
    if not VERIFIER_PRIVATE_KEY:
        _problems.append("VERIFIER_PRIVATE_KEY must be set")
    if _problems:
        raise RuntimeError("insecure production configuration: " + "; ".join(_problems))

for _d in (MODEL_DIR, INDEX_DIR, UPLOADS_DIR, INCOMING_DIR, EVIDENCE_DIR, REPORTS_DIR, METADATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
