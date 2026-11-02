"""Dataset v2: NFT-relevant authentic art + modern AI generators.

Adds to the v1 real-world dataset (training/fetch_realworld.py):
  authentic  real NFT collections minted before modern image generators existed
             (BAYC, MAYC, Azuki, Trippy Toadz, CryptoSkulls, nftrex, Dooggies - MIT,
             huggingnft) and WikiArt paintings (huggan/wikiart)
  ai         DALL-E 3, Midjourney, Stable Diffusion 3 (Rapidata, CDLA-Permissive-2.0)
             and SDXL (ash12321/sdxl-generated-10k, MIT)
  unseen     Flux (Rapidata) - never used for training; measures generalisation
and rebalances v1 towards what is actually minted: fewer stock photos, fewer
CryptoPunks, no BigGAN, fewer images from 2022-era generators.

Every image goes through the same normalisation as v1 (short side 256 px + JPEG),
so file format and resolution carry no label information.

Run from backend/:   python -m training.fetch_v2
"""

import random
from collections import Counter, defaultdict

import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem
from tqdm import tqdm

from training.fetch_realworld import IMG_DIR, Writer, download, parquet_rows

rng = random.Random(77)
HELD_OUT = "flux"

NFT_COLLECTIONS = {  # repo -> (parquet file, images to keep)
    "huggingnft/boredapeyachtclub": 800,
    "huggingnft/mutant-ape-yacht-club": 600,
    "huggingnft/azuki": 800,
    "huggingnft/trippytoadznft": 500,
    "huggingnft/cryptoskulls": 400,
    "huggingnft/nftrex": 300,
    "huggingnft/dooggies": 300,
}
RAPIDATA = "Rapidata/700k_Human_Preference_Dataset_FLUX_SD3_MJ_DALLE3"
RAPIDATA_FILES = ["train_0006", "train_0011", "train_0016", "train_0024"]  # dalle3+mj, dalle3+sd3, mj+flux, sd3+flux
RAPIDATA_KEEP = {"dalle-3": 1500, "mj": 1500, "stable_diffusion": 1500, "flux": 1000}
RAPIDATA_NAME = {"dalle-3": "dalle3", "mj": "midjourney", "stable_diffusion": "sd3", "flux": "flux"}

# v1 rebalancing caps: generator -> max images kept (None = drop entirely)
CAPS = {
    "real_photo": 1200,
    "cryptopunk": 1000,
    "local_ai_art": 1500,
    "BigGAN": None,
    "ADM": 300,
    "GLIDE": 300,
    "VQDM": 300,
    "Wukong": 300,
    "SD15": 300,
    "Midjourney": 300,
}


def first_parquet(fs, repo):
    return sorted(f for f in fs.glob(f"datasets/{repo}/**/*.parquet"))[0].split(f"datasets/{repo}/", 1)[1]


def fetch_nfts(w, fs):
    for repo, keep in NFT_COLLECTIONS.items():
        name = repo.split("/")[1]
        if sum(1 for r in w.records if r["generator"] == f"nft_{name}") >= keep:
            continue
        rows = list(parquet_rows(download(repo, first_parquet(fs, repo)), ["image", "id"]))
        for row in tqdm(rng.sample(rows, min(keep, len(rows))), desc=name):
            w.add(f"nft_{name}_{row['id']}", row["image"]["bytes"], "authentic", "nft_collections", f"nft_{name}")
        w.save()


def fetch_wikiart(w):
    for i, row in enumerate(
        tqdm(
            parquet_rows(download("huggan/wikiart", "data/train-00010-of-00072.parquet"), ["image", "style"]),
            desc="wikiart",
        )
    ):
        w.add(f"wikiart_10_{i}", row["image"]["bytes"], "authentic", "wikiart", "human_art")
    w.save()


def fetch_rapidata(w, fs):
    files = [f for f in fs.glob(f"datasets/{RAPIDATA}/**/*.parquet") if any(k in f for k in RAPIDATA_FILES)]
    got = Counter(r["generator"] for r in w.records if r["source"] == "rapidata")
    for f in sorted(files):
        rel = f.split(f"datasets/{RAPIDATA}/", 1)[1]
        for i, row in enumerate(
            tqdm(parquet_rows(download(RAPIDATA, rel), ["image1", "image2", "model1", "model2"]), desc=rel[-30:])
        ):
            for k in ("1", "2"):
                gen = RAPIDATA_NAME[row[f"model{k}"]]
                if got[gen] >= RAPIDATA_KEEP[row[f"model{k}"]]:
                    continue
                before = len(w.records)
                w.add(f"rapidata_{rel.split('/')[-1][:10]}_{i}_{k}", row[f"image{k}"]["bytes"], "ai", "rapidata", gen)
                got[gen] += len(w.records) - before
        w.save()


def fetch_sdxl(w, fs, row_groups=2):
    repo = "ash12321/sdxl-generated-10k"
    path = f"datasets/{repo}/{first_parquet(fs, repo)}"
    pf = pq.ParquetFile(fs.open(path, "rb", block_size=8 << 20))  # HTTP range reads: only these row groups
    for rg in range(min(row_groups, pf.num_row_groups)):
        for i, row in enumerate(tqdm(pf.read_row_group(rg, columns=["image"]).to_pylist(), desc=f"sdxl rg{rg}")):
            w.add(f"sdxl_{rg}_{i}", row["image"]["bytes"], "ai", "sdxl", "sdxl")
    w.save()


def rebalance(w):
    """Apply CAPS to v1 data; removed images are deleted from disk."""
    by_gen = defaultdict(list)
    for r in w.records:
        by_gen[r["generator"]].append(r)
    drop = set()
    for gen, cap in CAPS.items():
        recs = sorted(by_gen.get(gen, []), key=lambda r: r["file"])
        keep = [] if cap is None else random.Random(gen).sample(recs, min(cap, len(recs)))
        drop |= {r["file"] for r in recs} - {r["file"] for r in keep}
    for f in drop:
        (IMG_DIR / f).unlink(missing_ok=True)
    w.records = [r for r in w.records if r["file"] not in drop]
    w.seen -= drop
    w.save()
    print(f"rebalance: removed {len(drop)} images")


def assign_splits(w):
    split_rng = random.Random(42)
    for r in sorted(w.records, key=lambda r: r["file"]):
        if r["generator"] == HELD_OUT:
            r["split"] = "test_unseen"
        else:
            x = split_rng.random()
            r["split"] = "test" if x < 0.1 else "val" if x < 0.2 else "train"
    w.save()


def main():
    w = Writer()
    fs = HfFileSystem()
    rebalance(w)
    fetch_nfts(w, fs)
    fetch_wikiart(w)
    fetch_rapidata(w, fs)
    fetch_sdxl(w, fs)
    assign_splits(w)
    print(Counter((r["split"], r["label"]) for r in w.records))
    print(Counter(r["generator"] for r in w.records))


if __name__ == "__main__":
    main()
