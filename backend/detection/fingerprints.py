"""Copy-detection fingerprints (Phase 4): PDQ hash, SSCD and DINOv2 global descriptors.

PDQ (Meta, BSD) is the industry-standard 256-bit perceptual hash (StopNCII, ThreatExchange); it
replaces the 64-bit pHash as the exact / near-exact filter. `pdq_dihedral` also returns the hashes
of the 7 flipped / rotated variants, so a mirrored copy still matches.

SSCD `sscd_disc_mixup` (Meta, MIT) is a ResNet-50 trained *for* copy detection (DISC21 winner
family): 512-d, L2-normalised. Input: images resized straight to 320x320 ("skew_320", the SSCD
README's batched setting). DINOv2 ViT-S/14 or B/14 (Apache-2.0): CLS token at 224x224.

`region_crops` cuts a query into overlapping windows so a copied work that is only part of a
collage still gets a descriptor of its own.
"""

import hashlib

import numpy as np
import pdqhash
import torch
from PIL import Image

from config import MODEL_DIR

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SSCD_FILE = MODEL_DIR / "copydet" / "sscd_disc_mixup.torchscript.pt"
# TorchScript carries code, so the file is pinned to the hash of Meta's published release.
SSCD_SHA256 = "9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56"
IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


# ---- PDQ ---------------------------------------------------------------------------------


def _pdq_input(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    if max(img.size) > 512:  # PDQ downsamples to 64x64 internally; this only saves time
        img = img.copy()
        img.thumbnail((512, 512), Image.BILINEAR)
    return np.ascontiguousarray(np.asarray(img))


def pdq(img: Image.Image) -> tuple[bytes, int]:
    """(32-byte hash, quality 0-100). Quality < 50 means a flat image: the hash is unreliable."""
    bits, quality = pdqhash.compute(_pdq_input(img))
    return np.packbits(bits.astype(np.uint8)).tobytes(), int(quality)


def pdq_dihedral(img: Image.Image) -> np.ndarray:
    """(8, 32) uint8: the hash of the image and of its 7 rotations / mirrors."""
    hashes, _ = pdqhash.compute_dihedral(_pdq_input(img))
    return np.stack([np.packbits(np.asarray(h, dtype=np.uint8)) for h in hashes])


def pdq_distances(query: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """Hamming distance(s). query (32,) or (k, 32) uint8, refs (n, 32) -> (n,), min over the k query hashes."""
    q = np.atleast_2d(query)
    return np.stack([_POPCOUNT[np.bitwise_xor(refs, h)].sum(axis=1, dtype=np.int32) for h in q]).min(axis=0)


# ---- Global descriptors ---------------------------------------------------------------------


def _to_tensor(images, size: int) -> torch.Tensor:
    arr = np.stack([np.asarray(im.convert("RGB").resize((size, size), Image.BILINEAR)) for im in images])
    x = torch.from_numpy(arr).permute(0, 3, 1, 2).float().div_(255)
    return (x - IMAGENET_MEAN) / IMAGENET_STD


class _Encoder:
    size = 224
    dim = 0
    batch = 64

    def _forward(self, x: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    @torch.no_grad()
    def encode(self, images, size: int | None = None) -> np.ndarray:
        """L2-normalised float32 descriptors, shape (n, dim). `size` overrides the input resolution
        (region crops run at a lower one to fit the CPU budget)."""
        out = []
        for s in range(0, len(images), self.batch):
            x = _to_tensor(images[s : s + self.batch], size or self.size).to(self.device, self.dtype)
            f = self._forward(x).float()
            out.append(torch.nn.functional.normalize(f, dim=-1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, self.dim), np.float32)

    def encode_one(self, img: Image.Image) -> np.ndarray:
        return self.encode([img])[0]


class SSCD(_Encoder):
    name, size, dim = "sscd_disc_mixup", 320, 512

    def __init__(self, device=DEVICE):
        if not SSCD_FILE.exists():
            raise FileNotFoundError(f"{SSCD_FILE} missing - run: python -m benchmark.fetch_models --only sscd")
        digest = hashlib.sha256(SSCD_FILE.read_bytes()).hexdigest()
        if digest != SSCD_SHA256:
            raise RuntimeError(f"{SSCD_FILE.name}: SHA-256 {digest} does not match the pinned release")
        self.device = device
        self.dtype = torch.float32  # the TorchScript model's GeM pooling collapses to a constant in fp16
        self.model = torch.jit.load(str(SSCD_FILE), map_location=device).eval().to(self.dtype)

    def _forward(self, x):
        return self.model(x)


class DinoV2(_Encoder):
    size = 224

    def __init__(self, repo="facebook/dinov2-small", device=DEVICE):
        from transformers import AutoModel

        self.name = repo.split("/")[-1]
        self.device = device
        self.dtype = torch.float16 if device == "cuda" else torch.float32
        try:
            m = AutoModel.from_pretrained(repo, local_files_only=True)
        except OSError:
            m = AutoModel.from_pretrained(repo)
        self.model = m.to(device, self.dtype).eval()
        self.dim = self.model.config.hidden_size

    def _forward(self, x):
        return self.model(pixel_values=x).pooler_output  # layer-normed CLS token


PIXEL_CHECK_PX = 128  # one 24x24 CryptoPunk grid cell = ~5x5 pixels: 64 px blurred it away


RESAMPLE = {"nearest": Image.NEAREST, "lanczos": Image.LANCZOS, "box": Image.BOX}


def rgb_small(img: Image.Image, resample: str = "nearest") -> np.ndarray:
    """128x128 RGB: the pixel-level check for exact copies of collection members. Colour matters
    (siblings often differ only in hue) and so does sharpness: bilinear resampling at 64 px averaged
    a one-cell accessory away, letting 3 genuine test siblings through as "exact" (benchmark).
    `resample` is the engine's `member_rule.resample` (benchmark/member_rule.py chooses it)."""
    return np.asarray(img.convert("RGB").resize((PIXEL_CHECK_PX,) * 2, RESAMPLE[resample]), dtype=np.int16)


def dihedral_small(a: np.ndarray) -> list[np.ndarray]:
    """The 8 rotations / mirrors of a square (h, w, 3) array (the same group pdq_dihedral hashes)."""
    out = []
    for k in range(4):
        r = np.rot90(a, k)
        out += [r, r[:, ::-1]]
    return out


def member_diff(q_variants: list[np.ndarray], ref: np.ndarray, level: int = 48) -> int:
    """Smallest differing-pixel count of any query variant (1 = as uploaded, 8 = dihedral) vs ref."""
    return min(differing_pixels(q, ref, level) for q in q_variants)


def differing_pixels(a: np.ndarray, b: np.ndarray, level: int = 48) -> int:
    """Pixels (of 128x128) where any channel differs by more than `level`. A re-upload / JPEG / noise
    copy of a pixel-art NFT differs by ~0; a sibling with another hat, eyes or colour by dozens."""
    return int((np.abs(a - b).max(axis=-1) > level).sum())


def region_crops(img: Image.Image, sizes=(0.5, 0.7), steps=3) -> list[Image.Image]:
    """Overlapping square-ish windows (fraction `s` of each side, `steps` x `steps` grid per size)."""
    w, h = img.size
    out = []
    for s in sizes:
        cw, ch = max(16, int(w * s)), max(16, int(h * s))
        for iy in range(steps):
            for ix in range(steps):
                x = int((w - cw) * ix / (steps - 1))
                y = int((h - ch) * iy / (steps - 1))
                out.append(img.crop((x, y, x + cw, y + ch)))
    return out
