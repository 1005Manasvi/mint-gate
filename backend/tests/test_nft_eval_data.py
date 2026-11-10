"""Phase 5b helpers: member pixel rule variants and copymint pairing (no network, no models)."""

import numpy as np
from PIL import Image

from detection import fingerprints as fp
from training.copymint_data import COLLECTIONS, original_token, unmirror


def _punk(seed=0, cell=10, grid=24):
    """A random pixel-art image (grid x grid cells, `cell` px each)."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 4, (grid, grid))
    pal = np.array([[0, 0, 0], [255, 255, 255], [200, 30, 30], [30, 30, 200]], np.uint8)
    return Image.fromarray(pal[a].repeat(cell, 0).repeat(cell, 1))


def test_dihedral_small_is_the_8_element_group():
    a = np.arange(4 * 4 * 3).reshape(4, 4, 3)
    vs = fp.dihedral_small(a)
    assert len(vs) == 8
    assert len({v.tobytes() for v in vs}) == 8
    assert any(np.array_equal(v, a[:, ::-1]) for v in vs)  # the left-right mirror is in the group


def test_member_diff_finds_a_mirrored_copy_only_with_dihedral():
    img = _punk(1)
    mirrored = img.transpose(Image.FLIP_LEFT_RIGHT)
    ref = fp.rgb_small(img)
    assert fp.member_diff([fp.rgb_small(mirrored)], ref) > 1000
    assert fp.member_diff(fp.dihedral_small(fp.rgb_small(mirrored)), ref) == 0


def test_smooth_resampling_survives_a_resolution_change():
    """The failure seen on real full-size uploads: NEAREST vs a LANCZOS thumbnail of the same art."""
    big = _punk(2, cell=50)  # 1200 px upload
    thumb = big.copy()
    thumb.thumbnail((128, 128), Image.LANCZOS)
    near = fp.member_diff([fp.rgb_small(big, "nearest")], fp.rgb_small(thumb, "nearest"))
    smooth = fp.member_diff([fp.rgb_small(big, "lanczos")], fp.rgb_small(thumb, "lanczos"))
    assert smooth < near


def test_siblings_stay_apart_with_smooth_resampling():
    a = np.asarray(_punk(3)).copy()
    b = a.copy()
    b[100:110, 100:110] = [200, 30, 30] if not (a[105, 105] == [200, 30, 30]).all() else [30, 30, 200]
    diff = fp.member_diff([fp.rgb_small(Image.fromarray(b), "lanczos")], fp.rgb_small(Image.fromarray(a), "lanczos"))
    assert diff > 0  # one changed 24-grid cell is still visible at 128 px


def test_copymint_pairing():
    assert original_token(COLLECTIONS["rrbayc"], "216", "#7400") == "7400"
    assert original_token(COLLECTIONS["rrbayc"], "216", "Ape") is None
    assert original_token(COLLECTIONS["cryptophunks"], "24", None) == "24"
    img = _punk(4)
    assert np.array_equal(np.asarray(unmirror(unmirror(img, "mirror"), "mirror")), np.asarray(img))
    assert unmirror(img, "none") is img


def test_as_upload_keeps_acceptable_files_and_shrinks_the_rest(monkeypatch):
    import io

    import config
    from training.ai_nft_data import as_upload

    buf = io.BytesIO()
    _punk(5).save(buf, format="PNG")
    small = buf.getvalue()
    assert as_upload(small) is small  # within the limits: the original bytes
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)  # pretend the original is too large
    out = as_upload(small)
    assert out[:2] == b"\xff\xd8" and Image.open(io.BytesIO(out)).size == (240, 240)  # JPEG, never upscaled
