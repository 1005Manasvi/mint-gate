"""Build and deploy Mint-Gate to a Hugging Face Space (Docker SDK).

    python deploy/hf_space.py --space <hf-username>/mint-gate            # build + upload
    python deploy/hf_space.py --space <hf-username>/mint-gate --build-only

Reads from backend/.env (never uploaded):
    HF_TOKEN               Hugging Face access token with write permission
    VERIFIER_PRIVATE_KEY   verifier wallet key            -> Space secret
    REVIEWER_KEY           human-reviewer secret          -> Space secret
    PINATA_JWT             IPFS pinning token             -> Space secret

The contract must already be deployed to Sepolia (backend/chain/deployment.json
with chainId 11155111): see README "Deploy".
"""
import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DIST = ROOT / "deploy" / "dist" / "space"
sys.path.insert(0, str(BACKEND))

import config  # noqa: E402  (loads backend/.env into os.environ)

SEPOLIA_CHAIN_ID = 11155111
BACKEND_FILES = ["config.py", "server.py", "wsgi.py", "policy.json",
                 "chain/__init__.py", "chain/client.py", "chain/ipfs.py", "chain/deployment.json",
                 "model/best_model.pt", "model/model_meta.json", "model/ai_detector.json",
                 "index/files.json", "index/phash.npy", "index/clip.npy", "index/nn_sim.npy"]

SPACE_README = """---
title: Mint-Gate
emoji: 🛡️
colorFrom: indigo
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: AI forensics gate that verifies artwork before NFT minting
---

# Mint-Gate

Upload an artwork: six forensic checks (ResNet-18, AI-content ensemble, copy-move,
pHash, CLIP, ORB) produce an authenticity score; a policy engine decides
PASS / REVIEW / FAIL; verdicts and verified mints are recorded on Ethereum Sepolia
(`MintGateNFT`), artwork and reports are pinned to IPFS.

Source code and model evaluation: see the project repository.
"""


def build(skip_chain_check=False):
    dep = json.loads((BACKEND / "chain" / "deployment.json").read_text())
    if dep["chainId"] != SEPOLIA_CHAIN_ID and not skip_chain_check:
        sys.exit(f"deployment.json is for chainId {dep['chainId']}; deploy the contract to Sepolia first "
                 "(npx hardhat run scripts/deploy.js --network sepolia)")
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "backend").mkdir(parents=True)

    for rel in BACKEND_FILES:
        dst = DIST / "backend" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BACKEND / rel, dst)
    for py in (BACKEND / "detection").glob("*.py"):
        dst = DIST / "backend" / "detection" / py.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(py, dst)
    shutil.copytree(BACKEND / "demo_samples", DIST / "backend" / "demo_samples")
    shutil.copytree(ROOT / "frontend", DIST / "frontend")

    # Protected reference collection (NFTs + "art/" paintings): only indexed files, one zip.
    files = json.loads((BACKEND / "index" / "files.json").read_text())
    with zipfile.ZipFile(DIST / "reference.zip", "w", zipfile.ZIP_STORED) as z:
        for name in files:
            src = (config.REFERENCE_ART_DIR / name[4:]) if name.startswith("art/") else (config.ORIGINALS_DIR / name)
            z.write(src, name)

    shutil.copy2(ROOT / "deploy" / "Dockerfile", DIST / "Dockerfile")
    shutil.copy2(ROOT / "deploy" / "requirements-deploy.txt", DIST / "requirements-deploy.txt")
    (DIST / "README.md").write_text(SPACE_README, encoding="utf-8")
    (DIST / ".gitattributes").write_text("*.pt filter=lfs diff=lfs merge=lfs -text\n"
                                         "*.npy filter=lfs diff=lfs merge=lfs -text\n"
                                         "*.zip filter=lfs diff=lfs merge=lfs -text\n")
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1e6
    print(f"built {DIST} ({size:.0f} MB) for contract {dep['address']}")


def upload(space_id):
    from huggingface_hub import HfApi
    env = config.env
    token = env("HF_TOKEN")
    missing = [k for k in ("HF_TOKEN", "VERIFIER_PRIVATE_KEY", "REVIEWER_KEY") if not env(k)]
    if missing:
        sys.exit(f"set {', '.join(missing)} in backend/.env first")
    if len(env("REVIEWER_KEY")) < 16:
        sys.exit("REVIEWER_KEY must be at least 16 characters")

    api = HfApi(token=token)
    api.create_repo(space_id, repo_type="space", space_sdk="docker", exist_ok=True)
    owner, name = space_id.split("/")
    public_url = f"https://{owner}-{name}".lower().replace("_", "-") + ".hf.space"

    for key in ("VERIFIER_PRIVATE_KEY", "REVIEWER_KEY", "PINATA_JWT"):
        if env(key):
            api.add_space_secret(space_id, key, env(key))  # encrypted, never in the repo
    api.add_space_variable(space_id, "PUBLIC_BASE_URL", public_url)

    api.upload_folder(repo_id=space_id, repo_type="space", folder_path=str(DIST),
                      commit_message="Deploy Mint-Gate")
    print(f"uploaded. Space: https://huggingface.co/spaces/{space_id}")
    print(f"app URL (after the build finishes, ~10 min): {public_url}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--skip-chain-check", action="store_true", help="packaging test only (never upload)")
    args = ap.parse_args()
    build(args.skip_chain_check)
    if args.skip_chain_check:
        sys.exit("packaging test complete - not uploading a build that targets a non-Sepolia chain")
    if not args.build_only:
        upload(args.space)
