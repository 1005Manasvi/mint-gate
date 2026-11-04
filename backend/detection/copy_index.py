"""Reference indexes for the copy engine: in-memory (numpy) and PostgreSQL (pgvector).

Both expose the same four calls the engine needs:
  exact(pdq8, max_bits)  -> [(Reference, bits)]   PDQ Hamming distance <= max_bits
  knn(vector, k)         -> [Reference]           ANN on the fused vector (inner product)
  knn_sscd(vector, k)    -> [Reference]           ANN on SSCD alone (region crops)
  load_image(ref)        -> PIL image             for LightGlue verification
`nn_sscd` / `nn_dino` (each reference's nearest neighbour inside the index) are kept current on
insert: a newcomer can become an existing reference's nearest neighbour.
"""

import json

import numpy as np

from detection import fingerprints as fp
from detection.copy_engine import Fingerprint, Reference
from detection.preprocessing import load_path


class MemoryCopyIndex:
    """Brute-force numpy index (tests, benchmark parity checks, small deployments)."""

    def __init__(self, w: float = 1.0):
        self.w = w
        self.names, self.paths, self.collection, self.extra = [], [], [], []
        self.pdq = np.zeros((0, 32), np.uint8)
        self.sscd = np.zeros((0, 512), np.float32)
        self.dino = None
        self.nn_sscd = np.zeros(0, np.float32)
        self.nn_dino = np.zeros(0, np.float32)

    def __len__(self):
        return len(self.names)

    def add(self, name: str, f: Fingerprint, path=None, collection=False, **extra):
        s = self.sscd @ f.sscd
        self.nn_sscd = np.maximum(self.nn_sscd, s) if len(s) else self.nn_sscd
        self.nn_sscd = np.append(self.nn_sscd, np.float32(s.max() if len(s) else 0.0))
        if f.dino is not None:
            if self.dino is None:
                self.dino = np.zeros((0, len(f.dino)), np.float32)
            d = self.dino @ f.dino
            self.nn_dino = np.maximum(self.nn_dino, d) if len(d) else self.nn_dino
            self.nn_dino = np.append(self.nn_dino, np.float32(d.max() if len(d) else 0.0))
            self.dino = np.vstack([self.dino, f.dino[None]])
        self.sscd = np.vstack([self.sscd, f.sscd[None]])
        self.pdq = np.vstack([self.pdq, np.frombuffer(f.pdq, np.uint8)[None]])
        self.names.append(name)
        self.paths.append(path)
        self.collection.append(bool(collection))
        self.extra.append({"image": name, **extra})

    def _ref(self, i) -> Reference:
        return Reference(
            key=i,
            name=self.names[i],
            collection=self.collection[i],
            sscd=self.sscd[i],
            dino=None if self.dino is None else self.dino[i],
            nn_sscd=float(self.nn_sscd[i]),
            nn_dino=float(self.nn_dino[i]) if self.dino is not None else 0.0,
            image_path=self.paths[i],
            extra=self.extra[i],
        )

    def exact(self, pdq8, max_bits):
        if not len(self):
            return []
        d = fp.pdq_distances(pdq8, self.pdq)
        return [(self._ref(int(i)), int(d[i])) for i in np.flatnonzero(d <= max_bits)]

    def _vectors(self):
        if self.dino is None:
            return self.sscd
        return np.hstack([np.sqrt(self.w) * self.sscd, np.sqrt(1 - self.w) * self.dino])

    def _top(self, sims, k):
        k = min(k, len(sims))
        return [self._ref(int(i)) for i in np.argsort(-sims)[:k]]

    def knn(self, vector, k):
        return self._top(self._vectors() @ vector, k) if len(self) else []

    def knn_sscd(self, vector, k):
        return self._top(self.sscd @ vector, k) if len(self) else []

    def load_image(self, ref: Reference):
        return load_path(ref.image_path)


# ---- PostgreSQL / pgvector -----------------------------------------------------------------------

MEMBER_K = 10  # collection members returned per query (display only: they never score copy_semantic)
_COLS = "id, source, image_uri, chain_id, contract, token_id, collection_member, sscd, dino, nn_sscd, nn_dino"


def resolve_image(source: str, image_uri: str):
    """Local path of a reference image (seed data, Mint-Gate upload or 128 px on-chain thumbnail)."""
    import config

    if source == "seed":
        from ingest.seed import seed_path

        return seed_path(image_uri)
    if image_uri.startswith("minted/"):
        return config.UPLOADS_DIR / image_uri[len("minted/") :]
    if image_uri.startswith("nft/"):
        return config.NFT_THUMBS_DIR / image_uri[len("nft/") :]
    return None


def _array(v):
    """pgvector value -> float32 array (raw SQL returns halfvec as its text form '[1,0,...]')."""
    if v is None:
        return None
    if isinstance(v, str):
        return np.array(json.loads(v), np.float32)
    return np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, np.float32)


def _row_ref(row) -> Reference:
    (rid, source, uri, chain_id, contract, token_id, member, sscd, dino, nn_s, nn_d) = row
    if source == "alchemy":
        name = f"{chain_id}:{contract}:{token_id}"
        extra = {"chain_id": chain_id, "contract": contract, "token_id": str(token_id), "image": uri}
    else:
        name = uri.removeprefix("seed/")
        extra = {"image": name}
    return Reference(
        key=rid,
        name=name,
        collection=bool(member),
        sscd=_array(sscd),
        dino=_array(dino),
        nn_sscd=float(nn_s or 0.0),
        nn_dino=float(nn_d or 0.0),
        image_path=resolve_image(source, uri),
        extra=extra,
    )


PDQ_REFRESH_S = 60
PDQ_FULL_RELOAD_S = 600


def _load_pdq(after_id: int):
    """(ids, (n, 4) uint64) PDQ hashes with id > after_id, in pgvector's binary format."""
    import db

    ids, rows = [], []
    raw = db.engine().raw_connection()
    try:
        from pgvector.psycopg import register_vector

        conn = raw.driver_connection
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pdq FROM reference_items WHERE pdq IS NOT NULL AND id > %s ORDER BY id", (after_id,)
            )
            for rid, bits in cur:
                ids.append(rid)
                if isinstance(bits, str):  # bit(n) arrives as its '0101...' text form
                    bits = np.frombuffer(bits.encode(), np.uint8) - ord("0")
                else:
                    bits = np.asarray(bits.to_numpy(), dtype=np.uint8)
                rows.append(np.packbits(bits))
        conn.rollback()
    finally:
        raw.close()
    if not rows:
        return np.zeros(0, np.int64), np.zeros((0, 4), np.uint64)
    return np.array(ids, np.int64), np.ascontiguousarray(np.stack(rows)).view(np.uint64)


class PgCopyIndex:
    """pgvector HNSW search over reference_items (all sources, status ready)."""

    def __init__(self, fused: bool = True, ef_search: int = 100):
        self.fused = fused  # False: the engine runs without DINOv2, so search SSCD alone
        self.ef_search = ef_search

    def _query(self, sql, params, k):
        from sqlalchemy import text

        import db

        with db.session() as s:
            s.execute(text(f"SET LOCAL hnsw.ef_search = {max(self.ef_search, 2 * k)}"))
            return s.execute(text(sql), params).all()

    def exact(self, pdq8, max_bits, limit=20):
        """Exact PDQ search over an in-memory copy of every hash. HNSW over 256-bit codes found a
        <= 8-bit match for only 94-98 % of collection members (clusters of identical placeholder
        art): an exact-copy check must not be approximate. 285k hashes = 9 MB; a scan takes ms."""
        from sqlalchemy import text

        import db

        ids, hashes = self._pdq_cache()
        if not len(ids):
            return []
        q = np.ascontiguousarray(np.atleast_2d(pdq8), dtype=np.uint8).view(np.uint64)  # (k, 4)
        w0, w1 = self._pdq["w0"], self._pdq["w1"]
        dist = {}
        for h in q:
            # Lower bound first: the distance over the first 128 bits is <= the full distance, and
            # unrelated hashes are ~64 bits apart there, so only near matches reach the full count.
            cand = np.flatnonzero(np.bitwise_count(w0 ^ h[0]) + np.bitwise_count(w1 ^ h[1]) <= max_bits)
            if not len(cand):
                continue
            full = np.bitwise_count(hashes[cand] ^ h).sum(axis=1)
            for i, dd in zip(cand[full <= max_bits], full[full <= max_bits]):
                rid = int(ids[i])
                dist[rid] = min(int(dd), dist.get(rid, 256))
        if not dist:
            return []
        dist = dict(sorted(dist.items(), key=lambda kv: kv[1])[:limit])
        with db.session() as s:
            rows = s.execute(
                text(f"SELECT {_COLS} FROM reference_items WHERE id = ANY(:ids)"), {"ids": list(dist)}
            ).all()
        return sorted(((_row_ref(r), dist[r[0]]) for r in rows), key=lambda x: x[1])

    def _pdq_cache(self):
        """(ids, (n, 4) uint64 hashes): new rows every 60 s, full reload every 10 min (rows that
        became ready later keep their old id, so an id cursor alone would miss them)."""
        import time

        now = time.monotonic()
        c = getattr(self, "_pdq", None)
        if c is None or now - c["full_at"] > PDQ_FULL_RELOAD_S:
            ids, h = _load_pdq(0)
            self._pdq = c = {"ids": ids, "h": h, "full_at": now, "at": now}
        elif now - c["at"] > PDQ_REFRESH_S:
            ids, h = _load_pdq(int(c["ids"].max()) if len(c["ids"]) else 0)
            if len(ids):
                c["ids"], c["h"] = np.concatenate([c["ids"], ids]), np.vstack([c["h"], h])
            c["at"] = now
        if c.get("w_n") != len(c["ids"]):  # contiguous first two words for the lower-bound pass
            c["w0"], c["w1"], c["w_n"] = c["h"][:, 0].copy(), c["h"][:, 1].copy(), len(c["ids"])
        return c["ids"], c["h"]

    def _knn(self, column, vector, k, members: bool):
        from pgvector import HalfVector

        rows = self._query(
            f"SELECT {_COLS} FROM reference_items WHERE {column} IS NOT NULL "
            f"AND {'' if members else 'NOT '}collection_member "
            f"ORDER BY {column} <#> CAST(:q AS halfvec) LIMIT :k",
            {"q": HalfVector(vector).to_text(), "k": k},
            k,
        )
        return [_row_ref(r) for r in rows]

    def knn(self, vector, k):
        """One-off works through their own partial HNSW index (migration 0005): in one graph with
        280k clustered collection items, HNSW found a painting copy's source only 51 % of the time
        (exact search: 100 %). Collection members only count as exact copies, so a few are enough
        here (they are shown as similar works)."""
        col = "copy_vec" if self.fused else "sscd"
        return self._knn(col, vector, k, members=False) + self._knn(col, vector, MEMBER_K, members=True)

    def knn_sscd(self, vector, k):
        return self._knn("sscd", vector, k, members=False)  # region crops: one-off works only

    def load_image(self, ref: Reference):
        return load_path(ref.image_path)
