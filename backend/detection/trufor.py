"""TruFor (Guillaro et al., CVPR 2023): image-level manipulation score + localisation + reliability map.

TruFor fuses RGB with Noiseprint++ (a learned camera / processing "fingerprint" that local edits
break) in a SegFormer-B2 encoder and outputs
  map    per-pixel probability of manipulation  (localisation)
  conf   per-pixel reliability of that map       (low on flat / saturated regions)
  score  image-level manipulation probability    (confidence-weighted pooling of map + conf)

**Licence: non-commercial / nonprofit only** (GRIP-UNINA; code in `third_party/trufor`, weights
fetched by `benchmark.forgery fetch`, SHA-256 pinned). The permissive-only configuration
(`MINTGATE_PERMISSIVE_ONLY=1`) never constructs this class.

Images larger than `MAX_SIDE` are downscaled first (4 GB GPU; CPU time grows with the pixel count).
Downscaling weakens the Noiseprint++ trace, which the benchmark measures as-is.
"""

import base64
import hashlib
import io
import time

import numpy as np
import torch
from PIL import Image

from config import MODEL_DIR

WEIGHTS = MODEL_DIR / "trufor" / "trufor.safetensors"  # converted once from the published .pth.tar
WEIGHTS_URL = "https://www.grip.unina.it/download/prog/TruFor/TruFor_weights.zip"
MAX_SIDE = 1024
VERSION = "trufor-cvpr2023"


class _Cfg(dict):
    """Attribute access + `in`, the subset of yacs.CfgNode TruFor's builder uses."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


CONFIG = _Cfg(
    MODEL=_Cfg(
        NAME="detconfcmx",
        MODS=("RGB", "NP++"),
        PRETRAINED=None,
        EXTRA=_Cfg(
            BACKBONE="mit_b2",
            DECODER="MLPDecoder",
            DECODER_EMBED_DIM=512,
            PREPRC="imagenet",
            BN_EPS=0.001,
            BN_MOMENTUM=0.1,
            DETECTION="confpool",
            CONF=True,
        ),
    ),
    DATASET=_Cfg(NUM_CLASSES=2),
)


def _check_weights():
    import json

    man = WEIGHTS.parent / "manifest.json"
    if not WEIGHTS.exists() or not man.exists():
        raise FileNotFoundError(f"{WEIGHTS} missing: run `python -m benchmark.forgery fetch`")
    want = json.loads(man.read_text())["sha256"]
    got = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
    if got != want:
        raise RuntimeError(f"{WEIGHTS}: SHA-256 mismatch")


class TruFor:
    def __init__(self, device=None, max_side=MAX_SIDE):
        from third_party.trufor.models.cmx.builder_np_conf import myEncoderDecoder

        _check_weights()
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.max_side = max_side
        self.model = myEncoderDecoder(cfg=CONFIG)
        from safetensors.torch import load_file

        self.model.load_state_dict(load_file(str(WEIGHTS)))  # safetensors: no pickle at runtime
        self.model = self.model.to(self.device).eval()
        self.version = VERSION

    def _resize(self, img):
        s = self.max_side / max(img.size)
        if s < 1:
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.BICUBIC)
        return img

    @torch.inference_mode()
    def analyse(self, img: Image.Image) -> dict:
        t0 = time.time()
        img = self._resize(img.convert("RGB"))
        x = torch.tensor(np.asarray(img).transpose(2, 0, 1), dtype=torch.float32)[None] / 256.0
        pred, conf, det, _ = self.model(x.to(self.device))
        heat = torch.softmax(pred[0], 0)[1].float().cpu().numpy()
        rel = torch.sigmoid(conf[0, 0]).float().cpu().numpy()
        return {
            "score": round(float(torch.sigmoid(det).item()), 4),
            "map": heat,
            "conf": rel,
            "size": img.size,
            "ms": int((time.time() - t0) * 1000),
        }


def heatmap(img: Image.Image, res: dict, max_side=512) -> str:
    """Evidence image: the photo with TruFor's localisation map in red (alpha = map x reliability).
    Base64 PNG, like `copy_move.heatmap`."""
    w, h = res["size"]
    s = min(1.0, max_side / max(w, h))
    size = (max(1, round(w * s)), max(1, round(h * s)))
    base = img.convert("RGB").resize(size, Image.BILINEAR).convert("RGBA")
    m = np.asarray(Image.fromarray((res["map"] * 255).astype(np.uint8)).resize(size, Image.BILINEAR), float) / 255
    c = np.asarray(Image.fromarray((res["conf"] * 255).astype(np.uint8)).resize(size, Image.BILINEAR), float) / 255
    over = np.zeros((size[1], size[0], 4), np.uint8)
    over[..., 0], over[..., 2] = 255, 60
    over[..., 3] = (np.clip(m * (0.4 + 0.6 * c), 0, 1) * 170).astype(np.uint8)
    comp = Image.alpha_composite(base, Image.fromarray(over, "RGBA")).convert("RGB")
    buf = io.BytesIO()
    comp.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
