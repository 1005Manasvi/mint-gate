"""Fingerprint pending references (all sources) with the copy engine; keep only 128 px thumbnails.

  pending row -> image (on-chain: spool; Mint-Gate: uploads/; seed: data/) -> PDQ + SSCD (+ DINOv2)
  -> reference_items (status ready) -> 128 px JPEG in storage/nft/thumbs/ -> spool file deleted.

Density term: `nn_sscd` / `nn_dino` = similarity of a reference to its nearest neighbour in the
index. `--follow` (live) keeps them current per batch with pgvector KNN (a newcomer can become an
existing reference's nearest neighbour); a bulk load uses `python -m ingest embed --rebuild-nn`,
an exact chunked GPU pass over the whole table.
"""

import time

import numpy as np
from PIL import Image
from sqlalchemy import text

import config
import db
from detection.copy_index import resolve_image
from detection.preprocessing import load_path
from ingest import fetch

THUMB_PX = 128
BATCH = 128


def log(*a):
    print(*a, flush=True)


def _source_image(source, uri, chain_id, contract, token_id):
    if source == "alchemy":
        return fetch.spool_path(chain_id, contract, str(token_id))
    return resolve_image(source, uri)


def _vec(v):
    return None if v is None else "[" + ",".join(f"{x:.5f}" for x in v) + "]"


def _bits(b: bytes):
    return "".join(f"{x:08b}" for x in b)


def save_thumb(img: Image.Image, uri: str):
    dest = config.NFT_THUMBS_DIR / uri[len("nft/") :]
    dest.parent.mkdir(parents=True, exist_ok=True)
    t = img.convert("RGB")
    t.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
    t.save(dest, format="JPEG", quality=88)


def store_fingerprint(s, rid: int, f, w: float):
    s.execute(
        text(
            "UPDATE reference_items SET pdq = CAST(:pdq AS bit(256)), pdq_quality = :q, "
            "sscd = CAST(:sscd AS halfvec), dino = CAST(:dino AS halfvec), "
            "copy_vec = CAST(:vec AS halfvec), status = 'ready' WHERE id = :id"
        ),
        {
            "pdq": _bits(f.pdq),
            "q": f.pdq_quality,
            "sscd": _vec(f.sscd),
            "dino": _vec(f.dino),
            "vec": _vec(f.vector(w)) if f.dino is not None else None,
            "id": rid,
        },
    )


def load_fingerprint(row):
    """Fingerprint of a ready reference_items row (for in-memory indexes)."""
    from detection.copy_engine import Fingerprint
    from detection.copy_index import _array as arr

    bits = row.pdq.to_numpy() if hasattr(row.pdq, "to_numpy") else np.array([c == "1" for c in str(row.pdq)])
    pdq = np.packbits(np.asarray(bits, np.uint8)).tobytes()
    return Fingerprint(pdq, int(row.pdq_quality or 0), np.frombuffer(pdq, np.uint8)[None], arr(row.sscd), arr(row.dino))


def update_nn(s, ids):
    """Incremental nearest-neighbour upkeep for freshly embedded rows (pgvector KNN, k=2)."""
    for col, nn in (("sscd", "nn_sscd"), ("dino", "nn_dino")):
        for rid in ids:
            rows = s.execute(
                text(
                    f"SELECT b.id, -(b.{col} <#> a.{col}) AS sim FROM reference_items a, LATERAL ("
                    f"  SELECT id, {col} FROM reference_items WHERE {col} IS NOT NULL AND id <> a.id"
                    f"  ORDER BY {col} <#> a.{col} LIMIT 3) b WHERE a.id = :id AND a.{col} IS NOT NULL"
                ),
                {"id": rid},
            ).all()
            if not rows:
                continue
            s.execute(text(f"UPDATE reference_items SET {nn} = :v WHERE id = :id"), {"v": float(rows[0][1]), "id": rid})
            for nid, sim in rows:
                s.execute(
                    text(f"UPDATE reference_items SET {nn} = GREATEST(COALESCE({nn}, -1), :v) WHERE id = :id"),
                    {"v": float(sim), "id": nid},
                )


def embed_pending(engine, sources=None, max_rows=None, incremental_nn=True) -> int:
    done, w = 0, engine.p["fusion"].get("w", 1.0)
    where = "status = 'pending'" + (" AND source = ANY(:src)" if sources else "")
    while max_rows is None or done < max_rows:
        with db.session() as s:
            rows = s.execute(
                text(
                    "SELECT id, source, image_uri, chain_id, contract, token_id FROM reference_items "
                    f"WHERE {where} ORDER BY id LIMIT :n"
                ),
                {"n": BATCH, "src": list(sources or [])},
            ).all()
        if not rows:
            break
        imgs, ok, failed = [], [], []
        for r in rows:
            path = _source_image(*r[1:])
            try:
                imgs.append(load_path(path))
                ok.append(r)
            except (OSError, ValueError, TypeError):
                failed.append(r[0])
        fps = engine.fingerprint_many(imgs) if imgs else []
        with db.session() as s:
            for r, img, f in zip(ok, imgs, fps):
                if r[1] == "alchemy":
                    save_thumb(img, r[2])
                store_fingerprint(s, r[0], f, w)
            if failed:
                s.execute(text("UPDATE reference_items SET status = 'failed' WHERE id = ANY(:ids)"), {"ids": failed})
            if incremental_nn:
                update_nn(s, [r[0] for r in ok])
        for r in ok:
            if r[1] == "alchemy":
                fetch.spool_path(*r[3:6]).unlink(missing_ok=True)
        done += len(rows)
    return done


def _load_column(col):
    """(ids, float16 matrix) of every non-null vector column, fetched in pgvector's binary format."""
    from pgvector.psycopg import register_vector

    ids, rows = [], []
    raw = db.engine().raw_connection()
    try:
        conn = raw.driver_connection
        register_vector(conn)
        with conn.cursor(name=f"nn_{col}") as cur:
            cur.itersize = 20000
            cur.execute(f"SELECT id, {col} FROM reference_items WHERE {col} IS NOT NULL ORDER BY id")
            for rid, v in cur:
                ids.append(rid)
                rows.append(v.to_numpy().astype(np.float16))
        conn.rollback()
    finally:
        raw.close()
    return np.array(ids), (np.stack(rows) if rows else np.zeros((0, 1), np.float16))


HNSW_INDEXES = {
    "ix_reference_items_pdq": "pdq bit_hamming_ops",
    "ix_reference_items_sscd": "sscd halfvec_ip_ops",
    "ix_reference_items_copy_vec": "copy_vec halfvec_ip_ops",
}


def drop_hnsw():
    """Bulk writes: every updated row would otherwise be re-inserted into three HNSW graphs."""
    with db.session() as s:
        for name in HNSW_INDEXES:
            s.execute(text(f"DROP INDEX IF EXISTS {name}"))


def create_hnsw():
    t = time.time()
    with db.session() as s:
        s.execute(text("SET LOCAL maintenance_work_mem = '768MB'"))
        for name, spec in HNSW_INDEXES.items():
            col, ops = spec.split()
            s.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON reference_items USING hnsw ({col} {ops})"))
    log(f"[embed] HNSW indexes rebuilt in {time.time() - t:.0f}s")


def rebuild_nn(chunk=256):
    """Exact nearest-neighbour similarity for every ready row (GPU matmul in chunks, fp16)."""
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    results = {}
    for col, nn in (("sscd", "nn_sscd"), ("dino", "nn_dino")):
        t = time.time()
        ids, mat = _load_column(col)
        if not len(ids):
            continue
        log(f"[embed] {col}: {len(ids)} vectors loaded in {time.time() - t:.0f}s")
        m = torch.from_numpy(mat).to(dev)
        best = np.empty(len(ids), np.float32)
        # Small chunks: a 2048 x 285k fp16 block (1.2 GB) spilled into shared memory on the 4 GB GPU.
        for i in range(0, len(ids), chunk):
            if i % (chunk * 200) == 0:
                log(f"[embed] {col}: {i}/{len(ids)}")
            sims = m[i : i + chunk] @ m.T
            sims[torch.arange(sims.shape[0]), torch.arange(i, i + sims.shape[0])] = -1
            best[i : i + chunk] = sims.max(1).values.float().cpu().numpy()
        del m, sims
        torch.cuda.empty_cache() if dev == "cuda" else None
        results[nn] = (ids, best)
        log(f"[embed] {nn}: computed for {len(ids)} references in {time.time() - t:.0f}s")
    # One UPDATE for both columns, with the HNSW indexes dropped (else hours of graph inserts).
    drop_hnsw()
    try:
        t = time.time()
        with db.session() as s:
            s.execute(text("CREATE TEMP TABLE nn_tmp (id bigint PRIMARY KEY, s real, d real) ON COMMIT DROP"))
            ids, s_best = results["nn_sscd"]
            d_map = dict(zip(*results["nn_dino"])) if "nn_dino" in results else {}
            for i in range(0, len(ids), 10000):
                vals = ",".join(
                    f"({int(a)},{float(b)},{'NULL' if int(a) not in d_map else float(d_map[int(a)])})"
                    for a, b in zip(ids[i : i + 10000], s_best[i : i + 10000])
                )
                s.execute(text("INSERT INTO nn_tmp VALUES " + vals))
            s.execute(text("UPDATE reference_items r SET nn_sscd = t.s, nn_dino = t.d FROM nn_tmp t WHERE r.id = t.id"))
        log(f"[embed] nn_sscd + nn_dino written in {time.time() - t:.0f}s")
    finally:
        create_hnsw()


def run(follow=False, interval=60, rebuild=False):
    from detection.copy_engine import CopyEngine

    if rebuild:
        rebuild_nn()
        return
    engine = CopyEngine(matcher=False)
    while True:
        t = time.time()
        n = embed_pending(engine, incremental_nn=follow)
        if n:
            log(f"[embed] {n} references fingerprinted in {time.time() - t:.0f}s")
        if not follow:
            with db.session() as s:
                missing = s.execute(
                    text("SELECT count(*) FROM reference_items WHERE status = 'ready' AND nn_sscd IS NULL")
                ).scalar()
            if missing:  # copy_semantic's density term is 0 for them: they would over-match
                log(f"[embed] WARNING: {missing} ready references have no nn_sscd: run `--rebuild-nn` (or `--follow`)")
            return
        time.sleep(interval if not n else 1)
