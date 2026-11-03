"""Wallet identity: Sign-In with Ethereum (EIP-4361), sessions, CSRF, signed submissions.

    GET  /api/auth/nonce   single-use nonce (Redis, 5 min)
    POST /api/auth/verify  SIWE message + signature -> HttpOnly, Secure, SameSite=Strict cookie
    GET  /api/auth/me      the session wallet + its CSRF token
    POST /api/auth/logout

Sessions live in Redis (any number of API instances), keyed by the SHA-256 of the cookie
value. Every state-changing request with a session also needs the `X-CSRF-Token` header and,
when the browser sends one, an allowed `Origin`. Only EOA signatures (EIP-191) are accepted;
smart-contract wallets (EIP-1271) are not supported yet.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Blueprint, g, jsonify, request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from web3 import Web3

import config
import db
from chain import networks
from db import repo
from jobs import queue as jq

bp = Blueprint("auth", __name__)

NONCE_PREFIX = "mintgate:siwe:nonce:"
SESSION_PREFIX = "mintgate:session:"
COOKIE = "__Host-mg_session" if config.COOKIE_SECURE else "mg_session"  # __Host- requires Secure
CSRF_HEADER = "X-CSRF-Token"
CLOCK_SKEW_S = 60
STATEMENT = "Sign in to Mint-Gate to submit and mint artwork from this wallet."

# EIP-4361 message grammar (version 1). Without a statement the address is followed by three newlines.
SIWE_RE = re.compile(
    r"^(?:(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://)?(?P<domain>[^ /?#\n]+)"
    r" wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[a-fA-F0-9]{40})\n\n"
    r"(?:(?P<statement>[^\n]+)\n)?\n"
    r"URI: (?P<uri>[^\n]+)\n"
    r"Version: (?P<version>[^\n]+)\n"
    r"Chain ID: (?P<chain_id>[0-9]+)\n"
    r"Nonce: (?P<nonce>[a-zA-Z0-9]{8,})\n"
    r"Issued At: (?P<issued_at>[^\n]+)"
    r"(?:\nExpiration Time: (?P<expiration_time>[^\n]+))?"
    r"(?:\nNot Before: (?P<not_before>[^\n]+))?"
    r"(?:\nRequest ID: (?P<request_id>[^\n]*))?"
    r"(?:\nResources:(?P<resources>(?:\n- [^\n]+)+))?$"
)


class AuthError(ValueError):
    """Rejected sign-in or claim; the message is safe to show to the user."""


@dataclass(frozen=True)
class SiweMessage:
    domain: str
    address: str
    statement: str | None
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime | None = None
    not_before: datetime | None = None
    request_id: str | None = None
    resources: tuple = ()
    scheme: str | None = None

    def text(self) -> str:
        """The EIP-4361 message (inverse of `parse_siwe`; frontend/js/wallet.js builds the same text)."""

        def ts(t):
            return t.isoformat().replace("+00:00", "Z")

        head = f"{self.scheme}://{self.domain}" if self.scheme else self.domain
        lines = [f"{head} wants you to sign in with your Ethereum account:", self.address, ""]
        lines += [self.statement, ""] if self.statement else [""]
        lines += [
            f"URI: {self.uri}",
            f"Version: {self.version}",
            f"Chain ID: {self.chain_id}",
            f"Nonce: {self.nonce}",
            f"Issued At: {ts(self.issued_at)}",
        ]
        if self.expiration_time:
            lines.append(f"Expiration Time: {ts(self.expiration_time)}")
        if self.not_before:
            lines.append(f"Not Before: {ts(self.not_before)}")
        if self.request_id is not None:
            lines.append(f"Request ID: {self.request_id}")
        if self.resources:
            lines += ["Resources:"] + [f"- {r}" for r in self.resources]
        return "\n".join(lines)


def _time(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuthError(f"{name} is not an RFC 3339 timestamp") from exc
    if t.tzinfo is None:
        raise AuthError(f"{name} needs a time zone")
    return t


def parse_siwe(text: str) -> SiweMessage:
    m = SIWE_RE.match(text.replace("\r\n", "\n"))
    if m is None:
        raise AuthError("not a valid Sign-In with Ethereum message")
    d = m.groupdict()
    if Web3.to_checksum_address(d["address"]) != d["address"]:
        raise AuthError("address must be EIP-55 checksummed")
    resources = tuple(r[2:] for r in (d["resources"] or "").split("\n") if r)
    return SiweMessage(
        domain=d["domain"],
        address=d["address"],
        statement=d["statement"],
        uri=d["uri"],
        version=d["version"],
        chain_id=int(d["chain_id"]),
        nonce=d["nonce"],
        issued_at=_time(d["issued_at"], "Issued At"),
        expiration_time=_time(d["expiration_time"], "Expiration Time"),
        not_before=_time(d["not_before"], "Not Before"),
        request_id=d["request_id"],
        resources=resources,
        scheme=d["scheme"],
    )


def recover(message: str, signature: str) -> str:
    """EIP-191 personal_sign signer."""
    try:
        return Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as exc:  # malformed signature: wrong length, bad v, not on the curve
        raise AuthError("invalid signature") from exc


def allowed_domains() -> set[str]:
    return set(config.SIWE_DOMAINS)


def verify_siwe(text: str, signature: str, redis, now: datetime | None = None) -> SiweMessage:
    """Every EIP-4361 check, then use up the nonce. Raises AuthError."""
    msg = parse_siwe(text)
    now = now or datetime.now(UTC)
    if msg.version != "1":
        raise AuthError("unsupported SIWE version")
    if msg.domain not in allowed_domains():
        raise AuthError(f"wrong domain {msg.domain!r}")
    uri = urlsplit(msg.uri)
    if uri.netloc != msg.domain or uri.scheme not in ("http", "https"):
        raise AuthError("URI does not belong to the signed domain")
    if msg.scheme and msg.scheme != uri.scheme:
        raise AuthError("scheme does not match the URI")
    if msg.chain_id not in {n.chain_id for n in networks.available().values()}:
        raise AuthError(f"chain {msg.chain_id} is not supported")
    age = (now - msg.issued_at).total_seconds()
    if age < -CLOCK_SKEW_S:
        raise AuthError("message issued in the future")
    if age > config.SIWE_MAX_AGE_S:
        raise AuthError("message expired - sign in again")
    if msg.expiration_time and msg.expiration_time <= now:
        raise AuthError("message expired - sign in again")
    if msg.not_before and msg.not_before > now + timedelta(seconds=CLOCK_SKEW_S):
        raise AuthError("message not valid yet")
    if recover(text, signature) != msg.address:
        raise AuthError("signature does not match the address")
    if not redis.getdel(NONCE_PREFIX + msg.nonce):  # atomic: a nonce works exactly once
        raise AuthError("unknown or already used nonce")
    return msg


# ---- signed submissions -------------------------------------------------------------------


def claim_message(domain: str, wallet: str, file_sha256: str, issued_at: str) -> str:
    """What the artist signs (personal_sign) when uploading: binds the exact file bytes to the wallet.
    Must match `claimMessage` in frontend/js/wallet.js."""
    return (
        "Mint-Gate artwork submission\n"
        "I submit this artwork for verification and claim to be its creator.\n\n"
        f"Domain: {domain}\n"
        f"Wallet: {wallet}\n"
        f"File SHA-256: 0x{file_sha256}\n"
        f"Issued At: {issued_at}"
    )


def verify_claim(raw: bytes, wallet: str, domain: str, issued_at: str, signature: str, now=None) -> dict:
    t = _time(issued_at, "claim time")
    now = now or datetime.now(UTC)
    if abs((now - t).total_seconds()) > config.SIWE_MAX_AGE_S:
        raise AuthError("submission signature expired - sign again")
    file_sha256 = hashlib.sha256(raw).hexdigest()
    text = claim_message(domain, wallet, file_sha256, issued_at)
    if recover(text, signature) != wallet:
        raise AuthError("the submission was not signed by the signed-in wallet")
    return {"file_sha256": file_sha256, "message": text, "signature": signature}


# ---- sessions -------------------------------------------------------------------------------


def _key(token: str) -> str:
    return SESSION_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def current_session() -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        raw = jq.connection().get(_key(token))
    except RedisError:
        return None
    return json.loads(raw) if raw else None


def _origin_ok() -> bool:
    origin = request.headers.get("Origin")
    return origin is None or urlsplit(origin).netloc in allowed_domains()


def require_session(csrf: bool = True):
    """Sets g.session / g.wallet; 401 without a session, 403 on a CSRF failure."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            sess = current_session()
            if sess is None:
                return _error("sign in with your wallet first", 401)
            if csrf:
                sent = request.headers.get(CSRF_HEADER, "")
                if not _origin_ok() or not hmac.compare_digest(sent.encode(), sess["csrf"].encode()):
                    return _error("CSRF check failed - reload the page", 403)
            g.session, g.wallet = sess, sess["address"]
            return fn(*args, **kwargs)

        return wrapper

    return deco


def _error(msg, code):
    return jsonify({"success": False, "error": msg}), code


def _public(sess: dict) -> dict:
    return {
        "success": True,
        "address": sess["address"],
        "chain_id": sess["chain_id"],
        "csrf": sess["csrf"],
        "expires_at": sess["expires_at"],
    }


@bp.get("/api/auth/nonce")
def nonce():
    n = secrets.token_hex(16)
    try:
        jq.connection().set(NONCE_PREFIX + n, "1", ex=config.SIWE_NONCE_TTL_S)
    except RedisError:
        return _error("session store unavailable", 503)
    resp = jsonify({"success": True, "nonce": n, "statement": STATEMENT, "domains": sorted(allowed_domains())})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/api/auth/verify")
def verify():
    if not _origin_ok():
        return _error("request from a foreign origin", 403)
    body = request.get_json(silent=True) or {}
    message, signature = str(body.get("message", "")), str(body.get("signature", ""))
    try:
        msg = verify_siwe(message, signature, jq.connection())
    except AuthError as exc:
        return _error(str(exc), 401)
    except RedisError:
        return _error("session store unavailable", 503)
    token = secrets.token_urlsafe(32)
    sess = {
        "address": msg.address,
        "chain_id": msg.chain_id,
        "domain": msg.domain,
        "csrf": secrets.token_urlsafe(24),
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + config.SESSION_TTL_S,
    }
    jq.connection().set(_key(token), json.dumps(sess), ex=config.SESSION_TTL_S)
    _audit("auth.signin", msg.address, chain_id=msg.chain_id)
    resp = jsonify(_public(sess))
    resp.set_cookie(
        COOKIE,
        token,
        max_age=config.SESSION_TTL_S,
        secure=config.COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return resp


@bp.get("/api/auth/me")
def me():
    sess = current_session()
    if sess is None:
        return _error("not signed in", 401)
    resp = jsonify(_public(sess))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/api/auth/logout")
@require_session()
def logout():
    jq.connection().delete(_key(request.cookies[COOKIE]))
    resp = jsonify({"success": True})
    resp.delete_cookie(COOKIE, path="/", secure=config.COOKIE_SECURE, httponly=True, samesite="Strict")
    return resp


def _audit(action, target, **meta):
    try:
        with db.session() as s:
            repo.audit(s, "api", action, target, **meta)
    except SQLAlchemyError:  # the audit trail must not block sign-in
        pass
