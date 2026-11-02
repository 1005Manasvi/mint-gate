"""Perceptual hashing (pHash) and fast Hamming search over the reference index."""

import imagehash
import numpy as np
from PIL import Image


def phash_int(img: Image.Image) -> int:
    """64-bit DCT perceptual hash as an unsigned integer."""
    return int(str(imagehash.phash(img)), 16)


def hamming_to_all(query: int, index: np.ndarray) -> np.ndarray:
    """Hamming distance between `query` and every uint64 hash in `index`."""
    x = np.bitwise_xor(index, np.uint64(query))
    return np.unpackbits(x.view(np.uint8).reshape(-1, 8), axis=1).sum(axis=1)
