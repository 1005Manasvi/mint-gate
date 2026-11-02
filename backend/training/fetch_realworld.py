"""Download and normalise the real-world training data.

Sources (Hugging Face Hub):
  TheKernel01/Tiny-GenImage            real photos (ImageNet) vs 7 generators   CC BY-NC-SA 4.0
  DataScienceProject/Art_Images_Ai_And_Real_  human art (WikiArt/Met) vs Ideogram,
                                       SDXL, Civitai, Copilot, OpenArt          (research use)
  OmAlve/Real-VS-AI-Art                high-resolution real vs AI artworks      (research use)
plus a sample of the local CryptoPunks / AI-art data.

Bias control ("Fake or JPEG?", Grommelt et al. 2024): in GenImage the real
images are JPEGs and the fakes are PNGs, so a detector can learn "JPEG =
real". Every image here, from both classes, is therefore put through the
SAME normalisation: shorter side resized to 256 px, then re-encoded as JPEG
with a random quality in [88, 96]. Format and resolution carry no label
information afterwards.

Output: backend/data/realworld/images/*.jpg + manifest.json
        (one record per image: file, label, source, generator, split)

Split: Midjourney images are held out entirely (test_unseen) to measure
generalisation to a generator never seen in training; everything else is
split 80/10/10 at random.

Run from backend/:   python -m training.fetch_realworld
"""

import hashlib
import io
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem, hf_hub_download, list_repo_files
from PIL import Image
from tqdm import tqdm

from config import AI_DIR, BACKEND_DIR, ORIGINALS_DIR

OUT = BACKEND_DIR / "data" / "realworld"
IMG_DIR = OUT / "images"
MANIFEST = OUT / "manifest.json"
SHORT_SIDE = 256
HELD_OUT_GENERATOR = "Midjourney"
GENIMAGE_NAMES = ["Real", "ADM", "BigGAN", "GLIDE", "Midjourney", "SD14", "SD15", "VQDM", "Wukong"]
Image.MAX_IMAGE_PIXELS = 120_000_000

rng = random.Random(1234)


def normalise(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    s = SHORT_SIDE / min(img.size)
    if s < 1:
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
    w, h = img.size  # cap extreme aspect ratios at 2:1 (centre crop)
    if w > 2 * h:
        img = img.crop(((w - 2 * h) // 2, 0, (w - 2 * h) // 2 + 2 * h, h))
    elif h > 2 * w:
        img = img.crop((0, (h - 2 * w) // 2, w, (h - 2 * w) // 2 + 2 * w))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=rng.randint(88, 96))
    return buf.getvalue()


class Writer:
    def __init__(self):
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        self.records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
        self.seen = {r["file"] for r in self.records}

    def add(self, key, img_or_bytes, label, source, generator):
        name = f"{key}.jpg"
        if name in self.seen:
            return
        try:
            img = img_or_bytes if isinstance(img_or_bytes, Image.Image) else Image.open(io.BytesIO(img_or_bytes))
            data = normalise(img)
        except Exception as exc:  # corrupt image in a public dataset: skip it
            print("skip", key, exc)
            return
        (IMG_DIR / name).write_bytes(data)
        self.records.append({"file": name, "label": label, "source": source, "generator": generator})
        self.seen.add(name)

    def save(self):
        MANIFEST.write_text(json.dumps(self.records))


def download(repo, filename):
    for attempt in range(20):  # flaky connection: hf_hub_download resumes partial files
        try:
            return hf_hub_download(repo, filename, repo_type="dataset")
        except Exception as exc:
            print(f"  retry {attempt + 1} {filename}: {str(exc)[:80]}")
    raise RuntimeError(f"could not download {filename}")


def parquet_rows(path, columns):
    pf = pq.ParquetFile(path)
    for rg in range(pf.num_row_groups):
        yield from pf.read_row_group(rg, columns=columns).to_pylist()


def fetch_genimage(w):
    repo = "TheKernel01/Tiny-GenImage"
    files = [
        "data/train-00000-of-00014.parquet",
        "data/train-00001-of-00014.parquet",
        "data/train-00002-of-00014.parquet",
        "data/validation-00000-of-00004.parquet",
    ]
    for f in files:
        local = download(repo, f)
        stem = Path(f).stem
        for i, row in enumerate(tqdm(parquet_rows(local, ["image", "label", "generator"]), desc=stem)):
            gen = GENIMAGE_NAMES[row["generator"]]
            label = "authentic" if row["label"] == 0 else "ai"
            w.add(
                f"genimage_{stem}_{i}",
                row["image"]["bytes"],
                label,
                "genimage",
                "real_photo" if label == "authentic" else gen,
            )
        w.save()


def fetch_art_images(w):
    repo = "DataScienceProject/Art_Images_Ai_And_Real_"
    # Streamed straight into memory (16 parallel connections): some file names
    # exceed Windows' 260-character path limit once placed in the HF cache.
    fs = HfFileSystem()
    files = [
        f
        for f in list_repo_files(repo, repo_type="dataset")
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        and f.split("/")[-2] in ("real", "fake")
        and "ela_output" not in f
    ]

    def key_of(f):
        return "artimg_" + hashlib.sha1(f.encode()).hexdigest()[:16]

    def fetch(f):
        for attempt in range(10):
            try:
                with fs.open(f"datasets/{repo}/{f}", "rb") as fh:
                    return f, fh.read()
            except Exception:
                time.sleep(2 + attempt)
        return f, None

    todo = [f for f in files if f"{key_of(f)}.jpg" not in w.seen]
    with ThreadPoolExecutor(16) as ex:
        for f, data in tqdm(ex.map(fetch, todo), total=len(todo), desc="art-images"):
            if data is None:
                print("failed", f)
                continue
            label = "authentic" if f.split("/")[-2] == "real" else "ai"
            w.add(key_of(f), data, label, "art_images", "human_art" if label == "authentic" else "mixed_art_generators")
    w.save()


def fetch_omalve(w):
    repo = "OmAlve/Real-VS-AI-Art"
    for f in ["data/train-00000-of-00002.parquet", "data/train-00001-of-00002.parquet"]:
        local = download(repo, f)
        stem = Path(f).stem
        for i, row in enumerate(tqdm(parquet_rows(local, ["image", "label"]), desc=stem)):
            label = "ai" if row["label"] == 0 else "authentic"  # 0 = AI_Art, 1 = Real_Art
            w.add(
                f"realvsai_{stem}_{i}",
                row["image"]["bytes"],
                label,
                "real_vs_ai_art",
                "human_art" if label == "authentic" else "mixed_art_generators",
            )
    w.save()


def add_local(w, n=2000):
    """Keep pixel-art NFTs in the mix (a real NFT genre), but no longer dominant."""
    for folder, label, gen in ((ORIGINALS_DIR, "authentic", "cryptopunk"), (AI_DIR, "ai", "local_ai_art")):
        if not folder.exists():  # source folder removed to save space; its sample is already in images/
            print(f"skip {folder} (not present)")
            continue
        files = sorted(folder.iterdir())
        for p in tqdm(rng.sample(files, min(n, len(files))), desc=f"local {label}"):
            w.add(f"local_{label}_{p.stem}", Image.open(p), label, "local", gen)
    w.save()


def assign_splits(w):
    split_rng = random.Random(42)
    for r in w.records:
        if r["generator"] == HELD_OUT_GENERATOR:
            r["split"] = "test_unseen"
        else:
            x = split_rng.random()
            r["split"] = "test" if x < 0.1 else "val" if x < 0.2 else "train"
    w.save()


def main():
    w = Writer()
    add_local(w)
    fetch_art_images(w)
    fetch_omalve(w)
    fetch_genimage(w)
    assign_splits(w)
    from collections import Counter

    print(Counter((r["split"], r["label"]) for r in w.records))
    print(Counter((r["label"], r["generator"]) for r in w.records))


if __name__ == "__main__":
    main()
