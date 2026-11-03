"""Wallet identity (Phase 3): SIWE verification, sessions, CSRF, and the wallet-scoped endpoints."""

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

import auth
from tests.conftest import ARTIST, ARTIST_KEY, OTHER, OTHER_KEY, SIWE_DOMAIN, now_iso, sign_text, siwe_text

# ---- SIWE message verification (unit level: no Flask) --------------------------------------------


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def issue(redis, nonce="abcdef0123456789"):
    redis.set(auth.NONCE_PREFIX + nonce, "1", ex=300)
    return nonce


def signed(text, key=ARTIST_KEY):
    return text, sign_text(key, text)


def test_valid_message_signs_in_once(redis):
    text, sig = signed(siwe_text(ARTIST, issue(redis)))
    msg = auth.verify_siwe(text, sig, redis)
    assert (msg.address, msg.domain, msg.chain_id) == (ARTIST, SIWE_DOMAIN, 31337)
    with pytest.raises(auth.AuthError, match="already used nonce"):  # replay of the same message
        auth.verify_siwe(text, sig, redis)


def test_unknown_nonce_is_rejected(redis):
    text, sig = signed(siwe_text(ARTIST, "neverissued123"))
    with pytest.raises(auth.AuthError, match="nonce"):
        auth.verify_siwe(text, sig, redis)


def test_expired_messages_are_rejected(redis):
    old = siwe_text(ARTIST, issue(redis), issued_at=now_iso(-11 * 60))  # older than SIWE_MAX_AGE_S
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_siwe(*signed(old), redis)
    past = datetime.now(UTC) - timedelta(seconds=1)
    short = siwe_text(ARTIST, issue(redis), expiration_time=past)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_siwe(*signed(short), redis)
    assert redis.exists(auth.NONCE_PREFIX + "abcdef0123456789")  # a rejected message does not burn the nonce


def test_time_window_edges(redis):
    with pytest.raises(auth.AuthError, match="future"):
        auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis), issued_at=now_iso(300))), redis)
    later = datetime.now(UTC) + timedelta(minutes=5)
    with pytest.raises(auth.AuthError, match="not valid yet"):
        auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis), not_before=later)), redis)
    soon = datetime.now(UTC) + timedelta(minutes=5)
    assert auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis), expiration_time=soon)), redis)


def test_wrong_domain_or_uri_is_rejected(redis):
    phishing = siwe_text(ARTIST, issue(redis), domain="mint-gate.evil.example")
    with pytest.raises(auth.AuthError, match="wrong domain"):
        auth.verify_siwe(*signed(phishing), redis)
    other_uri = siwe_text(ARTIST, issue(redis), uri="https://evil.example/login")
    with pytest.raises(auth.AuthError, match="URI"):
        auth.verify_siwe(*signed(other_uri), redis)


def test_wrong_signer_chain_or_version(redis):
    with pytest.raises(auth.AuthError, match="signature does not match"):
        auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis)), key=OTHER_KEY), redis)
    with pytest.raises(auth.AuthError, match="invalid signature"):
        auth.verify_siwe(siwe_text(ARTIST, issue(redis)), "0x1234", redis)
    with pytest.raises(auth.AuthError, match="chain 1 is not supported"):
        auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis), chain_id=1)), redis)
    with pytest.raises(auth.AuthError, match="version"):
        auth.verify_siwe(*signed(siwe_text(ARTIST, issue(redis), version="2")), redis)


def test_parser_follows_eip4361():
    full = siwe_text(
        ARTIST,
        "n0nce12345678",
        statement=None,
        expiration_time=datetime(2030, 1, 1, tzinfo=UTC),
        request_id="req-1",
        resources=("ipfs://bafy", "https://example.com/terms"),
    )
    assert f"{ARTIST}\n\n\nURI:" in full  # no statement: three newlines after the address
    msg = auth.parse_siwe(full)
    assert msg.statement is None and msg.request_id == "req-1"
    assert msg.resources == ("ipfs://bafy", "https://example.com/terms")
    assert msg.text() == full  # round trip
    assert auth.parse_siwe(full.replace("\n", "\r\n")) == msg
    with pytest.raises(auth.AuthError, match="checksummed"):
        auth.parse_siwe(full.replace(ARTIST, ARTIST.lower()))
    for broken in ("hello", full.replace("Version: 1", "Version 1"), full.replace("Nonce: n0nce12345678", "Nonce: x")):
        with pytest.raises(auth.AuthError):
            auth.parse_siwe(broken)
    with pytest.raises(auth.AuthError, match="time zone"):
        auth.parse_siwe(full.replace("2030-01-01T00:00:00Z", "2030-01-01T00:00:00"))


def test_loopback_alias_is_allowed(monkeypatch):
    import config

    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://127.0.0.1:5000")
    monkeypatch.delenv("SIWE_DOMAINS", raising=False)
    assert config._siwe_domains() == ["127.0.0.1:5000", "localhost:5000"]
    monkeypatch.setenv("SIWE_DOMAINS", "mintgate.example, www.mintgate.example")
    assert config._siwe_domains() == ["mintgate.example", "www.mintgate.example"]


# ---- sessions over HTTP --------------------------------------------------------------------------


def test_sign_in_sets_a_strict_session_cookie(server, wallet, fake_redis):
    w = wallet(login=False)
    resp = w.login()
    assert resp.status_code == 200 and resp.get_json()["address"] == ARTIST
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"{auth.COOKIE}=") and auth.COOKIE == "__Host-mg_session"
    for flag in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=3600"):
        assert flag in cookie
    me = w.get("/api/auth/me").get_json()
    assert me["address"] == ARTIST and me["csrf"] == w.csrf
    [key] = fake_redis.keys(auth.SESSION_PREFIX + "*")
    assert 3500 < fake_redis.ttl(key) <= 3600  # short-lived, server-side
    assert w.http.get_cookie(auth.COOKIE).value not in key.decode()  # only the token's hash is stored


def test_nonce_endpoint_issues_single_use_nonces(server, client, fake_redis):
    a, b = (client.get("/api/auth/nonce").get_json()["nonce"] for _ in range(2))
    assert a != b and len(a) == 32
    assert 0 < fake_redis.ttl(auth.NONCE_PREFIX + a) <= 300
    text = siwe_text(ARTIST, a)
    first = client.post("/api/auth/verify", json={"message": text, "signature": sign_text(ARTIST_KEY, text)})
    again = client.post("/api/auth/verify", json={"message": text, "signature": sign_text(ARTIST_KEY, text)})
    assert first.status_code == 200 and again.status_code == 401


def test_failed_sign_in(server, client, wallet):
    assert client.post("/api/auth/verify", json={}).status_code == 401
    assert wallet(login=False).login(domain="evil.example").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    foreign = client.post("/api/auth/verify", json={}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403


def test_csrf_and_origin_are_checked(server, wallet, fixture_bytes):
    w = wallet()
    w.csrf = "forged"
    resp = w.upload(fixture_bytes("photo.png"))
    assert resp.status_code == 403 and "CSRF" in resp.get_json()["error"]
    w = wallet()
    resp = w.post("/api/attest/" + "0" * 32, json={}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    same_site = w.post("/api/auth/logout", headers={"Origin": f"http://{SIWE_DOMAIN}"})
    assert same_site.status_code == 200


def test_logout_ends_the_session(server, wallet, fake_redis):
    w = wallet()
    assert w.post("/api/auth/logout").status_code == 200
    assert fake_redis.keys(auth.SESSION_PREFIX + "*") == []
    assert w.get("/api/auth/me").status_code == 401


def test_expired_session_is_gone(server, wallet, fake_redis):
    w = wallet()
    fake_redis.flushall()  # TTL elapsed
    assert w.get("/api/me/artworks").status_code == 401


# ---- wallet-scoped endpoints ----------------------------------------------------------------------


def test_only_the_artist_wallet_gets_the_attestation(server, wallet, fixture_bytes, run_jobs):
    artist, other = wallet(ARTIST_KEY), wallet(OTHER_KEY)
    rid = artist.upload(fixture_bytes("photo.png")).get_json()["job_id"]
    run_jobs()
    assert server.app.test_client().post(f"/api/attest/{rid}", json={}).status_code == 401
    stolen = other.post(f"/api/attest/{rid}", json={})
    assert stolen.status_code == 403 and "artist's wallet" in stolen.get_json()["error"]
    assert artist.post(f"/api/attest/{rid}", json={}).status_code == 200


def test_my_artworks(server, wallet, fixture_bytes, run_jobs):
    from chain import networks
    from indexer.core import Indexer

    artist, other = wallet(ARTIST_KEY), wallet(OTHER_KEY)
    assert artist.get("/api/me/artworks").get_json()["artworks"] == []  # empty state
    rid = artist.upload(fixture_bytes("photo.png"), "mine.png").get_json()["job_id"]
    other.upload(fixture_bytes("painting.png"), "theirs.png")
    run_jobs()
    report = artist.get(f"/api/report/{rid}").get_json()
    net = networks.get("localtest")
    ix = Indexer(net, w3=server.Web3())
    with server.db.session() as s:
        ix.apply(
            s,
            {
                "block": 7,
                "tx": "0x" + "cd" * 32,
                "contract": net.registry.address,
                "event": "ImageMinted",
                "args": {
                    "imageHash": "0x" + report["image_sha256"],
                    "reportHash": "0x" + report["report_hash"],
                    "collection": server.Web3.to_checksum_address(net.nft.address),
                    "tokenId": 1,
                    "artist": ARTIST,
                },
            },
        )
    mine = artist.get("/api/me/artworks").get_json()
    assert mine["wallet"] == ARTIST
    [row] = mine["artworks"]  # the other wallet's upload is not listed
    assert row["filename"] == "mine.png" and row["status"] == "MINTED" and row["signed"]
    assert row["decision"] == "PASS" and row["report_hash"] == report["report_hash"]
    [tok] = row["tokens"]
    assert tok["token_id"] == "1" and tok["network"] == "localtest" and tok["status"] == "active"
    assert row["disputes"] == []
    assert [r["filename"] for r in other.get("/api/me/artworks").get_json()["artworks"]] == ["theirs.png"]
    assert OTHER != ARTIST

    # the indexed token, addressed like the public provenance page: /token/<chain>/<contract>/<id>
    for ref in ("localtest", "31337"):
        t = artist.get(f"/api/token/{ref}/{net.nft.address.lower()}/1").get_json()
        assert t["report_id"] == rid and "rpc_url" in t and t["registry"] == net.registry.address
    assert artist.get(f"/api/token/localtest/{'0x' + '99' * 20}/1").status_code == 404
    assert artist.get("/api/token/localtest/not-an-address/1").status_code == 404
    page = artist.get(f"/token/localtest/{net.nft.address}/1")
    assert page.status_code == 200 and b'src="/static/js/token.js"' in page.data


def test_networks_endpoint_and_csp(server, client, monkeypatch):
    nets = client.get("/api/networks").get_json()
    [n] = nets["networks"]
    assert nets["default"] == "localtest"
    assert n["chain_id"] == 31337 and n["native_currency"]["symbol"] == "ETH" and "abi" not in n
    full = client.get("/api/networks?abi=1").get_json()["networks"][0]
    assert any(e.get("name") == "NotMintable" for e in full["abi"]["registry"])
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "unsafe-inline" not in csp
    assert "connect-src 'self' " in csp and "https://gateway.pinata.cloud" in csp

    # RPC_URL_<NET> may hold an API key: the browser only ever sees PUBLIC_RPC_<NET>
    monkeypatch.setenv("RPC_URL_LOCALTEST", "https://rpc.example/v2/SECRET")
    monkeypatch.setenv("PUBLIC_RPC_LOCALTEST", "https://public.example")
    [n] = client.get("/api/networks").get_json()["networks"]
    assert n["rpc_url"] == "https://public.example" and "SECRET" not in server.build_csp()
    assert "https://public.example" in server.build_csp()
