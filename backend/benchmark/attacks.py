"""Copy attacks for the copy-detection benchmark (richer than `detection.manipulations.near_duplicate`).

Every attack takes a PIL RGB image and a `random.Random` and returns a new RGB image. The same
attacks are applied to the distractors, so "this image was edited" is never a copy signal.
`collage` also needs a background image; `regen` (img2img) runs in its own GPU pass
(`benchmark.build_copydet --regen`), so it is not in `ATTACKS`.
"""

import io

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

WORDS = ("@copycat", "FOR SALE", "rare mint", "#1/1", "OpenSea", "official drop", "© 2026", "genesis")


def _font(size):
    return ImageFont.load_default(size=max(8, int(size)))


def jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(quality))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def crop(img, rng):
    """Remove 5-40 % of the area (random aspect + position)."""
    w, h = img.size
    keep = 1 - rng.uniform(0.05, 0.40)
    ar = rng.uniform(0.75, 1.33)
    cw = int(min(w, max(16, w * np.sqrt(keep) * np.sqrt(ar))))
    ch = int(min(h, max(16, h * keep * w / max(cw, 1))))
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    return img.crop((x, y, x + cw, y + ch))


def rotate(img, rng):
    angle = rng.uniform(5, 30) * rng.choice((-1, 1))
    fill = tuple(rng.randint(0, 255) for _ in range(3))
    return img.rotate(angle, resample=Image.BICUBIC, expand=rng.random() < 0.5, fillcolor=fill)


def flip(img, rng):
    return ImageOps.mirror(img) if rng.random() < 0.8 else ImageOps.flip(img)


def perspective(img, rng):
    w, h = img.size
    a = np.asarray(img)
    d = rng.uniform(0.05, 0.2)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jit = lambda: rng.uniform(0, d)  # noqa: E731
    dst = np.float32(
        [
            [w * jit(), h * jit()],
            [w * (1 - jit()), h * jit()],
            [w * (1 - jit()), h * (1 - jit())],
            [w * jit(), h * (1 - jit())],
        ]
    )
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(a, m, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return Image.fromarray(out)


def recolour(img, rng):
    kind = rng.choice(("hue", "hue", "colour", "gray", "invert_channels"))
    if kind == "hue":
        hsv = np.asarray(img.convert("HSV")).copy()
        hsv[..., 0] = (hsv[..., 0].astype(int) + rng.randint(20, 235)) % 256
        return Image.fromarray(hsv, "HSV").convert("RGB")
    if kind == "colour":
        out = ImageEnhance.Color(img).enhance(rng.uniform(0.2, 2.0))
        out = ImageEnhance.Contrast(out).enhance(rng.uniform(0.6, 1.5))
        return ImageEnhance.Brightness(out).enhance(rng.uniform(0.7, 1.3))
    if kind == "gray":
        return ImageOps.grayscale(img).convert("RGB")
    a = np.asarray(img)
    return Image.fromarray(a[..., list(rng.sample([0, 1, 2], 3))].copy())


def blur(img, rng):
    r = rng.uniform(1.0, 4.0) * max(img.size) / 512
    return img.filter(ImageFilter.GaussianBlur(r))


def noise(img, rng):
    a = np.asarray(img).astype(np.float32)
    nrng = np.random.default_rng(rng.randint(0, 2**31))
    a += nrng.normal(0, rng.uniform(5, 25), a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def jpeg_attack(img, rng):
    return jpeg(img, rng.randint(30, 95))


def overlay(img, rng):
    """Text, a logo-like badge or a tiled semi-transparent watermark."""
    out = img.convert("RGBA")
    w, h = out.size
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    kind = rng.choice(("text", "logo", "watermark"))
    col = tuple(rng.randint(0, 255) for _ in range(3))
    if kind == "text":
        f = _font(h * rng.uniform(0.05, 0.12))
        d.text((rng.uniform(0, w * 0.5), rng.uniform(0, h * 0.85)), rng.choice(WORDS), fill=col + (255,), font=f)
    elif kind == "logo":
        s = int(min(w, h) * rng.uniform(0.12, 0.25))
        x, y = rng.randint(0, w - s), rng.randint(0, h - s)
        d.ellipse((x, y, x + s, y + s), fill=col + (230,))
        d.text((x + s * 0.2, y + s * 0.35), "NFT", fill=(255, 255, 255, 255), font=_font(s * 0.3))
    else:
        f = _font(h * 0.06)
        a = rng.randint(60, 120)
        for y in range(0, h, max(1, int(h * 0.15))):
            for x in range(-w // 4, w, max(1, int(w * 0.35))):
                d.text((x + (y % 2) * 20, y), rng.choice(WORDS), fill=col + (a,), font=f)
    return Image.alpha_composite(out, layer).convert("RGB")


def border(img, rng):
    w, h = img.size
    p = int(max(w, h) * rng.uniform(0.05, 0.2))
    col = tuple(rng.randint(0, 255) for _ in range(3))
    out = ImageOps.expand(img, border=p, fill=col)
    if rng.random() < 0.5:  # double frame
        out = ImageOps.expand(out, border=max(2, p // 4), fill=tuple(255 - c for c in col))
    return out


def screenshot(img, rng):
    """Shrunk, re-upscaled and placed in a 16:9 / phone 'screen' with UI bars."""
    w, h = img.size
    s = rng.uniform(0.3, 0.6)
    small = img.resize((max(16, int(w * s)), max(16, int(h * s))), Image.BILINEAR)
    sw, sh = (1280, 720) if rng.random() < 0.5 else (720, 1280)
    canvas = Image.new("RGB", (sw, sh), tuple(rng.randint(200, 255) for _ in range(3)))
    fit = min(sw * 0.8 / small.width, sh * 0.7 / small.height)
    art = small.resize((max(16, int(small.width * fit)), max(16, int(small.height * fit))), Image.BILINEAR)
    canvas.paste(art, ((sw - art.width) // 2, (sh - art.height) // 2))
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, sw, int(sh * 0.06)), fill=(30, 30, 30))
    d.rectangle((0, int(sh * 0.93), sw, sh), fill=(240, 240, 240))
    d.text((10, 5), "12:34   marketplace.example", fill=(255, 255, 255), font=_font(sh * 0.03))
    return canvas


def collage(img, rng, background):
    """The copied work fills 30-60 % of a larger canvas made from another image."""
    frac = rng.uniform(0.3, 0.6)
    bw, bh = background.size
    scale = max(1.0, 512 / max(bw, bh))
    canvas = background.resize((int(bw * scale), int(bh * scale)), Image.BILINEAR)
    cw, ch = canvas.size
    ar = img.width / img.height
    tw = int(np.sqrt(frac * cw * ch * ar))
    th = int(tw / ar)
    if tw > cw or th > ch:
        f = min(cw / tw, ch / th)
        tw, th = int(tw * f), int(th * f)
    art = img.resize((max(16, tw), max(16, th)), Image.BICUBIC)
    canvas.paste(art, (rng.randint(0, cw - art.width), rng.randint(0, ch - art.height)))
    return canvas


SINGLE = {
    "crop": crop,
    "rotate": rotate,
    "flip": flip,
    "perspective": perspective,
    "recolour": recolour,
    "blur": blur,
    "noise": noise,
    "jpeg": jpeg_attack,
    "overlay": overlay,
    "border": border,
    "screenshot": screenshot,
}
# Weighted toward realistic plagiarism: most copies get 1 edit, "combo" gets 2-3 stacked edits.
ATTACKS = (*SINGLE, "collage", "combo")


def apply(name, img, rng, background=None):
    img = img.convert("RGB")
    if max(img.size) > 1024:  # keep the dataset small; the pipeline sees uploads <= 40 MP anyway
        img.thumbnail((1024, 1024), Image.LANCZOS)
    if name == "collage":
        out = collage(img, rng, background)
    elif name == "combo":
        out = img
        for n in rng.sample([k for k in SINGLE if k != "jpeg"], rng.randint(2, 3)):
            out = SINGLE[n](out, rng)
    else:
        out = SINGLE[name](img, rng)
    return out if name == "jpeg" else jpeg(out, rng.randint(60, 95))
