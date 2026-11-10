"""Mint-Gate API server (stateless: Postgres + Redis + shared storage; run as many as needed).

    GET  /api/auth/nonce | POST /api/auth/verify | GET /api/auth/me | POST /api/auth/logout
                                   Sign-In with Ethereum (auth.py): session cookie + CSRF token
    POST /api/analyze              upload signed by the session wallet -> 202 {job_id}
    GET  /api/jobs/<id>            job status; the report once done
    GET  /api/report/<id>          stored report (+ evidence images)
    GET  /api/reports?status=      list reports (e.g. the REVIEW queue)
    POST /api/review/<id>          reviewer approves / rejects a REVIEW verdict (mirrored on chain)
    POST /api/attest/<id>          EIP-712 verdict + token URI for the artist (session wallet) to mint with
    GET  /api/me/artworks          the session wallet's submissions, verdicts, tokens and disputes
    GET  /api/networks             chains the frontend may switch to (public RPC, addresses, ABIs)
    GET  /api/verify/<id>          compare the stored report with the on-chain verdict
    GET  /api/token/[<net>/[<contract>/]]<id>   indexed provenance of a minted token
    GET  /token/<net>/<contract>/<id>          public provenance page (checks the chain itself)
    GET  /api/status               policy, models, networks, indexer, queues
    /api/disputes/...              dispute flow: reporter evidence, reviewer reasons (disputes/api.py)
    /api/explorer/..., /api/governance/...   public explorer + Safe proposals (explorer.py)
    GET  /explorer                 public explorer page (stats, policy history, governance)
    GET  /healthz                  liveness probe

The models run in workers (`python -m jobs.worker`), chain state comes from the indexer
(`python -m indexer`). The server never mints and never pays for mints.
Development:  python server.py            Production:  gunicorn wsgi:app (see Dockerfile)
"""

import hmac
import json
import logging
import mimetypes
import re
import time
import uuid
from decimal import Decimal

from flask import Flask, abort, g, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from web3 import Web3
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import config
import db
import explorer
from chain import ipfs, metadata, networks, registry
from chain.eip712 import Verdict, sign_verdict
from chain.registry import ChainUnavailable
from db import repo
from db.models import IndexerCursor, Token
from detection.policy import Policy
from detection.report_hash import canonical_hash
from disputes import api as disputes_api
from jobs import queue as jq

# Windows can map .js to text/plain via the registry; with nosniff, browsers then refuse ES modules.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mintgate")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES + 64 * 1024
if config.TRUSTED_PROXIES:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.TRUSTED_PROXIES, x_proto=config.TRUSTED_PROXIES)

# Shared by every API instance through Redis. If Redis is down, requests are not blocked.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=config.RATELIMIT_STORAGE_URI,
    storage_options={"socket_connect_timeout": 1},
    strategy="moving-window",
    swallow_errors=True,
    key_prefix="mintgate",
)

app.register_blueprint(auth.bp)
limiter.limit("30/minute")(auth.bp)
app.register_blueprint(disputes_api.bp)
app.register_blueprint(explorer.bp)
limiter.limit("60/minute")(explorer.bp)
limiter.limit("30/minute")(disputes_api.bp)

FRONTEND_DIR = config.PROJECT_DIR / "frontend"
REPORT_ID = re.compile(r"^[0-9a-f]{32}$")
ATTESTATION_VALIDITY = 24 * 3600
POLICY = Policy()


def error(msg, code=400):
    return jsonify({"success": False, "error": msg}), code


def _check_id(rid):
    if not REPORT_ID.match(rid):
        abort(404)
    return rid


# ---- security headers -----------------------------------------------------------------


def _origin(url):
    m = re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else None


def build_csp() -> str:
    """Scripts only from this origin. Besides the API, the browser may read the configured chains'
    public RPCs and the IPFS gateway: the provenance page checks the chain without trusting this API."""
    readable = [n.public_rpc() for n in networks.available().values()] + [config.IPFS_GATEWAY]
    connect = ["'self'"] + sorted({o for o in map(_origin, readable) if o})
    return (
        "default-src 'self'; img-src 'self' data: blob: https:; script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        f"connect-src {' '.join(connect)}; object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'self' https://huggingface.co https://*.hf.space"
    )


CSP = build_csp()


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# ---- static frontend --------------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/token/<network>/<contract>/<int:token_id>")
def token_page(network, contract, token_id):
    return send_from_directory(FRONTEND_DIR, "token.html")


@app.get("/explorer")
def explorer_page():
    return send_from_directory(FRONTEND_DIR, "explorer.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(FRONTEND_DIR, name)


@app.get("/api/reference/<path:name>")
def reference_image(name):
    if name.startswith("minted/"):  # artworks minted through Mint-Gate
        return send_from_directory(config.UPLOADS_DIR, name.removeprefix("minted/"))
    if name.startswith("art/"):  # protected paintings
        return send_from_directory(config.REFERENCE_ART_DIR, name.removeprefix("art/"))
    if name.startswith("nft/"):  # on-chain NFT index: 128 px thumbnails
        return send_from_directory(config.NFT_THUMBS_DIR, name.removeprefix("nft/"))
    return send_from_directory(config.ORIGINALS_DIR, name)


@app.get("/api/upload/<rid>.png")
def uploaded_image(rid):
    return send_from_directory(config.UPLOADS_DIR, f"{_check_id(rid)}.png")


@app.get("/metadata/<rid>.json")
def token_metadata(rid):
    return send_from_directory(config.METADATA_DIR, f"{_check_id(rid)}.json")


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
    ok_db, ok_redis = db.ping(), jq.ping()
    return jsonify({"ok": ok_db and ok_redis, "db": ok_db, "redis": ok_redis}), 200 if ok_db else 503


# ---- status -----------------------------------------------------------------------------


def _json_file(path):
    return json.loads(path.read_text()) if path.exists() else {}


def _chain_overview():
    """Per configured network: addresses + how far the indexer got (from Postgres, no RPC call)."""
    nets = networks.available()
    with db.session() as s:
        cursors = {c.chain_id: c for c in s.scalars(select(IndexerCursor))}
        counts = dict(s.execute(select(Token.chain_id, func.count()).group_by(Token.chain_id)).all())
        references = repo.count_references(s)
    out = {}
    for name, n in nets.items():
        cur = cursors.get(n.chain_id)
        out[name] = {
            **n.public(),
            "indexed_block": cur.block if cur else None,
            "indexed_at": int(cur.updated_at.timestamp()) if cur else None,
            "tokens": counts.get(n.chain_id, 0),
        }
    return out, references


@app.get("/api/status")
def status():
    try:
        nets, minted_refs = _chain_overview()
    except SQLAlchemyError:
        nets, minted_refs = {}, None
    default = nets.get(config.DEFAULT_NETWORK)
    chain = {"connected": False, "error": f"{config.DEFAULT_NETWORK} not deployed / not indexed yet"}
    if default and default["indexed_block"] is not None:
        chain = {
            "connected": True,
            **default,
            "contract": default["nft"],
            "block": default["indexed_block"],
            "total_minted": default["tokens"],
        }
    try:
        queues = {q: jq.queue(q).count for q in (jq.ANALYSIS, jq.CHAIN)}
    except RedisError:
        queues = None
    ai_meta = _json_file(config.AI_DETECTOR_FILE)
    return jsonify(
        {
            "references": {"index": len(_json_file(config.INDEX_FILES) or []), "minted": minted_refs},
            "policy": POLICY.describe(),
            "models": {
                "ai_detector": {
                    "version": ai_meta.get("version"),
                    "backbones": ai_meta.get("backbones"),
                    "metrics": ai_meta.get("metrics"),
                },
                "permissive_only": config.PERMISSIVE_ONLY,
            },
            "ipfs": ipfs.enabled(),
            "ipfs_gateway": config.IPFS_GATEWAY,
            "chain": chain,
            "networks": nets,
            "queues": queues,
        }
    )


# ---- submit + jobs ------------------------------------------------------------------------


@app.post("/api/analyze")
@limiter.limit(lambda: f"{config.RATE_ANALYZE_PER_HOUR}/hour")
@auth.require_session()
def analyze():
    """The artist is the signed-in wallet, and it must have signed the SHA-256 of these exact bytes."""
    f = request.files.get("image")
    if f is None:
        return error("form field 'image' is required")
    raw = f.read()
    if not raw:
        return error("empty file")
    artist = g.wallet
    try:
        claim = auth.verify_claim(
            raw,
            artist,
            g.session["domain"],
            request.form.get("claim_issued_at", ""),
            request.form.get("claim_signature", ""),
        )
    except auth.AuthError as exc:
        return error(str(exc), 401)

    sid = uuid.uuid4().hex
    path = config.INCOMING_DIR / sid
    tmp = path.with_suffix(".part")
    tmp.write_bytes(raw)
    tmp.replace(path)
    try:
        with db.session() as s:
            repo.create_submission(s, sid, artist, f.filename or "", claim=claim)
            repo.audit(
                s, "api", "submission.created", sid, artist=artist, bytes=len(raw), file_sha256=claim["file_sha256"]
            )
    except SQLAlchemyError as exc:
        path.unlink(missing_ok=True)
        log.error("analyze: database: %s", exc)
        return error("database temporarily unavailable", 503)
    try:
        jq.enqueue_analysis(sid)
    except RedisError as exc:
        log.error("analyze: queue: %s", exc)
        with db.session() as s:
            repo.set_status(s, sid, "error", "job queue unavailable")
        path.unlink(missing_ok=True)
        return error("job queue temporarily unavailable", 503)
    return jsonify({"success": True, "job_id": sid, "status": "queued", "status_url": f"/api/jobs/{sid}"}), 202


def _rq_status(jid):
    try:
        return Job.fetch(jid, connection=jq.connection()).get_status()
    except NoSuchJobError:
        return None
    except RedisError:
        return "unknown"


@app.get("/api/jobs/<jid>")
def job_status(jid):
    _check_id(jid)
    with db.session() as s:
        sub = repo.get_submission(s, jid)
        if sub is None:
            abort(404)
        state, err = sub.status, sub.error
        report = repo.report_dict(sub.report) if sub.report else None
    out = {"success": True, "job_id": jid}
    if state in repo.PENDING:
        rq_state = _rq_status(jid)
        if rq_state in (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED):
            return jsonify(out | {"status": "error", "error": "analysis failed - please upload again"})
        if rq_state is None and state == "queued" and (config.INCOMING_DIR / jid).exists():
            jq.enqueue_analysis(jid)  # Redis lost the job (flushed / restarted without AOF): queue it again
        return jsonify(out | {"status": state})
    if state in repo.FAILED:
        return jsonify(out | {"status": "error", "error": err or "analysis failed"})
    return jsonify(out | {"status": "done", "report": report})


# ---- reports --------------------------------------------------------------------------------


def _load_report(rid, with_evidence=False):
    _check_id(rid)
    with db.session() as s:
        rep = repo.get_report(s, rid)
        if rep is None:
            abort(404)
        out = repo.report_dict(rep)
        evidence_uri = rep.evidence_uri
    if with_evidence and evidence_uri:
        path = config.STORAGE_DIR / evidence_uri
        out["evidence"] = json.loads(path.read_text()) if path.exists() else None
    return out


@app.get("/api/report/<rid>")
def get_report(rid):
    return jsonify(_load_report(rid, with_evidence=True))


@app.get("/api/reports")
def list_reports():
    wanted = request.args.get("status")
    with db.session() as s:
        rows = [repo.report_dict(r) for r in repo.list_reports(s, wanted)]
    keys = ("report_id", "filename", "decision", "status", "score", "created_at", "reasons", "report_hash", "artist")
    return jsonify([{k: r.get(k) for k in keys} for r in rows])


@app.post("/api/review/<rid>")
@limiter.limit("30/hour")
def review(rid):
    _check_id(rid)
    body = request.get_json(silent=True) or {}
    actor = _review_actor(body)
    if isinstance(actor, tuple):
        return actor
    approve = bool(body.get("approve"))
    with db.session() as s:
        rep = repo.get_report(s, rid, lock=True)
        if rep is None:
            abort(404)
        sub = rep.submission
        if sub.status != "REVIEW":
            return error(f"report status is {sub.status}, not REVIEW", 409)
        sub.status = "REVIEW_APPROVED" if approve else "REVIEW_REJECTED"
        rep.review = {
            "approved": approve,
            "note": str(body.get("note", ""))[:500],
            "at": int(time.time()),
            "reviewer": actor,
        }
        repo.audit(s, actor, "review.resolved", rid, approved=approve)
        new_status, review_info, has_artist = sub.status, rep.review, sub.artist is not None
    job_id = _queue_review(rid, approve) if has_artist else None
    return jsonify({"success": True, "status": new_status, "review": review_info, "chain_job_id": job_id})


def _review_actor(body):
    """Phase 6: a signed-in wallet with REVIEWER_ROLE on the default chain's registry (CSRF-checked).
    The shared REVIEWER_KEY still works for scripts, unless REVIEWER_KEY_DISABLED=1."""
    sess = auth.current_session()
    if sess is not None:
        sent = request.headers.get(auth.CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), sess["csrf"].encode()):
            return error("CSRF check failed - reload the page", 403)
        net = networks.available().get(config.DEFAULT_NETWORK)
        try:
            ok = net is not None and disputes_api.is_reviewer(net, sess["address"])
        except Exception as exc:
            log.warning("review: role check: %s", exc)
            return error("blockchain temporarily unavailable", 503)
        if ok:
            return sess["address"]
        if "reviewer_key" not in body:
            return error(f"{sess['address']} does not hold REVIEWER_ROLE on {config.DEFAULT_NETWORK}", 403)
    key = str(body.get("reviewer_key", ""))
    if config.REVIEWER_KEY_DISABLED or not hmac.compare_digest(key.encode(), config.REVIEWER_KEY.encode()):
        return error("invalid reviewer key", 403)
    return "reviewer"


def _queue_review(rid, approve):
    if config.DEFAULT_NETWORK not in networks.available():
        return None
    try:
        return jq.enqueue_chain("resolve_review", rid, approve, config.DEFAULT_NETWORK).id
    except RedisError as exc:  # retried by /api/attest
        log.warning("review %s: could not queue the chain update: %s", rid, exc)
        return None


# ---- attestation ------------------------------------------------------------------------------


@app.post("/api/attest/<rid>")
@limiter.limit(lambda: f"{config.RATE_ATTEST_PER_DAY}/day")
@auth.require_session()
def attest(rid):
    """Everything the artist's wallet needs to mint: a signed verdict (PASS) or the on-chain
    approved review (REVIEW_APPROVED), plus the token URI. The artist pays the gas."""
    _check_id(rid)
    body = request.get_json(silent=True) or {}
    try:
        net = networks.get(str(body.get("network") or config.DEFAULT_NETWORK))
    except networks.UnknownNetwork:
        return error(f"unknown network; available: {', '.join(networks.available()) or 'none'}")
    key = net.signer_key()
    if not key:
        return error("attestation signer is not configured", 503)

    report = _load_report(rid)
    if report["status"] not in repo.MINTABLE:
        return error(f"minting blocked by Mint-Gate policy: status {report['status']}", 403)
    if not report["artist"]:
        return error("this report has no artist wallet", 409)
    if report["artist"] != g.wallet:
        return error("only the artist's wallet can mint this artwork - sign in with it", 403)
    reviewed_on = (report["review"] or {}).get("network", config.DEFAULT_NETWORK)
    if report["status"] == "REVIEW_APPROVED" and net.name != reviewed_on:
        return error(f"this verdict was reviewed on {reviewed_on}; mint there", 409)
    publication = report["publication"]
    if publication is None:
        try:
            publication = metadata.publish(report)
        except ipfs.IPFSError as exc:
            log.error("ipfs: %s", exc)
            return error("could not store the artwork on IPFS - try again", 502)

    with db.session() as s:
        rep = repo.get_report(s, rid, lock=True)
        if rep.submission.status not in repo.MINTABLE:  # changed meanwhile (e.g. minted)
            return error(f"minting blocked by Mint-Gate policy: status {rep.submission.status}", 403)
        rep.publication = rep.publication or publication
        att = repo.usable_attestation(s, rid, net.chain_id, net.registry.address)
        if att is None and rep.submission.status == "PASS":
            v = Verdict.from_report(report, report["artist"], ATTESTATION_VALIDITY)
            att = repo.add_attestation(
                s, rid, net.chain_id, net.registry.address, v, sign_verdict(v, key, net.chain_id, net.registry.address)
            )
            repo.audit(s, "api", "attestation.issued", rid, chain_id=net.chain_id, nonce=str(v.nonce))
        token_uri = rep.publication["token_uri"]
        review_tx = (rep.review or {}).get("tx_hash") or (rep.review or {}).get("already")
        verdict, signature = (att.verdict, att.signature) if att else (None, None)
        expires_at = int(att.expires_at.timestamp()) if att else None

    out = {
        "success": True,
        **net.public(),
        "artist": report["artist"],
        "token_uri": token_uri,
        "links": {"image": publication["image"], "report": publication["report"]},
        "max_royalty_bps": 1000,
    }
    if report["status"] == "PASS":
        # MintGateNFTV2.submitAndMint(verdict, signature, uri, royaltyBps), sent by the artist wallet
        return jsonify(
            out
            | {
                "call": "submitAndMint",
                "ready": True,
                "verdict": verdict,
                "signature": signature,
                "expires_at": expires_at,
            }
        )
    if not review_tx:
        _queue_review(rid, True)  # idempotent: anchors + resolves if that has not happened yet
    # REVIEW_APPROVED: the verdict is on chain already -> MintGateNFTV2.mint(imageHash, uri, royaltyBps)
    return jsonify(out | {"call": "mint", "ready": bool(review_tx), "image_hash": "0x" + report["image_sha256"]})


# ---- chain reads ------------------------------------------------------------------------------


@app.get("/api/verify/<rid>")
def verify(rid):
    """Tamper check: recompute the stored report's hash and compare with the on-chain verdict."""
    report = _load_report(rid)
    recomputed = canonical_hash(report)
    on = next((a for a in report["attestations"] if a["submitted_tx"]), None)
    net = (networks.by_chain_id(on["chain_id"]) if on else None) or networks.available().get(config.DEFAULT_NETWORK)
    if net is None:
        return error("no network configured", 503)
    try:
        onchain = registry.client(net, jq.connection()).verdict_of(report["report_hash"])
    except Exception as exc:  # node down, RPC error
        log.warning("verify: %s", exc)
        return error("blockchain temporarily unavailable", 503)
    return jsonify(
        {
            "network": net.name,
            "report_hash": report["report_hash"],
            "recomputed_hash": recomputed,
            "report_untampered": recomputed == report["report_hash"],
            "on_chain": onchain,
            "on_chain_matches": onchain["decision"] != "NONE"
            and onchain["image_sha256"] == report["image_sha256"]
            and onchain["score"] == report["score"],
        }
    )


@app.get("/api/networks")
def list_networks():
    """Chains the frontend can use; `?abi=1` adds the registry + NFT ABIs (to decode custom errors)."""
    with_abi = request.args.get("abi") == "1"
    out = []
    for n in networks.available().values():
        d = n.browser()
        if with_abi:
            d["abi"] = {"registry": n.registry.abi, "nft": n.nft.abi}
        out.append(d)
    return jsonify({"default": config.DEFAULT_NETWORK, "networks": out})


@app.get("/api/me/artworks")
@auth.require_session(csrf=False)
def my_artworks():
    with db.session() as s:
        rows = repo.artworks_of(s, g.wallet)
    names = {n.chain_id: n.name for n in networks.available().values()}
    for r in rows:
        for item in r["tokens"] + r["disputes"]:
            item["network"] = names.get(item["chain_id"])
    return jsonify({"success": True, "wallet": g.wallet, "artworks": rows})


@app.get("/api/token/<int:token_id>")
@app.get("/api/token/<network>/<int:token_id>")
@app.get("/api/token/<network>/<contract>/<int:token_id>")
def token(token_id, network=None, contract=None):
    try:
        net = networks.resolve(network or config.DEFAULT_NETWORK)
    except networks.UnknownNetwork:
        abort(404)
    if contract is not None and not Web3.is_address(contract):
        abort(404)
    address = Web3.to_checksum_address(contract or net.nft.address)
    with db.session() as s:
        t = s.get(Token, (net.chain_id, address, Decimal(token_id)))
        if t is None:
            return error("token not found", 404)
        rep = repo.get_report_by_hash(s, t.report_hash)
        out = {
            **net.browser(),
            "contract": t.contract,
            "token_id": int(t.token_id),
            "owner": t.owner,
            "artist": t.artist,
            "status": t.status,
            "image_sha256": t.image_sha256,
            "report_hash": t.report_hash,
            "minted_block": t.minted_block,
            "minted_tx": t.minted_tx,
            "report_id": rep.id if rep else None,
        }
        if rep:
            out["verification"] = {"decision": rep.decision, "score": rep.score, "policy_version": rep.policy_version}
            out["token_uri"] = (rep.publication or {}).get("token_uri")
    if out.get("token_uri"):
        out["token_uri_http"] = ipfs.gateway_url(out["token_uri"])
    return jsonify(out)


@app.errorhandler(413)
def too_large(_):
    return error("file too large", 413)


@app.errorhandler(429)
def rate_limited(_):
    return error("rate limit reached - try again later", 429)


@app.errorhandler(ChainUnavailable)
def chain_down(exc):
    log.warning("chain: %s", exc)
    return error("blockchain temporarily unavailable", 503)


@app.errorhandler(500)
def internal(_):
    return error("internal error", 500)


if __name__ == "__main__":
    # Development server, bound to localhost only. Production uses gunicorn (Dockerfile).
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
