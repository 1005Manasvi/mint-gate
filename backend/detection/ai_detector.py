"""AI-generated / GAN content detection: an ensemble of two complementary detectors.

1. Spectral fingerprint (Durall et al., CVPR 2020, "Watch your Up-Convolution"):
   generators up-sample with transposed convolutions / interpolation, leaving
   traces in the high-frequency power spectrum. Features = azimuthally
   averaged log power spectrum (64 bins) + noise-residual statistics.

2. CLIP linear probe (Ojha et al., CVPR 2023, "Towards Universal Fake Image
   Detectors"): a linear classifier on frozen CLIP ViT-B/32 embeddings. CLIP's
   feature space was never trained to separate real from fake, which makes the
   probe generalise much better to *unseen* generators than a fine-tuned CNN.

Both are logistic regressions trained by `training/train_ai_detector.py` and
stored as plain JSON (no pickle), so loading the model can never execute code.
The ensemble output is w * clip_probe + (1 - w) * spectral, with w chosen on the
validation split (stored as `weight_clip`).
"""
import json

import cv2
import numpy as np
from PIL import Image

from config import AI_DETECTOR_FILE

CROP = 256
BINS = 64


def _crop(img: Image.Image) -> np.ndarray:
    """Native-resolution centre crop so resampling does not erase artefacts."""
    g = img.convert("L")
    if min(g.size) < CROP:
        s = CROP / min(g.size)
        g = g.resize((max(CROP, round(g.width * s)), max(CROP, round(g.height * s))), Image.BICUBIC)
    l, t = (g.width - CROP) // 2, (g.height - CROP) // 2
    return np.asarray(g.crop((l, t, l + CROP, t + CROP)), dtype=np.float32)


_yy, _xx = np.indices((CROP, CROP)) - CROP // 2
_radius = np.sqrt(_xx ** 2 + _yy ** 2) / (CROP // 2)
_bin = np.clip((_radius * BINS).astype(int), 0, BINS - 1)
_bin_count = np.bincount(_bin.ravel(), minlength=BINS)


def spectral_features(img: Image.Image) -> np.ndarray:
    g = _crop(img)
    win = np.outer(np.hanning(CROP), np.hanning(CROP))
    spec = np.fft.fftshift(np.fft.fft2((g - g.mean()) * win))
    logp = np.log1p(np.abs(spec) ** 2)
    profile = np.bincount(_bin.ravel(), weights=logp.ravel(), minlength=BINS) / _bin_count
    profile = profile - profile[:4].mean()            # brightness / contrast invariance

    resid = g - cv2.medianBlur(g.astype(np.uint8), 3).astype(np.float32)
    r = resid.ravel()
    sd = r.std() + 1e-6
    kurt = ((r - r.mean()) ** 4).mean() / sd ** 4
    stats = np.array([np.log(sd), np.log(np.abs(r).mean() + 1e-6), np.log(kurt + 1e-6),
                      (np.abs(r) < 0.5).mean()])     # fraction of perfectly flat pixels
    return np.concatenate([profile, stats]).astype(np.float64)


class _LogReg:
    def __init__(self, p):
        self.mean, self.scale = np.array(p["mean"]), np.array(p["scale"])
        self.coef, self.intercept = np.array(p["coef"]), float(p["intercept"])

    def __call__(self, x):
        z = (np.asarray(x, dtype=np.float64) - self.mean) / self.scale
        return float(1.0 / (1.0 + np.exp(-(z @ self.coef + self.intercept))))


class AIDetector:
    def __init__(self, path=AI_DETECTOR_FILE):
        self.ready = path.exists()
        self.metrics = {}
        if not self.ready:
            return
        p = json.loads(path.read_text())
        self.metrics = p.get("metrics", {})
        self.version = p.get("version", "spectral-only")
        self.weight_clip = float(p.get("weight_clip", 0.5))
        if "spectral" in p:  # ensemble format
            self.spectral = _LogReg(p["spectral"])
            self.probe = _LogReg(p["clip_probe"]) if "clip_probe" in p else None
        else:  # legacy single-model file
            self.spectral, self.probe = _LogReg(p), None

    def predict(self, img: Image.Image, clip_emb=None) -> dict:
        """Returns {'spectral', 'clip_probe', 'probability'} - probability the image is AI-generated.

        `img` should be `preprocessing.detector_view(...)`; `clip_emb` the CLIP
        embedding of that same view.
        """
        if not self.ready:
            return {"spectral": 0.0, "clip_probe": None, "probability": 0.0}
        s = self.spectral(spectral_features(img))
        c = self.probe(clip_emb) if (self.probe is not None and clip_emb is not None) else None
        prob = s if c is None else self.weight_clip * c + (1 - self.weight_clip) * s
        return {"spectral": round(s, 4), "clip_probe": None if c is None else round(c, 4),
                "probability": round(prob, 4)}
