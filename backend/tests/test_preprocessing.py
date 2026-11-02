"""Upload validation and canonicalisation."""

import io

import pytest
from PIL import Image, PngImagePlugin

from config import MAX_IMAGE_PIXELS, MAX_UPLOAD_BYTES
from detection.preprocessing import InvalidImage, detector_view, prepare


def encode(img, fmt="PNG", **kw):
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def test_valid_png(fixture_bytes):
    p = prepare(fixture_bytes("photo.png"))
    assert p.rgb.mode == "RGB"
    assert (p.width, p.height) == p.rgb.size
    assert p.source_format == "PNG"
    assert Image.open(io.BytesIO(p.png_bytes)).format == "PNG"


@pytest.mark.parametrize("raw", [b"", b"not an image at all", b"\x89PNG\r\n\x1a\n" + b"\0" * 64])
def test_invalid_bytes_rejected(raw):
    with pytest.raises(InvalidImage):
        prepare(raw)


def test_oversize_upload_rejected():
    with pytest.raises(InvalidImage, match="larger than"):
        prepare(b"\0" * (MAX_UPLOAD_BYTES + 1))


def test_unsupported_format_rejected():
    with pytest.raises(InvalidImage, match="unsupported format"):
        prepare(encode(Image.new("RGB", (32, 32)), "TIFF"))


def test_too_small_rejected():
    with pytest.raises(InvalidImage, match="smaller than"):
        prepare(encode(Image.new("RGB", (8, 64))))


def test_decompression_bomb_rejected():
    # A few KB of PNG that would decode to 1.25x the pixel limit. Pillow itself only
    # warns below 2x the limit, so this exercises our own check.
    w = 10_000
    bomb = encode(Image.new("1", (w, int(MAX_IMAGE_PIXELS * 1.25) // w)))
    assert len(bomb) < 100_000
    with pytest.raises(InvalidImage, match="pixels"):
        prepare(bomb)


def test_huge_bomb_rejected_by_pillow_guard():
    w = 10_000
    bomb = encode(Image.new("1", (w, int(MAX_IMAGE_PIXELS * 2.1) // w)))
    with pytest.raises(InvalidImage):
        prepare(bomb)


def test_rgba_composited_on_white():
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))  # fully transparent
    img.putpixel((0, 0), (255, 0, 0, 255))  # opaque red
    img.putpixel((1, 0), (0, 0, 255, 128))  # half-transparent blue
    p = prepare(encode(img))
    assert p.rgb.mode == "RGB"
    assert p.rgb.getpixel((5, 5)) == (255, 255, 255)
    assert p.rgb.getpixel((0, 0)) == (255, 0, 0)
    r, g, b = p.rgb.getpixel((1, 0))
    assert 120 <= r <= 135 and 120 <= g <= 135 and b == 255


def test_palette_image_converted():
    img = Image.new("RGB", (32, 32), (10, 200, 30)).convert("P", palette=Image.Palette.ADAPTIVE)
    assert prepare(encode(img)).rgb.getpixel((0, 0)) == (10, 200, 30)


def test_sha256_depends_on_pixels_only():
    img = Image.effect_noise((48, 40), 60).convert("RGB")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Author", "someone")
    base = prepare(encode(img)).sha256
    assert prepare(encode(img)).sha256 == base  # stable
    assert prepare(encode(img, pnginfo=meta)).sha256 == base  # metadata ignored
    assert prepare(encode(img, "BMP")).sha256 == base  # container ignored
    assert prepare(encode(img, optimize=True)).sha256 == base  # encoder settings ignored
    changed = img.copy()
    changed.putpixel((0, 0), tuple((c + 1) % 256 for c in img.getpixel((0, 0))))
    assert prepare(encode(changed)).sha256 != base  # one pixel changes it


def test_sha256_includes_dimensions():
    # same pixel buffer, different shape
    a = Image.new("RGB", (32, 16), (1, 2, 3))
    b = Image.new("RGB", (16, 32), (1, 2, 3))
    assert prepare(encode(a)).sha256 != prepare(encode(b)).sha256


def test_canonical_png_round_trips_to_same_hash(fixture_bytes):
    p = prepare(fixture_bytes("painting.png"))
    assert prepare(p.png_bytes).sha256 == p.sha256


def test_exif_orientation_applied():
    img = Image.new("RGB", (40, 20), (0, 128, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees
    p = prepare(encode(img, "JPEG", exif=exif))
    assert p.rgb.size == (20, 40)


def test_detector_view_normalisation():
    view = detector_view(Image.new("RGB", (1200, 400), (200, 10, 10)))
    assert min(view.size) == 256 and max(view.size) <= 512  # short side 256, aspect capped 2:1
    small = detector_view(Image.new("RGB", (100, 80)))
    assert small.size == (100, 80)  # never upscaled
