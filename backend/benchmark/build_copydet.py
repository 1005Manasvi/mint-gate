"""Build the copy-detection benchmark dataset (queries + reference DB), resumably.

Reference DB (the "protected" works the engine searches):
  painting  1000 one-off artworks (WikiArt / museum, train+val pool)
  nft       1500 CryptoPunks + 1500 tokens of 7 real collections (BAYC, Azuki, MAYC, ...)
Queries (DISC21 style: most queries are NOT copies):
  positives    300 per domain: an attacked copy of one reference
  distractors 3000 per domain: genuine non-copies, attacked with the SAME attack mix
               painting: other paintings + real photos; nft: siblings from the same collections
Attacks (benchmark/attacks.py): crop, rotate, flip, perspective, recolour, blur, noise, jpeg,
overlay, border, screenshot, collage, combo and regen (SD 1.5 img2img, strength 0.2-0.4).
Every query gets a val/test split (positives by reference): tune on val, report on test.

Run from backend/:
  python -m benchmark.build_copydet            # sample + write all non-regen queries (CPU)
  python -m benchmark.build_copydet --regen    # img2img queries (GPU, ~4 GB fp16; run alone)
"""

import argparse
import hashlib
import json
import random
import time

import numpy as np
import pdqhash
from PIL import Image

from benchmark.attacks import ATTACKS, apply
from config import BACKEND_DIR, ORIGINALS_DIR, REFERENCE_ART_DIR
from detection.preprocessing import load_path
from training.realworld_data import load_manifest
from training.split import load_split, paths

BENCH_DIR = BACKEND_DIR / "data" / "benchmark" / "copydet"
QUERY_DIR = BENCH_DIR / "queries"
MANIFEST = BENCH_DIR / "manifest.json"
ATTACK_SET = (*ATTACKS, "regen")
N_REF = {"painting": 1000, "punks": 1500, "nft": 1500}
N_POS, N_DIS = 300, 3000
# A distractor is dropped as a dataset duplicate of a reference only if it is PDQ-close AND nearly
# pixel-identical: collection siblings are often PDQ-close, and they are exactly the hard distractors.
NEAR_DUP_PDQ = 8
NEAR_DUP_MAE = 4.0  # mean absolute grey-level difference at 32x32


def _split_of(key: str) -> str:
    return "val" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


def source_path(name: str):
    return REFERENCE_ART_DIR / name[4:] if name.startswith("art/") else ORIGINALS_DIR / name


def pdq_bits(img):
    h, _ = pdqhash.compute(np.asarray(img.convert("RGB")))
    return np.packbits(h.astype(np.uint8))


def tiny(img):
    return np.asarray(img.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.float32)


def sample(rng):
    man = load_manifest()
    paint = [r["file"] for r in man if r["generator"] == "human_art"]
    photos = [r["file"] for r in man if r["generator"] == "real_photo"]
    nft = [r for r in man if r["generator"].startswith("nft_")]
    split = load_split()
    punks = sorted({p.name for k in ("train", "val", "test") for p in paths(split, "authentic", k)})
    for pool in (paint, photos, nft, punks):
        rng.shuffle(pool)

    refs = [{"name": "art/" + f, "domain": "painting", "collection": None} for f in paint[: N_REF["painting"]]]
    refs += [{"name": f, "domain": "nft", "collection": "cryptopunks"} for f in punks[: N_REF["punks"]]]
    refs += [{"name": "art/" + r["file"], "domain": "nft", "collection": r["generator"]} for r in nft[: N_REF["nft"]]]
    dis = {
        "painting": ["art/" + f for f in paint[N_REF["painting"] :]] + ["art/" + f for f in photos],
        "nft": [f for f in punks[N_REF["punks"] : N_REF["punks"] + 2000]]
        + ["art/" + r["file"] for r in nft[N_REF["nft"] :]],
    }
    for pool in dis.values():
        rng.shuffle(pool)
    return refs, dis


def plan(rng):
    refs, dis_pool = sample(rng)
    print("[build] hashing references for the near-duplicate filter", flush=True)
    ref_imgs = [load_path(source_path(r["name"])) for r in refs]
    ref_pdq = np.stack([pdq_bits(im) for im in ref_imgs])
    ref_tiny = np.stack([tiny(im) for im in ref_imgs])
    del ref_imgs
    queries = []
    for dom in ("painting", "nft"):
        dom_refs = [r for r in refs if r["domain"] == dom]
        pool, dropped, kept = dis_pool[dom], 0, []
        for name in pool:
            if len(kept) >= N_DIS:
                break
            img = load_path(source_path(name))
            close = np.flatnonzero(np.unpackbits(ref_pdq ^ pdq_bits(img), axis=1).sum(1) <= NEAR_DUP_PDQ)
            if len(close) and np.abs(ref_tiny[close] - tiny(img)).mean(axis=(1, 2)).min() < NEAR_DUP_MAE:
                dropped += 1
                continue
            kept.append(name)
        print(f"[build] {dom}: {len(kept)} distractors, {dropped} dropped as duplicates of a reference", flush=True)
        backgrounds = kept[:]  # collage canvases come from distractors, never from references
        for i, r in enumerate(rng.sample(dom_refs, N_POS)):
            queries.append(
                {
                    "id": f"{dom}_pos_{i:04d}",
                    "domain": dom,
                    "source": r["name"],
                    "gt": r["name"],
                    "attack": ATTACK_SET[i % len(ATTACK_SET)],
                    "background": rng.choice(backgrounds),
                    "split": _split_of(r["name"]),
                }
            )
        for i, name in enumerate(kept):
            queries.append(
                {
                    "id": f"{dom}_dis_{i:04d}",
                    "domain": dom,
                    "source": name,
                    "gt": None,
                    "attack": ATTACK_SET[i % len(ATTACK_SET)],
                    "background": rng.choice([b for b in backgrounds[:50] if b != name]),
                    "split": _split_of(name),
                }
            )
    return {"created": int(time.time()), "refs": refs, "queries": queries}


def write_queries(man, attack_filter):
    QUERY_DIR.mkdir(parents=True, exist_ok=True)
    todo = [q for q in man["queries"] if attack_filter(q["attack"]) and not (QUERY_DIR / f"{q['id']}.jpg").exists()]
    print(f"[build] {len(todo)} queries to write", flush=True)
    return todo


def build(args):
    if MANIFEST.exists():
        man = json.loads(MANIFEST.read_text())
    else:
        man = plan(random.Random(2026))
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(man, indent=1))
    t = time.time()
    for n, q in enumerate(write_queries(man, lambda a: a != "regen")):
        rng = random.Random(q["id"])
        img = load_path(source_path(q["source"]))
        bg = load_path(source_path(q["background"])) if q["attack"] == "collage" else None
        apply(q["attack"], img, rng, background=bg).save(QUERY_DIR / f"{q['id']}.jpg", quality=95)
        if n % 500 == 0:
            print(f"[build] {n} written ({time.time() - t:.0f}s)", flush=True)
    print(f"[build] done: {len(man['refs'])} refs, {len(man['queries'])} queries", flush=True)


def regen(args):
    """Re-generation attack: SD 1.5 img2img at low strength (the copy is 're-drawn' by a model)."""
    import torch
    from diffusers import StableDiffusionImg2ImgPipeline

    from benchmark.attacks import jpeg

    man = json.loads(MANIFEST.read_text())
    todo = write_queries(man, lambda a: a == "regen")
    if not todo:
        return
    pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
        "runwayml/stable-diffusion-v1-5", torch_dtype=torch.float16, safety_checker=None, local_files_only=True
    ).to("cuda")
    pipe.enable_attention_slicing()
    pipe.set_progress_bar_config(disable=True)
    prompts = ("a digital artwork", "an nft artwork, digital art", "a painting", "an illustration")
    t = time.time()
    for n, q in enumerate(todo):
        rng = random.Random(q["id"])
        img = load_path(source_path(q["source"])).convert("RGB")
        w, h = img.size
        s = 512 / max(w, h)
        img = img.resize((max(64, int(w * s) // 8 * 8), max(64, int(h * s) // 8 * 8)), Image.BICUBIC)
        g = torch.Generator("cuda").manual_seed(rng.randint(0, 2**31))
        out = pipe(
            prompt=rng.choice(prompts),
            image=img,
            strength=rng.uniform(0.2, 0.4),
            guidance_scale=7.0,
            num_inference_steps=30,
            generator=g,
        ).images[0]
        jpeg(out, rng.randint(70, 95)).save(QUERY_DIR / f"{q['id']}.jpg", quality=95)
        if n % 25 == 0:
            print(f"[regen] {n}/{len(todo)} ({time.time() - t:.0f}s)", flush=True)
    print("[regen] done", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen", action="store_true")
    a = ap.parse_args()
    regen(a) if a.regen else build(a)
