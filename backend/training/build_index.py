"""Build the protected reference collection index (pHash + CLIP + nearest-neighbour similarity).

References:
  * CryptoPunks train + val split (an existing NFT collection)
  * human artworks (WikiArt / museum paintings) from the real-world train + val splits
  * real NFT collections (BAYC, MAYC, Azuki, ...) from the train + val splits
Test-split images are deliberately left out: they play the role of new,
never-minted artworks in the evaluation.

Run from backend/:   python -m training.build_index
"""
import json
import time

import numpy as np
from tqdm import tqdm

from config import INDEX_CLIP, INDEX_FILES, INDEX_NN, INDEX_PHASH, REFERENCE_ART_DIR
from detection.clip_similarity import ClipEncoder
from detection.hashing import phash_int
from detection.preprocessing import load_path
from detection.reference_index import ART_PREFIX, nearest_neighbour_sims
from training.realworld_data import load_manifest
from training.split import load_split, paths

BATCH = 64


def main():
    split = load_split()
    punks = paths(split, "authentic", "train") + paths(split, "authentic", "val")
    art = [r["file"] for r in load_manifest()
           if (r["generator"] == "human_art" or r["generator"].startswith("nft_"))
           and r["split"] in ("train", "val")]
    entries = [(p.name, p) for p in punks] + [(ART_PREFIX + f, REFERENCE_ART_DIR / f) for f in art]

    enc = ClipEncoder()
    hashes, embs = [], []
    t = time.time()
    for i in tqdm(range(0, len(entries), BATCH), desc="indexing"):
        imgs = [load_path(p) for _, p in entries[i:i + BATCH]]
        hashes += [phash_int(im) for im in imgs]
        embs.append(enc.encode(imgs))
    clip = np.concatenate(embs)
    np.save(INDEX_PHASH, np.array(hashes, dtype=np.uint64))
    np.save(INDEX_CLIP, clip)
    np.save(INDEX_NN, nearest_neighbour_sims(clip))
    INDEX_FILES.write_text(json.dumps([name for name, _ in entries]))
    print(f"indexed {len(punks)} NFTs + {len(art)} protected artworks in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main()
