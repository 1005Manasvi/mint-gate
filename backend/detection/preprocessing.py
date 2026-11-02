"""Image validation and normalisation shared by every check.

Uploaded bytes are decoded exactly once, validated, flattened to RGB and
re-encoded as PNG so that all checks operate on identical pixels and the
SHA-256 we put on-chain identifies the *pixels*, not the container metadata.
"""
import hashlib
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from config import MAX_IMAGE_PIXELS, MAX_UPLOAD_BYTES

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP", "GIF"}
MIN_SIDE = 16


class InvalidImage(ValueError):
    """Raised for uploads that are not safe, supported images."""


@dataclass
class PreparedImage:
    rgb: Image.Image          # RGB, EXIF orientation applied
    png_bytes: bytes          # canonical PNG encoding of `rgb`
    sha256: str               # hex digest of the RGB pixel buffer + size
    width: int
    height: int
    source_format: str

    @property
    def gray(self) -> np.ndarray:
        return np.asarray(self.rgb.convert("L"))


def prepare(raw: bytes) -> PreparedImage:
    if not raw:
        raise InvalidImage("empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidImage(f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()  # structural check before full decode
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImage(f"not a valid image: {exc}") from exc
    if fmt not in ALLOWED_FORMATS:
        raise InvalidImage(f"unsupported format {fmt}")

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        # Composite transparency onto white so alpha does not leak into checks.
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    rgb = img.convert("RGB")
    if min(rgb.size) < MIN_SIDE:
        raise InvalidImage(f"image smaller than {MIN_SIDE}px")

    digest = hashlib.sha256()
    digest.update(f"{rgb.width}x{rgb.height}".encode())
    digest.update(rgb.tobytes())

    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return PreparedImage(rgb, buf.getvalue(), digest.hexdigest(), rgb.width, rgb.height, fmt)


DETECTOR_SHORT_SIDE = 256


def detector_view(img: Image.Image, quality: int = 92) -> Image.Image:
    """The exact normalisation applied to every training image (training/fetch_realworld.py):
    shorter side -> 256 px (downscale only), aspect capped at 2:1, JPEG re-encode.

    The learned detectors must see uploads through the same lens, otherwise a
    PNG upload (no JPEG traces) would be out-of-distribution for them.
    """
    img = img.convert("RGB")
    s = DETECTOR_SHORT_SIDE / min(img.size)
    if s < 1:
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    w, h = img.size
    if w > 2 * h:
        img = img.crop(((w - 2 * h) // 2, 0, (w - 2 * h) // 2 + 2 * h, h))
    elif h > 2 * w:
        img = img.crop((0, (h - 2 * w) // 2, w, (h - 2 * w) // 2 + 2 * w))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def load_path(path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")
