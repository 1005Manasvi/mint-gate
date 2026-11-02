"""Pre-computed index of the protected / already-minted collection.

Built once by `training/build_index.py`. Per reference it stores a 64-bit
pHash, a CLIP embedding and `nn_sim`: the CLIP similarity of that reference
to its own nearest neighbour inside the collection.

`nn_sim` makes the copy score density-relative. In a generative collection
(CryptoPunks) siblings are ~0.99 similar to each other, while distinct
paintings are ~0.85 similar. A fixed similarity threshold cannot serve both,
but "how much closer is the upload than the reference's own nearest
neighbour" can.

Name prefixes:  "<file>"  seed NFT collection (REFERENCE_DIR)
                "art/<file>"     protected artworks (REFERENCE_ART_DIR)
                "minted/<file>"  minted through Mint-Gate (UPLOADS_DIR)
"""
import json

import numpy as np

from config import (INDEX_CLIP, INDEX_FILES, INDEX_NN, INDEX_PHASH, ORIGINALS_DIR,
                    REFERENCE_ART_DIR, UPLOADS_DIR)
from detection.hashing import hamming_to_all

MINTED_PREFIX = "minted/"
ART_PREFIX = "art/"
EXACT_SIM = 0.9999
# References whose own nearest neighbour is this similar sit in a "dense" region
# (a generative collection). There, shared keypoints / hashes are expected between
# genuine siblings, so pHash and ORB are only evaluated against sparse references.
DENSE_NN = 0.95
NO_MATCH_DISTANCE = 64  # pHash distance reported when no sparse reference exists


def nearest_neighbour_sims(emb: np.ndarray, chunk=2048) -> np.ndarray:
    """For every row, the cosine similarity of its most similar *other* row."""
    out = np.empty(len(emb), dtype=np.float32)
    for s in range(0, len(emb), chunk):
        sims = emb[s:s + chunk] @ emb.T
        idx = np.arange(s, min(s + chunk, len(emb)))
        sims[idx - s, idx] = -1.0  # ignore self
        out[s:s + chunk] = sims.max(axis=1)
    return out


class ReferenceIndex:
    def __init__(self):
        if not INDEX_FILES.exists():
            raise FileNotFoundError("reference index missing - run: python -m training.build_index")
        self.files = json.loads(INDEX_FILES.read_text())
        self.phash = np.load(INDEX_PHASH)
        self.clip = np.load(INDEX_CLIP)
        self.nn_sim = np.load(INDEX_NN) if INDEX_NN.exists() else nearest_neighbour_sims(self.clip)

    def __len__(self):
        return len(self.files)

    def path(self, i):
        name = self.files[i]
        if name.startswith(MINTED_PREFIX):
            return UPLOADS_DIR / name[len(MINTED_PREFIX):]
        if name.startswith(ART_PREFIX):
            return REFERENCE_ART_DIR / name[len(ART_PREFIX):]
        return ORIGINALS_DIR / name

    def add(self, name: str, phash: int, emb: np.ndarray):
        """Register a newly minted artwork so later uploads are compared against it too."""
        emb = emb.astype(self.clip.dtype)
        sims = self.clip @ emb
        self.nn_sim = np.maximum(self.nn_sim, sims)  # the newcomer may be someone's new nearest neighbour
        self.files.append(MINTED_PREFIX + name)
        self.phash = np.append(self.phash, np.uint64(phash))
        self.clip = np.vstack([self.clip, emb[None, :]])
        self.nn_sim = np.append(self.nn_sim, np.float32(sims.max() if len(sims) else 0.0))

    @property
    def sparse(self) -> np.ndarray:
        return self.nn_sim < DENSE_NN

    def search(self, phash: int, emb: np.ndarray, k=5):
        """Return (clip_sims, phash_dists, candidate_indices).

        Candidates = top-k by CLIP similarity (any reference) U top-3 sparse references
        by CLIP U top-3 sparse references by pHash distance.
        """
        sims = self.clip @ emb
        dists = hamming_to_all(phash, self.phash)
        sparse = np.flatnonzero(self.sparse)
        by_clip = np.argsort(-sims)[:k]
        cand = [*by_clip.tolist()]
        if len(sparse):
            cand += sparse[np.argsort(-sims[sparse])[:3]].tolist()
            cand += sparse[np.argsort(dists[sparse], kind="stable")[:3]].tolist()
        return sims, dists, list(dict.fromkeys(cand))

    def sparse_min_distance(self, dists: np.ndarray):
        """(distance, index) of the closest pHash among sparse references."""
        sparse = np.flatnonzero(self.sparse)
        if not len(sparse):
            return NO_MATCH_DISTANCE, None
        j = int(sparse[np.argmin(dists[sparse])])
        return int(dists[j]), j

    def copy_score(self, sims: np.ndarray):
        """(score, index): how far the upload stands out above each reference's own nearest
        neighbour, maximised over the top candidates. Identical images score 1.0."""
        top = np.argsort(-sims)[:10]
        margins = sims[top] - self.nn_sim[top]
        j = int(top[int(np.argmax(margins))])
        score = float(margins.max())
        if sims[top[0]] >= EXACT_SIM:
            return 1.0, int(top[0])
        return score, j
