"""Copy engine (Phase 4): PDQ exact filter, density-relative scoring, collection rule, index upkeep.

Real PDQ and scoring code; SSCD is the deterministic stand-in from tests/fakes.py.
"""

import numpy as np
from PIL import ImageOps

from detection import fingerprints as fp
from detection.copy_engine import Fingerprint, Reference
from detection.copy_index import MemoryCopyIndex
from tests.fakes import FIXTURES, copy_engine, fixture_image, tiny_copy_index


def test_pdq_distance_and_dihedral():
    img = fixture_image("painting.png")
    h, q = fp.pdq(img)
    assert len(h) == 32 and q >= 50
    refs = np.frombuffer(h, np.uint8)[None]
    assert fp.pdq_distances(np.frombuffer(h, np.uint8), refs)[0] == 0
    mirrored = ImageOps.mirror(img)
    plain = fp.pdq_distances(np.frombuffer(fp.pdq(mirrored)[0], np.uint8), refs)[0]
    assert fp.pdq_distances(fp.pdq_dihedral(mirrored), refs)[0] < plain  # the dihedral hashes undo the flip


def test_exact_copy_of_protected_painting():
    eng = copy_engine()
    out = eng.analyse(fixture_image("painting.png"), tiny_copy_index(eng))
    assert out["values"]["copy_exact"] == 0
    assert out["details"]["copy_exact"]["nearest"] == "art/painting.png"
    assert out["details"]["copy_semantic"]["nearest"] == "art/painting.png"
    assert out["values"]["copy_semantic"] > 0.1  # identical: cos 1.0 - nn 0.85
    assert out["matches"][0]["reference"] == "art/painting.png" and out["matches"][0]["image"] == "art/painting.png"


def test_collection_members_only_count_when_exact():
    """v1 lesson: a generative-collection sibling is not a copy; only copy_exact may flag members."""
    eng = copy_engine()
    out = eng.analyse(fixture_image("punk.png"), tiny_copy_index(eng))
    assert out["values"]["copy_exact"] == 0 and out["details"]["copy_exact"]["nearest"] == "punk.png"
    assert out["details"]["copy_semantic"]["nearest"] == "art/painting.png"  # the member is skipped
    assert out["matches"][0]["collection_member"] is True


def test_unrelated_upload_matches_nothing_exactly():
    eng = copy_engine()
    out = eng.analyse(fixture_image("photo.png"), tiny_copy_index(eng))
    assert out["values"]["copy_exact"] > 31 and out["details"]["copy_exact"]["nearest"] is None
    assert out["values"]["copy_semantic"] < 0.1
    assert "copy_geometric" not in out["values"]  # LightGlue did not pass the benchmark gate


def test_scores_follow_the_benchmark_formula():
    """cos - alpha * nn(ref) - beta * mean top-k cos(query, background): benchmark.copydet.normalised."""
    rng = np.random.default_rng(0)
    unit = lambda x: x / np.linalg.norm(x, axis=-1, keepdims=True)  # noqa: E731
    q, refs, bg = unit(rng.normal(size=512)), unit(rng.normal(size=(5, 512))), unit(rng.normal(size=(20, 512)))
    nn = rng.uniform(0.5, 0.9, 5)
    eng = copy_engine()
    eng.p = {**eng.p, "sscd": {"alpha": 0.5, "beta": 1.0}, "bg_k": 3}
    eng.bg = {"sscd": bg.astype(np.float32)}
    f = Fingerprint(b"\0" * 32, 100, np.zeros((1, 32), np.uint8), q.astype(np.float32))
    rs = [Reference(i, str(i), False, refs[i].astype(np.float32), None, nn[i], 0.0) for i in range(5)]
    expected = refs @ q - 0.5 * nn - np.sort(bg @ q)[-3:].mean()
    assert np.allclose(eng.scores(f, rs), expected, atol=1e-5)


def test_memory_index_keeps_nearest_neighbours_current():
    eng = copy_engine()
    idx = MemoryCopyIndex()
    a, b = fixture_image("painting.png"), fixture_image("photo.png")
    idx.add("a", eng.fingerprint(a), FIXTURES / "painting.png")
    idx.add("b", eng.fingerprint(b), FIXTURES / "photo.png")
    before = idx.nn_sscd[0]
    idx.add("a-copy", eng.fingerprint(a), FIXTURES / "painting.png")  # a newcomer becomes a's neighbour
    assert idx.nn_sscd[0] > before and np.isclose(idx.nn_sscd[0], 1.0, atol=1e-5)
    assert np.isclose(idx.nn_sscd[2], 1.0, atol=1e-5)
    assert [r.name for r in idx.knn(eng.fingerprint(a).sscd, 2)] in (["a", "a-copy"], ["a-copy", "a"])


def test_member_exact_copy_needs_pixel_identity():
    """A collection member counts as copied only if pixel-identical: a sibling with one changed
    'grid cell' is PDQ-close but must not match (CryptoPunks lesson); a lossless re-upload must.
    Known limit: a JPEG of *textured* collection art can exceed the pixel budget (not caught)."""
    from PIL import ImageDraw

    from benchmark.attacks import jpeg

    eng = copy_engine()
    punk = fixture_image("punk.png")
    sibling = punk.copy()
    w, h = sibling.size
    ImageDraw.Draw(sibling).rectangle((w // 3, h // 3, w // 3 + w // 8, h // 3 + h // 8), fill=(255, 0, 255))
    reupload = punk.resize((w * 2, h * 2))  # a lossless re-upload at another size
    assert fp.differing_pixels(fp.rgb_small(punk), fp.rgb_small(reupload)) <= 13
    assert fp.differing_pixels(fp.rgb_small(punk), fp.rgb_small(sibling)) > 13
    assert fp.differing_pixels(fp.rgb_small(punk), fp.rgb_small(jpeg(punk, 90))) > 13  # the known limit
    eng.p = {**eng.p, "member_max_diff_px": 13}
    idx = tiny_copy_index(eng)
    assert eng.analyse(reupload, idx)["details"]["copy_exact"]["nearest"] == "punk.png"
    assert eng.analyse(sibling, idx)["details"]["copy_exact"]["nearest"] is None


def test_copy_member_margin_catches_a_mirrored_remint():
    """Phase 5b: copy_member = cos(upload or its mirror, member) - the member's own nn_sscd; one-off
    works are not members and never count here; without `member_semantic` the value is absent."""
    eng = copy_engine()
    idx = tiny_copy_index(eng)
    punk = fixture_image("punk.png")
    assert "copy_member" not in eng.analyse(punk, idx)["values"]
    eng.p = {**eng.p, "member_semantic": {"k": 10, "mirror": True, "threshold": 0.15}}
    idx.nn_sscd[0] = 0.6  # the punk's nearest sibling
    out = eng.analyse(ImageOps.mirror(punk), idx)
    assert abs(out["values"]["copy_member"] - 0.4) < 1e-3  # mirror of the mirror = the punk: cos 1.0 - 0.6
    assert out["details"]["copy_member"] == {"nearest": "punk.png", "mirrored": True}
    empty = MemoryCopyIndex()
    assert eng.analyse(punk, empty)["values"]["copy_member"] == -1.0
