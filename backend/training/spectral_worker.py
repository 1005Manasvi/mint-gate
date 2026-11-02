"""Lightweight worker for parallel spectral-feature extraction.

Kept separate on purpose: worker processes import only this module, so they
load NumPy/OpenCV but never PyTorch/CUDA. On Windows each process that
imports torch commits ~2 GB of virtual memory; spawning one per core exhausts
the paging file (WinError 1455).
"""

from PIL import Image

from detection.ai_detector import spectral_features
from training.fetch_realworld import IMG_DIR


def spectral_of(file_name: str):
    with Image.open(IMG_DIR / file_name) as im:
        return spectral_features(im.convert("RGB"))
