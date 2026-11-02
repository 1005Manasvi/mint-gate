"""Synthetic forgery generators.

Used in three places:
  * training the ResNet (manipulated originals are labelled "forged", so the
    classifier cannot succeed purely by recognising the CryptoPunk style),
  * calibrating the duplicate / copy-move thresholds,
  * producing the demo samples.
"""

import io
import random

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont


def _textured_box(arr, rng, frac_lo, frac_hi, min_std=12.0, tries=25):
    """Pick a rectangle that actually contains structure (not flat background)."""
    h, w = arr.shape[:2]
    for _ in range(tries):
        bw = int(w * rng.uniform(frac_lo, frac_hi))
        bh = int(h * rng.uniform(frac_lo, frac_hi))
        x = rng.randint(0, w - bw)
        y = rng.randint(0, h - bh)
        if arr[y : y + bh, x : x + bw].std() >= min_std:
            return x, y, bw, bh
    return None


def copy_move(img: Image.Image, rng: random.Random) -> Image.Image:
    """Clone a textured region of the image onto a different location."""
    arr = np.asarray(img).copy()
    h, w = arr.shape[:2]
    box = _textured_box(arr, rng, 0.22, 0.35)
    if box is None:
        box = (w // 8, h // 8, w // 4, h // 4)
    x, y, bw, bh = box
    patch = arr[y : y + bh, x : x + bw].copy()
    for _ in range(25):
        nx, ny = rng.randint(0, w - bw), rng.randint(0, h - bh)
        far = abs(nx - x) > bw * 0.6 or abs(ny - y) > bh * 0.6
        if far and np.abs(arr[ny : ny + bh, nx : nx + bw].astype(int) - patch).mean() > 10:
            break
    arr[ny : ny + bh, nx : nx + bw] = patch
    return Image.fromarray(arr)


def splice(img: Image.Image, donor: Image.Image, rng: random.Random) -> Image.Image:
    """Paste a region taken from a different artwork."""
    out = img.copy()
    w, h = out.size
    donor = donor.resize((w, h), Image.BILINEAR)
    d_arr = np.asarray(donor)
    box = _textured_box(d_arr, rng, 0.25, 0.45, min_std=8.0) or (0, 0, w // 3, h // 3)
    x, y, bw, bh = box
    patch = donor.crop((x, y, x + bw, y + bh))
    out.paste(patch, (rng.randint(0, w - bw), rng.randint(0, h - bh)))
    return out


def random_forgery(img, donor, rng):
    return copy_move(img, rng) if rng.random() < 0.5 else splice(img, donor, rng)


# ---- Near-duplicate transforms (plagiarism attempts) ----------------------


def jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def near_duplicate(img: Image.Image, rng: random.Random) -> Image.Image:
    """A plagiarised copy: random mix of resize, crop, colour tweak, re-compression."""
    w, h = img.size
    out = img
    if rng.random() < 0.7:
        c = rng.uniform(0.0, 0.10)
        out = out.crop((int(w * c), int(h * c), int(w * (1 - c)), int(h * (1 - c))))
    scale = rng.uniform(0.6, 2.0)
    out = out.resize((max(32, int(w * scale)), max(32, int(h * scale))), Image.BICUBIC)
    if rng.random() < 0.6:
        out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.8, 1.2))
    if rng.random() < 0.5:
        out = ImageEnhance.Color(out).enhance(rng.uniform(0.7, 1.3))
    if rng.random() < 0.3:
        out = out.rotate(rng.uniform(-8, 8), resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    if rng.random() < 0.3:
        d = ImageDraw.Draw(out)
        d.text((5, 5), "@copycat", fill=(255, 255, 255), font=ImageFont.load_default())
    return jpeg(out, rng.randint(60, 95))
