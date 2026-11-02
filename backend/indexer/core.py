"""Chain indexer: registry + NFT events -> `chain_events` -> projections (`tokens`, `disputes`, ...).

Per network, one pass:
  1. reorg check: if the stored cursor block's hash changed, rewind REWIND_BLOCKS, delete those
     events and rebuild the projections from the remaining events;
  2. fetch logs in chunks up to `head - confirmations`, insert them (idempotent: unique
     chain_id + tx + log_index) and apply them to the projections in (block, log_index) order,
     advancing the cursor in the same transaction.

The projections are a pure function of `chain_events`, so `--from-block` (delete + re-fetch)
reproduces the on-chain state from scratch.
"""

import logging
from decimal import Decimal

from eth_utils import event_abi_to_log_topic
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from web3 import Web3
from web3.exceptions import BlockNotFound

import db
from chain.networks import Network
from db import repo
from db.models import ChainEvent, Dispute, IndexerCursor, Report, Submission, Token

log = logging.getLogger("mintgate.indexer")
REWIND_BLOCKS = 64
REGISTRY_EVENTS = (
    "VerdictSubmitted",
    "ReviewResolved",
    "VerdictVoided",
    "ImageMinted",
    "DisputeOpened",
    "DisputeResolved",
    "ImageRevoked",
)
NFT_EVENTS = ("VerifiedMint", "Transfer")
ZERO = "0x" + "00" * 20


def _jsonable(v):
    if isinstance(v, bytes):
        return "0x" + v.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _h(v: str) -> str:
    """bytes32 event arg -> report-style hex (no 0x)."""
    return v.removeprefix("0x")


class Indexer:
    def __init__(self, net: Network, w3: Web3 | None = None, chunk: int = 2000):
        self.net = net
        self.w3 = w3 or Web3(Web3.HTTPProvider(net.rpc_url, request_kwargs={"timeout": 30}))
        self.chunk = chunk
        self.registry = Web3.to_checksum_address(net.registry.address)
        self.nft = Web3.to_checksum_address(net.nft.address)
        self._decoders = {}
        for address, abi, names in (
            (self.registry, net.registry.abi, REGISTRY_EVENTS),
            (self.nft, net.nft.abi, NFT_EVENTS),
        ):
            contract = self.w3.eth.contract(address=address, abi=abi)
            for item in abi:
                if item.get("type") == "event" and item["name"] in names:
                    topic = "0x" + event_abi_to_log_topic(item).hex()
                    self._decoders[(address, topic)] = getattr(contract.events, item["name"])()

    # ---- cursor ------------------------------------------------------------------------------

    def _cursor(self, s) -> IndexerCursor:
        cur = s.get(IndexerCursor, self.net.chain_id, with_for_update=True)
        if cur is None:
            cur = IndexerCursor(chain_id=self.net.chain_id, network=self.net.name, block=self.net.deploy_block - 1)
            s.add(cur)
            s.flush()
        return cur

    def _block_hash(self, n: int) -> str:
        return "0x" + bytes(self.w3.eth.get_block(n)["hash"]).hex()

    def reset(self, from_block: int):
        """Forget everything from `from_block` on and rebuild the projections (`--from-block`)."""
        with db.session() as s:
            cur = self._cursor(s)
            s.execute(
                delete(ChainEvent).where(ChainEvent.chain_id == self.net.chain_id, ChainEvent.block >= from_block)
            )
            cur.block = max(from_block, self.net.deploy_block) - 1
            cur.block_hash = None
            self.rebuild(s)
            repo.audit(s, "indexer", "indexer.reset", self.net.name, from_block=from_block)

    def _check_reorg(self, s, cur: IndexerCursor) -> bool:
        if cur.block_hash is None or cur.block < self.net.deploy_block:
            return False
        try:
            if self._block_hash(cur.block) == cur.block_hash:
                return False
        except BlockNotFound:  # the canonical chain is now shorter than our cursor
            pass
        to = max(min(cur.block - REWIND_BLOCKS, self.w3.eth.block_number), self.net.deploy_block - 1)
        log.warning("%s: reorg detected at block %d, rewinding to %d", self.net.name, cur.block, to)
        s.execute(delete(ChainEvent).where(ChainEvent.chain_id == self.net.chain_id, ChainEvent.block > to))
        cur.block, cur.block_hash = to, None
        self.rebuild(s)
        repo.audit(s, "indexer", "indexer.reorg", self.net.name, rewound_to=to)
        return True

    # ---- fetching ------------------------------------------------------------------------------

    def _fetch(self, frm: int, to: int) -> list[dict]:
        logs = self.w3.eth.get_logs({"fromBlock": frm, "toBlock": to, "address": [self.registry, self.nft]})
        out = []
        for lg in logs:
            topic = "0x" + bytes(lg["topics"][0]).hex() if lg["topics"] else None
            decoder = self._decoders.get((Web3.to_checksum_address(lg["address"]), topic))
            if decoder is None:  # an event we do not index (role changes, ...)
                continue
            ev = decoder.process_log(lg)
            out.append(
                {
                    "chain_id": self.net.chain_id,
                    "block": lg["blockNumber"],
                    "block_hash": "0x" + bytes(lg["blockHash"]).hex(),
                    "tx": "0x" + bytes(lg["transactionHash"]).hex(),
                    "log_index": lg["logIndex"],
                    "contract": Web3.to_checksum_address(lg["address"]),
                    "event": ev["event"],
                    "args": {k: _jsonable(v) for k, v in ev["args"].items()},
                }
            )
        out.sort(key=lambda e: (e["block"], e["log_index"]))
        return out

    def run_once(self) -> int:
        """Index up to the confirmed head. Returns the number of new events."""
        safe = self.w3.eth.block_number - self.net.confirmations
        new = 0
        with db.session() as s:
            self._check_reorg(s, self._cursor(s))
        while True:
            with db.session() as s:
                cur = self._cursor(s)
                if cur.block >= safe:
                    break
                frm, to = cur.block + 1, min(cur.block + self.chunk, safe)
                events = self._fetch(frm, to)
                for e in events:
                    res = s.execute(insert(ChainEvent).values(**e).on_conflict_do_nothing(constraint="uq_chain_event"))
                    if res.rowcount:  # apply each event exactly once
                        self.apply(s, e)
                        new += 1
                cur.block, cur.block_hash = to, self._block_hash(to)
        if new:
            log.info("%s: %d new events", self.net.name, new)
        return new

    # ---- projections ---------------------------------------------------------------------------

    def rebuild(self, s):
        """Recompute tokens / disputes for this chain from chain_events (after a reorg or reset)."""
        cid = self.net.chain_id
        s.execute(delete(Token).where(Token.chain_id == cid))
        s.execute(delete(Dispute).where(Dispute.chain_id == cid))
        rows = s.scalars(
            select(ChainEvent).where(ChainEvent.chain_id == cid).order_by(ChainEvent.block, ChainEvent.log_index)
        )
        for ev in rows:
            self.apply(
                s,
                {"block": ev.block, "tx": ev.tx, "contract": ev.contract, "event": ev.event, "args": ev.args},
                replay=True,
            )
        # Submissions marked MINTED whose token disappeared (reorged out) go back to their verdict.
        minted = {t for (t,) in s.execute(select(Token.report_hash))}
        for sub in s.scalars(select(Submission).where(Submission.status == "MINTED").join(Report)):
            rep = sub.report
            if rep.report_hash not in minted and "legacy_v1" not in rep.body:
                sub.status = "REVIEW_APPROVED" if (rep.review or {}).get("approved") else rep.decision

    def apply(self, s, e: dict, replay: bool = False):
        cid, a, name = self.net.chain_id, e["args"], e["event"]
        if name == "VerdictSubmitted":
            repo.mark_verdict_submitted(s, cid, e["contract"], _h(a["reportHash"]), e["tx"])
        elif name == "ImageMinted":
            image, report_hash = _h(a["imageHash"]), _h(a["reportHash"])
            s.execute(
                insert(Token)
                .values(
                    chain_id=cid,
                    contract=a["collection"],
                    token_id=Decimal(a["tokenId"]),
                    owner=a["artist"],
                    artist=a["artist"],
                    image_sha256=image,
                    report_hash=report_hash,
                    status="active",
                    minted_block=e["block"],
                    minted_tx=e["tx"],
                )
                .on_conflict_do_nothing()
            )
            rep = repo.get_report_by_hash(s, report_hash)
            if rep is not None:
                rep.submission.status = "MINTED"
                repo.add_reference(s, "mintgate", f"minted/{rep.id}.png", cid, a["collection"], a["tokenId"])
                if not replay:
                    repo.audit(s, "indexer", "token.minted", rep.id, chain_id=cid, token_id=a["tokenId"], tx=e["tx"])
        elif name == "Transfer" and a["from"] != ZERO:
            s.execute(
                update(Token)
                .where(Token.chain_id == cid, Token.contract == e["contract"], Token.token_id == Decimal(a["tokenId"]))
                .values(owner=a["to"])
            )
        elif name == "DisputeOpened":
            s.execute(
                insert(Dispute)
                .values(
                    chain_id=cid,
                    registry_address=e["contract"],
                    dispute_id=Decimal(a["disputeId"]),
                    image_sha256=_h(a["imageHash"]),
                    disputant=a["disputant"],
                    bond=Decimal(a["bond"]),
                    evidence_uri=a["evidenceURI"],
                    status="open",
                    opened_block=e["block"],
                    opened_tx=e["tx"],
                )
                .on_conflict_do_nothing()
            )
            self._set_token_status(s, _h(a["imageHash"]), "flagged")
        elif name == "DisputeResolved":
            s.execute(
                update(Dispute)
                .where(Dispute.chain_id == cid, Dispute.dispute_id == Decimal(a["disputeId"]))
                .values(
                    status="upheld" if a["upheld"] else "rejected",
                    resolved_block=e["block"],
                    resolved_tx=e["tx"],
                    reviewer=a["reviewer"],
                )
            )
            if not a["upheld"]:
                self._set_token_status(s, _h(a["imageHash"]), "active")
        elif name == "ImageRevoked":  # emitted before DisputeResolved in the same tx; revoked is final
            self._set_token_status(s, _h(a["imageHash"]), "revoked")

    def _set_token_status(self, s, image: str, status: str):
        q = update(Token).where(Token.chain_id == self.net.chain_id, Token.image_sha256 == image)
        s.execute(q.where(Token.status != "revoked").values(status=status))
