"""AI-generated image detection: linear probe(s) on frozen vision backbones (UnivFD recipe).

Ojha et al. (CVPR 2023, "Towards Universal Fake Image Detectors"): a logistic regression on the
embeddings of a frozen foundation model generalises to *unseen* generators far better than a
fine-tuned CNN, because the feature space was never fitted to any generator. v1 used CLIP ViT-B/32;
Phase 5 benchmarks CLIP-L/14, SigLIP 2, DINOv2, ensembles and a LoRA fine-tune on the out-of-source
protocol (`training/ai_backbones.py`, docs/models.md) and ships the winner in model/ai_detector.json.

Probes are stored as plain JSON (no pickle), so loading the model can never execute code.
`spectral_features` (Durall et al., CVPR 2020) is the v1 spectral branch; its ensemble weight was
0, it is kept for the training scripts only.
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
_radius = np.sqrt(_xx**2 + _yy**2) / (CROP // 2)
_bin = np.clip((_radius * BINS).astype(int), 0, BINS - 1)
_bin_count = np.bincount(_bin.ravel(), minlength=BINS)


def spectral_features(img: Image.Image) -> np.ndarray:
    g = _crop(img)
    win = np.outer(np.hanning(CROP), np.hanning(CROP))
    spec = np.fft.fftshift(np.fft.fft2((g - g.mean()) * win))
    logp = np.log1p(np.abs(spec) ** 2)
    profile = np.bincount(_bin.ravel(), weights=logp.ravel(), minlength=BINS) / _bin_count
    profile = profile - profile[:4].mean()  # brightness / contrast invariance

    resid = g - cv2.medianBlur(g.astype(np.uint8), 3).astype(np.float32)
    r = resid.ravel()
    sd = r.std() + 1e-6
    kurt = ((r - r.mean()) ** 4).mean() / sd**4
    stats = np.array(
        [np.log(sd), np.log(np.abs(r).mean() + 1e-6), np.log(kurt + 1e-6), (np.abs(r) < 0.5).mean()]
    )  # fraction of perfectly flat pixels
    return np.concatenate([profile, stats]).astype(np.float64)


class _LogReg:
    def __init__(self, p):
        self.mean, self.scale = np.array(p["mean"]), np.array(p["scale"])
        self.coef, self.intercept = np.array(p["coef"]), float(p["intercept"])

    def __call__(self, x):
        z = (np.asarray(x, dtype=np.float64) - self.mean) / self.scale
        return float(1.0 / (1.0 + np.exp(-(z @ self.coef + self.intercept))))


class AIDetector:
    """Probability that an image is AI-generated: linear probe(s) on frozen backbone(s).

    model/ai_detector.json, format 2 (Phase 5, `training.ai_backbones ship`):
      {"format": 2, "version", "backbones": [key, ...], "probes": {key: LR params},
       "weights": {key: w}, "metrics": {...}}   probability = sum_k w_k * probe_k(features_k)
    The v1 file (spectral + CLIP-B/32 probe, `weight_clip` 1.0) still loads: it becomes the
    single backbone "clip-b32" (its spectral branch had weight 0 and is dropped).
    """

    def __init__(self, path=AI_DETECTOR_FILE, backbones=None):
        self.ready = path.exists()
        self.metrics, self.keys, self.version = {}, [], "none"
        if not self.ready:
            return
        p = json.loads(path.read_text())
        self.metrics = p.get("metrics", {})
        self.version = p.get("version", "unknown")
        if p.get("format") == 2:
            self.keys = list(p["backbones"])
            self.probes = {k: _LogReg(p["probes"][k]) for k in self.keys}
            self.weights = {k: float(p["weights"][k]) for k in self.keys}
        else:  # v1 file
            self.keys = ["clip-b32"]
            self.probes = {"clip-b32": _LogReg(p["clip_probe"])}
            self.weights = {"clip-b32": 1.0}
        self.thresholds = p.get("thresholds")
        if backbones is None:
            from detection.backbones import Backbone

            backbones = {k: Backbone(k) for k in self.keys}
        self.backbones = backbones

    def predict(self, img: Image.Image, clip_emb=None) -> dict:
        """{'probability', 'probes': {key: p}}. `img` = `preprocessing.detector_view(...)`.
        (`clip_emb` is accepted for the old call signature and ignored.)"""
        if not self.ready:
            return {"probability": 0.0, "probes": {}}
        per = {k: self.probes[k](self.backbones[k].encode_one(img)) for k in self.keys}
        prob = sum(self.weights[k] * v for k, v in per.items())
        return {"probability": round(float(prob), 4), "probes": {k: round(float(v), 4) for k, v in per.items()}}
