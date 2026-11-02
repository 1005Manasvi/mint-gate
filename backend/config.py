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
AI_DETECTOR_FILE = MODEL_DIR / "ai_detector.json"
POLICY_FILE = BACKEND_DIR / "policy.json"

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
UPLOADS_DIR = STORAGE_DIR / "uploads"
REPORTS_DIR = STORAGE_DIR / "reports"
METADATA_DIR = STORAGE_DIR / "metadata"
DEMO_DIR = BACKEND_DIR / "demo_samples"

# ---- Blockchain -------------------------------------------------------------------
DEPLOYMENT_FILE = Path(env("DEPLOYMENT_FILE", str(BACKEND_DIR / "chain" / "deployment.json")))
RPC_URL = env("RPC_URL", "http://127.0.0.1:8545")
EXPLORER_URL = env("EXPLORER_URL", "")  # e.g. https://sepolia.etherscan.io
# Hardhat's account #0 key is public test data; only used on the local chain (chainId 31337).
HARDHAT_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
VERIFIER_PRIVATE_KEY = env("VERIFIER_PRIVATE_KEY", "")

# ---- IPFS (Pinata) -----------------------------------------------------------------
PINATA_JWT = env("PINATA_JWT", "")
IPFS_GATEWAY = env("IPFS_GATEWAY", "https://gateway.pinata.cloud/ipfs/")

# ---- Web server -----------------------------------------------------------------------
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
PORT = int(env("PORT", "5000"))
REVIEWER_KEY = env("REVIEWER_KEY", "reviewer-demo-key")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
RATE_ANALYZE_PER_HOUR = int(env("RATE_ANALYZE_PER_HOUR", "60"))  # per client IP
RATE_MINT_PER_DAY = int(env("RATE_MINT_PER_DAY", "5"))  # per client IP (mints cost gas)
TRUSTED_PROXIES = int(env("TRUSTED_PROXIES", "0"))  # 1 behind HF Spaces / nginx

if IS_PRODUCTION:
    _problems = []
    if REVIEWER_KEY == "reviewer-demo-key" or len(REVIEWER_KEY) < 16:
        _problems.append("REVIEWER_KEY must be set to a strong secret (>= 16 chars)")
    if not VERIFIER_PRIVATE_KEY:
        _problems.append("VERIFIER_PRIVATE_KEY must be set")
    if _problems:
        raise RuntimeError("insecure production configuration: " + "; ".join(_problems))

for _d in (MODEL_DIR, INDEX_DIR, UPLOADS_DIR, REPORTS_DIR, METADATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
