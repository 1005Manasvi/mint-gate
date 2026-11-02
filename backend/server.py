"""Mint-Gate API server.

    POST /api/analyze            upload artwork -> report (+ anchored on-chain)
    GET  /api/report/<id>        stored report
    GET  /api/reports?status=    list reports (e.g. REVIEW queue)
    POST /api/review/<id>        reviewer approves / rejects a REVIEW verdict
    POST /api/mint/<id>          mint an NFT (only PASS or REVIEW_APPROVED)
    GET  /api/verify/<id>        compare stored report with the on-chain record
    GET  /api/token/<tokenId>    on-chain provenance of a minted token
    GET  /api/status             models, policy, chain health
    GET  /healthz                liveness probe

Development:  python server.py            Production:  gunicorn wsgi:app (see Dockerfile)
"""

import hmac
import io
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque

import requests
from flask import Flask, abort, jsonify, request, send_from_directory
from PIL import Image
from web3 import Web3
from werkzeug.middleware.proxy_fix import ProxyFix

import config
from chain import ipfs
from chain.client import ChainClient, ChainUnavailable
from detection.pipeline import MintGatePipeline, canonical_hash
from detection.preprocessing import InvalidImage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mintgate")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES + 64 * 1024
if config.TRUSTED_PROXIES:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.TRUSTED_PROXIES, x_proto=config.TRUSTED_PROXIES)

FRONTEND_DIR = config.PROJECT_DIR / "frontend"
REPORT_ID = re.compile(r"^[0-9a-f]{32}$")
MINTABLE = {"PASS", "REVIEW_APPROVED"}
CONTRACT_ERRORS = {
    "ImageAlreadyMinted": "this exact image has already been minted",
    "ReportAlreadyUsed": "this verification report was already used for a mint",
    "NotMintable": "the on-chain verdict does not allow minting",
    "UnknownReport": "no on-chain verification exists for this report",
}

pipeline = MintGatePipeline()
_chain = None
_chain_lock = threading.Lock()
_report_lock = threading.Lock()


def chain() -> ChainClient:
    """Lazily (re)connect so the server survives the node being unavailable at start."""
    global _chain
    with _chain_lock:
        if _chain is None or not _chain.ok:
            _chain = ChainClient()
        return _chain


# ---- rate limiting (per client IP, in memory; one gunicorn worker) ---------------------

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(bucket: str, limit: int, window_s: int) -> bool:
    key = (bucket, request.remote_addr or "?")
    now = time.time()
    with _hits_lock:
        q = _hits[key]
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


# ---- security headers -----------------------------------------------------------------

CSP = (
    "default-src 'self'; img-src 'self' data: https:; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'self' https://huggingface.co https://*.hf.space"
)


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# ---- report storage ---------------------------------------------------------------------


def _report_path(rid):
    if not REPORT_ID.match(rid):
        abort(404)
    return config.REPORTS_DIR / f"{rid}.json"


def load_report(rid):
    p = _report_path(rid)
    if not p.exists():
        abort(404)
    return json.loads(p.read_text())


def save_report(r):
    _report_path(r["report_id"]).write_text(json.dumps(r))


def error(msg, code=400):
    return jsonify({"success": False, "error": msg}), code


# ---- static frontend --------------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(FRONTEND_DIR, name)


@app.get("/api/reference/<path:name>")
def reference_image(name):
    if name.startswith("minted/"):  # artworks minted through Mint-Gate
        return send_from_directory(config.UPLOADS_DIR, name.removeprefix("minted/"))
    if name.startswith("art/"):  # protected paintings
        return send_from_directory(config.REFERENCE_ART_DIR, name.removeprefix("art/"))
    return send_from_directory(config.ORIGINALS_DIR, name)


@app.get("/api/upload/<rid>.png")
def uploaded_image(rid):
    _report_path(rid)
    return send_from_directory(config.UPLOADS_DIR, f"{rid}.png")


@app.get("/metadata/<rid>.json")
def token_metadata(rid):
    _report_path(rid)
    return send_from_directory(config.METADATA_DIR, f"{rid}.json")


@app.get("/api/samples")
def samples():
    if not config.DEMO_DIR.exists():
        return jsonify([])
    return jsonify(sorted(p.name for p in config.DEMO_DIR.iterdir() if p.suffix.lower() in (".png", ".jpg")))


@app.get("/api/samples/<path:name>")
def sample_file(name):
    return send_from_directory(config.DEMO_DIR, name)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "references": len(pipeline.index)})


# ---- API --------------------------------------------------------------------------------


@app.get("/api/status")
def status():
    meta_results = {}
    if config.RESNET_META.exists():
        meta_results = json.loads(config.RESNET_META.read_text()).get("results", {})
    return jsonify(
        {
            "references": len(pipeline.index),
            "policy": pipeline.policy.describe(),
            "models": {
                "resnet18": {"version": pipeline.resnet.version, "results": meta_results},
                "ai_detector": {"version": getattr(pipeline.ai, "version", None), "metrics": pipeline.ai.metrics},
            },
            "ipfs": ipfs.enabled(),
            "ipfs_gateway": config.IPFS_GATEWAY,
            "chain": chain().status(),
        }
    )


@app.post("/api/analyze")
def analyze():
    if rate_limited("analyze", config.RATE_ANALYZE_PER_HOUR, 3600):
        return error("rate limit reached - try again later", 429)
    f = request.files.get("image")
    if f is None:
        return error("form field 'image' is required")
    try:
        report, img = pipeline.analyse(f.read(), f.filename or "")
    except InvalidImage as exc:
        return error(str(exc))

    (config.UPLOADS_DIR / f"{report['report_id']}.png").write_bytes(img.png_bytes)
    report["status"] = report["decision"]
    report["review"] = None
    report["mint"] = None
    try:
        report["chain"] = {"anchored": True, **chain().record(report)}
    except Exception as exc:  # analysis still succeeds; anchoring is retried at review/mint
        log.warning("anchoring failed: %s", exc)
        report["chain"] = {"anchored": False, "error": "blockchain temporarily unavailable"}
    with _report_lock:
        save_report(report)
    return jsonify({"success": True, "report": report})


@app.get("/api/report/<rid>")
def get_report(rid):
    return jsonify(load_report(rid))


@app.get("/api/reports")
def list_reports():
    wanted = request.args.get("status")
    out = []
    for p in sorted(config.REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
        r = json.loads(p.read_text())
        if wanted and r.get("status") != wanted:
            continue
        out.append(
            {
                k: r.get(k)
                for k in (
                    "report_id",
                    "filename",
                    "decision",
                    "status",
                    "score",
                    "created_at",
                    "reasons",
                    "report_hash",
                )
            }
        )
    return jsonify(out)


def _ensure_anchored(r):
    if not r.get("chain", {}).get("anchored"):
        r["chain"] = {"anchored": True, **chain().record(r)}


@app.post("/api/review/<rid>")
def review(rid):
    if rate_limited("review", 30, 3600):
        return error("rate limit reached - try again later", 429)
    body = request.get_json(silent=True) or {}
    key = str(body.get("reviewer_key", ""))
    if not hmac.compare_digest(key.encode(), config.REVIEWER_KEY.encode()):
        return error("invalid reviewer key", 403)
    approve = bool(body.get("approve"))
    with _report_lock:
        r = load_report(rid)
        if r["status"] != "REVIEW":
            return error(f"report status is {r['status']}, not REVIEW", 409)
        try:
            _ensure_anchored(r)
            tx = chain().resolve_review(r["report_hash"], approve)
        except ChainUnavailable as exc:
            log.warning("review: chain unavailable: %s", exc)
            return error("blockchain temporarily unavailable", 503)
        r["status"] = "REVIEW_APPROVED" if approve else "REVIEW_REJECTED"
        r["review"] = {"approved": approve, "note": str(body.get("note", ""))[:500], **tx}
        save_report(r)
    return jsonify({"success": True, "status": r["status"], "review": r["review"]})


def _publish(r, rid):
    """Store image, verification report and ERC-721 metadata; returns (tokenURI, links)."""
    image_bytes = (config.UPLOADS_DIR / f"{rid}.png").read_bytes()
    public_report = {k: v for k, v in r.items() if k not in ("evidence", "chain", "review", "mint")}
    attributes = [
        {"trait_type": "Authenticity score", "value": r["score"]},
        {"trait_type": "Mint-Gate decision", "value": r["status"]},
        {"trait_type": "Policy version", "value": r["policy_version"]},
    ]
    if ipfs.enabled():
        image_cid = ipfs.pin_file(image_bytes, f"{rid}.png", "image/png")
        report_cid = ipfs.pin_json(public_report, f"mintgate-report-{rid}")
        image_uri, report_uri = f"ipfs://{image_cid}", f"ipfs://{report_cid}"
    else:  # local development fallback
        image_uri = f"{config.PUBLIC_BASE_URL}/api/upload/{rid}.png"
        report_uri = f"{config.PUBLIC_BASE_URL}/api/report/{rid}"
    metadata = {
        "name": f"Mint-Gate verified artwork {r['image_sha256'][:10]}",
        "description": "Artwork verified by the Mint-Gate AI forensics pipeline before minting.",
        "image": image_uri,
        "external_url": f"{config.PUBLIC_BASE_URL}/?report={rid}",
        "attributes": attributes,
        "mint_gate": {
            "report_id": rid,
            "report_hash": r["report_hash"],
            "image_sha256": r["image_sha256"],
            "verification_report": report_uri,
        },
    }
    (config.METADATA_DIR / f"{rid}.json").write_text(json.dumps(metadata, indent=2))
    if ipfs.enabled():
        token_uri = f"ipfs://{ipfs.pin_json(metadata, f'mintgate-metadata-{rid}')}"
    else:
        token_uri = f"{config.PUBLIC_BASE_URL}/metadata/{rid}.json"
    return token_uri, {"image": image_uri, "report": report_uri}


@app.post("/api/mint/<rid>")
def mint(rid):
    body = request.get_json(silent=True) or {}
    recipient = str(body.get("recipient", "")).strip()
    if not Web3.is_address(recipient):
        return error("recipient must be a valid 0x Ethereum address")
    with _report_lock:
        r = load_report(rid)
        # Server-side gate (the contract enforces the same rule on-chain).
        if r["status"] not in MINTABLE:
            return error(f"minting blocked by Mint-Gate policy: status {r['status']}", 403)
        if r.get("mint"):
            return error("already minted", 409)
        if rate_limited("mint", config.RATE_MINT_PER_DAY, 86400):
            return error("daily mint limit reached for this client", 429)
        try:
            token_uri, links = _publish(r, rid)
            _ensure_anchored(r)
            tx = chain().mint(recipient, r["report_hash"], token_uri)
        except ipfs.IPFSError as exc:
            log.error("ipfs: %s", exc)
            return error("could not store the artwork on IPFS - try again", 502)
        except ChainUnavailable as exc:
            log.warning("mint: chain unavailable: %s", exc)
            return error("blockchain temporarily unavailable", 503)
        except Exception as exc:  # contract revert = on-chain rule violated
            reason = next((e for e in CONTRACT_ERRORS if e in str(exc)), None)
            log.warning("mint failed: %s", exc)
            return error(f"blocked on-chain: {CONTRACT_ERRORS[reason]}" if reason else "mint transaction failed", 409)
        r["mint"] = {"recipient": Web3.to_checksum_address(recipient), "token_uri": token_uri, "links": links, **tx}
        r["status"] = "MINTED"
        save_report(r)
    pipeline.register_minted(rid)
    return jsonify({"success": True, "mint": r["mint"]})


@app.get("/api/verify/<rid>")
def verify(rid):
    """Tamper check: recompute the stored report's hash and compare with the chain."""
    r = load_report(rid)
    recomputed = canonical_hash(r)
    try:
        onchain = chain().verification(r["report_hash"])
    except ChainUnavailable:
        return error("blockchain temporarily unavailable", 503)
    return jsonify(
        {
            "report_hash": r["report_hash"],
            "recomputed_hash": recomputed,
            "report_untampered": recomputed == r["report_hash"],
            "on_chain": onchain,
            "on_chain_matches": onchain["decision"] != "NONE"
            and onchain["image_sha256"] == r["image_sha256"]
            and onchain["score"] == r["score"],
        }
    )


@app.get("/api/token/<int:token_id>")
def token(token_id):
    try:
        t = chain().token(token_id)
    except ChainUnavailable:
        return error("blockchain temporarily unavailable", 503)
    except Exception:
        t = None
    if not t:
        return error("token not found", 404)
    t["token_uri_http"] = ipfs.gateway_url(t["token_uri"])
    return jsonify(t)


@app.errorhandler(413)
def too_large(_):
    return error("file too large", 413)


@app.errorhandler(500)
def internal(_):
    return error("internal error", 500)


# ---- startup: restore minted artworks from chain + IPFS -------------------------------


def _fetch_minted_image(token):
    """(report_id, image) for a minted token, from local storage or via its IPFS metadata."""
    own = f"{config.PUBLIC_BASE_URL}/metadata/"
    if token["token_uri"].startswith(own):  # served by this very app: read from disk
        rid = token["token_uri"][len(own) :].removesuffix(".json")
        local_meta = config.METADATA_DIR / f"{rid}.json"
        if REPORT_ID.match(rid) and local_meta.exists() and (config.UPLOADS_DIR / f"{rid}.png").exists():
            return rid, None
        return None
    meta = requests.get(ipfs.gateway_url(token["token_uri"]), timeout=30).json()
    rid = meta["mint_gate"]["report_id"]
    local = config.UPLOADS_DIR / f"{rid}.png"
    if local.exists():
        return rid, None
    img = Image.open(io.BytesIO(requests.get(ipfs.gateway_url(meta["image"]), timeout=60).content))
    return rid, img.convert("RGB")


def _startup_sync():
    try:
        c = chain()
        if c.ok:
            pipeline.sync_minted_from_chain(c, _fetch_minted_image)
    except Exception as exc:
        log.warning("startup sync skipped: %s", exc)


threading.Thread(target=_startup_sync, daemon=True).start()

if __name__ == "__main__":
    # Development server, bound to localhost only. Production uses gunicorn (Dockerfile).
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
