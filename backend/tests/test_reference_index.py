"""Reference index: density-relative copy score and the sparse-reference mask."""

import numpy as np
import pytest

from detection.hashing import hamming_to_all
from detection.reference_index import DENSE_NN, NO_MATCH_DISTANCE, ReferenceIndex, nearest_neighbour_sims


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def make_index(files, clip, nn_sim, phash=None):
    idx = ReferenceIndex.__new__(ReferenceIndex)
    idx.files = list(files)
    idx.clip = np.asarray(clip, dtype=np.float32)
    idx.nn_sim = np.asarray(nn_sim, dtype=np.float32)
    idx.phash = np.asarray(phash if phash is not None else [0] * len(files), dtype=np.uint64)
    return idx


@pytest.fixture
def index():
    rng = np.random.default_rng(0)
    clip = np.stack([unit(rng.normal(size=16)) for _ in range(5)])
    files = [
        "cryptopunk1.jpg",  # seed collection (no "/")
        "art/nft_bayc_7.png",  # generative collection inside art/
        "art/monet_1.jpg",  # protected painting, sparse
        "art/dense_1.jpg",  # painting in a dense region
        "minted/abc.png",
    ]  # minted through Mint-Gate
    return make_index(files, clip, [0.99, 0.97, 0.80, 0.96, 0.70])


def test_collection_and_sparse_masks(index):
    assert index.collection.tolist() == [True, True, False, False, False]
    assert index.sparse.tolist() == [False, False, True, False, True]


def test_masks_refresh_after_add(index):
    index.add("new.png", 123, unit(np.ones(16)))
    assert len(index) == 6 and index.files[-1] == "minted/new.png"
    assert len(index.sparse) == len(index.collection) == 6


def test_exact_copy_scores_one(index):
    for i in range(len(index)):  # including collection members: exact copies always count
        score, j = index.copy_score(index.clip @ index.clip[i])
        assert (score, j) == (1.0, i)


def test_collection_near_copy_is_not_a_copy(index):
    # very close to the CryptoPunk (sim ~0.999, but not identical): a genuine sibling
    q = unit(index.clip[0] + 0.03 * index.clip[2])
    sims = index.clip @ q
    assert 0.99 < sims[0] < 0.9999
    score, j = index.copy_score(sims)
    assert j != 0 and score < 0.2


def test_near_copy_of_painting_stands_out(index):
    q = unit(index.clip[2] + 0.05 * index.clip[3])
    score, j = index.copy_score(index.clip @ q)
    assert j == 2
    assert score == pytest.approx(float(index.clip[2] @ q) - 0.80, abs=1e-5)
    assert score > 0.15


def test_only_collection_candidates_scores_zero():
    clip = np.stack([unit([1, 0, 0]), unit([0, 1, 0])])
    idx = make_index(["punk1.jpg", "punk2.jpg"], clip, [0.99, 0.99])
    assert idx.copy_score(idx.clip @ unit([1, 0.1, 0]))[0] == 0.0


def test_sparse_min_distance_ignores_collection(index):
    dists = np.array([0, 0, 20, 1, 9])
    assert index.sparse_min_distance(dists) == (9, 4)


def test_sparse_min_distance_without_sparse_refs():
    idx = make_index(["punk1.jpg"], [unit([1, 0])], [0.99])
    assert idx.sparse_min_distance(np.array([0])) == (NO_MATCH_DISTANCE, None)


def test_search_candidates_are_unique(index):
    sims, dists, cand = index.search(0, index.clip[2], k=3)
    assert cand[0] == 2
    assert len(cand) == len(set(cand))
    assert set(np.flatnonzero(index.sparse)) <= set(cand)


def test_add_updates_neighbour_similarity(index):
    near = unit(index.clip[2] + 0.01 * index.clip[1])
    index.add("copy.png", 0, near)
    assert index.nn_sim[2] > 0.99  # the painting now has a near-identical neighbour
    assert index.nn_sim[-1] == pytest.approx(float(index.clip[2] @ near), abs=1e-5)
    assert not index.sparse[2]  # ... so it moved to the dense region
    assert DENSE_NN < 0.99


def test_nearest_neighbour_sims_ignores_self():
    emb = np.stack([unit([1, 0]), unit([1, 0.1]), unit([0, 1])])
    nn = nearest_neighbour_sims(emb, chunk=2)
    assert nn[0] == pytest.approx(float(emb[0] @ emb[1]))
    assert nn[2] == pytest.approx(float(emb[2] @ emb[1]))


def test_hamming_distance():
    index = np.array([0, 0xFF, 2**64 - 1], dtype=np.uint64)
    assert hamming_to_all(0, index).tolist() == [0, 8, 64]
