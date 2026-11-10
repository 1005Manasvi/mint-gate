"""Reviewer notifications: a Discord webhook post on new disputes and new REVIEW items.

    DISCORD_WEBHOOK_URL   the channel webhook (secret: never logged)
    NOTIFY_DISPUTES=1     post when a dispute is opened on chain (indexer)
    NOTIFY_REVIEWS=1      post when an upload gets a REVIEW verdict (analysis worker)

Posts run as jobs on the `chain` queue (retried with back-off), so a Discord outage never blocks
the indexer or the analysis. Messages hold public facts only (ids, hashes, links).
"""

import logging

import requests

import config

log = logging.getLogger("mintgate.notify")
MAX_LEN = 1900  # Discord: 2000 characters per message


def enabled(kind: str) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        return False
    return {"dispute": config.NOTIFY_DISPUTES, "review": config.NOTIFY_REVIEWS}.get(kind, False)


def post(text: str) -> bool:
    """Send one message. Raises on HTTP errors so the job is retried; returns False when disabled."""
    url = config.DISCORD_WEBHOOK_URL
    if not url:
        return False
    r = requests.post(url, json={"content": text[:MAX_LEN], "allowed_mentions": {"parse": []}}, timeout=10)
    if r.status_code == 429 or r.status_code >= 500:
        raise RuntimeError(f"discord webhook: HTTP {r.status_code} (will retry)")
    if r.status_code >= 400:  # bad / deleted webhook: retrying will not help
        log.error("discord webhook rejected the message: HTTP %s", r.status_code)
        return False
    return True


def dispute_text(network: str, d: dict) -> str:
    base = config.PUBLIC_BASE_URL
    lines = [
        f"**New dispute #{d['dispute_id']}** on {network}",
        f"Image `{d['image_sha256'][:16]}…` · disputant `{d['disputant']}` · bond {int(d['bond']) / 1e18:g} ETH",
        f"Evidence: {d['evidence_uri']}",
    ]
    if d.get("token"):
        t = d["token"]
        lines.append(f"Token: {base}/token/{network}/{t['contract']}/{t['token_id']}")
    lines.append(f"Review: {base}/?view=disputes&dispute={network}:{d['dispute_id']}")
    return "\n".join(lines)


def review_text(report: dict) -> str:
    reasons = "; ".join(report.get("reasons") or [])[:400]
    return "\n".join(
        [
            f"**REVIEW needed** for upload `{report['report_id']}` (score {report['score']}/100)",
            f"Reasons: {reasons or 'see report'}",
            f"Open: {config.PUBLIC_BASE_URL}/?report={report['report_id']}",
        ]
    )
