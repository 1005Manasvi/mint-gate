"""Out-of-source protocol v2: data that contributed NOTHING to training, per domain.

v1 (`training/independent_check.py`) had 480 genuine NFTs (4 collections) + 200 Flux images and
saturated (CLIP probe AUC 0.9998), so it cannot rank new detectors. v2 keeps that exact sample
(group prefix `v1:`) and adds harder, more varied sources. Every source below is absent from the
training manifest (`data/realworld/manifest.json`); each group is reported separately.

Genuine (label "authentic"):
  nft_collection  huggingnft CyberKongz, CrypToadz, Shiboshis, EtherBears (v1) + Alpacadabraz,
                  Mini Mutants (MIT); on-chain tokens of the Phase 4 index from
                  collections never used in training (images via Alchemy's CDN)
  pixel_art       on-chain pixel-art collections (Moonbirds, Nouns, Lil Nouns, Pixelmon, ...)
  painting        Rijksmuseum paintings (thers2m/rijksmuseum_painting_dataset, test split; the
                  museum's images are CC0). Training paintings are WikiArt / Met only.
  photo           DOCCI photos (OpenFakeTiny test split) + COCO val2017 (CC BY / BY-SA / PD only).
                  Training photos are ImageNet only.
AI (label "ai"), generators that are in NO training split:
  OpenFakeTiny `core` test split (ComplexDataLab, CC BY-NC 4.0, evaluation only): z-image-turbo,
  Flux.2-klein, Illustrious (anime SDXL fine-tune), GPT-image-1.5, Midjourney 7, Recraft v2 / v3,
  Seedream 5, Ideogram 2, Aurora, Lumina, Nano Banana Pro, ... (video frames dropped).
  Flux.1-dev from LukasT9/Flux-1-Dev-Images-1k (Apache-2.0), the v1 sample.

Every image gets the training normalisation (`fetch_realworld.normalise`: shorter side 256, JPEG
88-96), so format and resolution carry no label information.

`selection` = a stratified 50 / 50 split per group: candidates are ranked on "select", the shipped
detector's headline numbers come from "report" (no number reported is one it was selected on).

Output: data/oos/images/*.jpg + data/oos/manifest.json
Run from backend/:   python -m training.oos_data [--only nft_hf,onchain,rijks,photo,openfake,flux]
"""

import argparse
import io
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import requests
from huggingface_hub import HfFileSystem, list_repo_files
from PIL import Image

from config import BACKEND_DIR
from training.fetch_realworld import download, normalise, parquet_rows

OUT = BACKEND_DIR / "data" / "oos"
IMG_DIR = OUT / "images"
MANIFEST = OUT / "manifest.json"

V1_COLLECTIONS = ["cyberkongz", "cryptoadz-by-gremplin", "theshiboshis", "etherbears"]
NEW_HF_COLLECTIONS = ["alpacadabraz", "mini-mutants"]  # HAPE PRIME: one 470 MB row group, too slow here
FLUX_REPO = "LukasT9/Flux-1-Dev-Images-1k"
OPENFAKE = "ComplexDataLab/OpenFakeTiny"
RIJKS = "thers2m/rijksmuseum_painting_dataset"
COCO = "phiyodr/coco2017"
COCO_OK_LICENCES = {4: "CC BY 2.0", 5: "CC BY-SA 2.0", 7: "no known copyright", 8: "US Government work"}
# Phase 4 index collections that ARE in the training data (or in the v1 HF groups): excluded.
ONCHAIN_EXCLUDE = {"BoredApeYachtClub", "MutantApeYachtClub", "Azuki", "Wrapped Cryptopunks", "CrypToadz"}
PIXEL_ART = {"Moonbirds", "Moonbirds Oddities", "Nouns", "Lil Nouns", "Pixelmon", "Chimpers", "Meebits"}
ONCHAIN_PER_COLLECTION = 9


class Out:
    def __init__(self):
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        self.records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
        self.seen = {r["file"] for r in self.records}

    def add(self, key, data_or_img, label, group, domain, source, licence, generator=None):
        name = f"{key}.jpg"
        if name in self.seen:
            return True
        try:
            img = data_or_img if isinstance(data_or_img, Image.Image) else Image.open(io.BytesIO(data_or_img))
            img.load()
            if min(img.size) < 32:
                return False
            data = normalise(img)
        except Exception as exc:  # broken image in a public dataset: skip, never crash the run
            print("skip", key, type(exc).__name__)
            return False
        (IMG_DIR / name).write_bytes(data)
        self.records.append(
            {
                "file": name,
                "label": label,
                "group": group,
                "domain": domain,
                "source": source,
                "licence": licence,
                "generator": generator or ("human" if label == "authentic" else group.split(":")[-1]),
            }
        )
        self.seen.add(name)
        return True

    def save(self):
        assign_selection(self.records)
        MANIFEST.write_text(json.dumps(self.records, indent=0))


def assign_selection(records):
    """Stratified 50/50 per group, deterministic (sorted by file name, seeded shuffle)."""
    by_group = {}
    for r in records:
        by_group.setdefault(r["group"], []).append(r)
    for g, rs in by_group.items():
        rs.sort(key=lambda r: r["file"])
        random.Random(f"sel-{g}").shuffle(rs)
        for i, r in enumerate(rs):
            r["selection"] = "select" if i % 2 == 0 else "report"


def fetch_bytes(fs, path, tries=8):
    for attempt in range(tries):
        try:
            with fs.open(path, "rb") as fh:
                return fh.read()
        except Exception:
            time.sleep(1 + attempt)
    return None


def v1_and_flux(out: Out, only):
    """The exact v1 sample (same rng sequence as independent_check.py) + new HF collections."""
    rng = random.Random(5)
    fs = HfFileSystem()
    for name in V1_COLLECTIONS:
        repo = f"huggingnft/{name}"
        pq_file = sorted(f for f in list_repo_files(repo, repo_type="dataset") if f.endswith(".parquet"))[0]
        rows = list(parquet_rows(download(repo, pq_file), ["image"]))
        picked = rng.sample(rows, min(120, len(rows)))
        if "nft_hf" in only:
            for i, row in enumerate(picked):
                out.add(
                    f"v1_{name}_{i}",
                    row["image"]["bytes"],
                    "authentic",
                    f"v1:nft_{name}",
                    "nft_collection",
                    repo,
                    "MIT",
                )
    flux_files = sorted(f for f in list_repo_files(FLUX_REPO, repo_type="dataset") if f.endswith(".png"))
    chosen = rng.sample(flux_files, min(200, len(flux_files)))
    if "flux" in only:
        todo = [(i, f) for i, f in enumerate(chosen) if f"v1_flux_{i}.jpg" not in out.seen]
        with ThreadPoolExecutor(12) as ex:
            for (i, _), data in zip(todo, ex.map(lambda x: fetch_bytes(fs, f"datasets/{FLUX_REPO}/{x[1]}"), todo)):
                if data:
                    out.add(
                        f"v1_flux_{i}", data, "ai", "v1:flux1-dev", "ai_photo_art", FLUX_REPO, "Apache-2.0", "flux1-dev"
                    )
    out.save()
    if "nft_hf" not in only:
        return
    for name in NEW_HF_COLLECTIONS:
        repo = f"huggingnft/{name}"
        pq_file = sorted(f for f in list_repo_files(repo, repo_type="dataset") if f.endswith(".parquet"))[0]
        # one row group over HTTP range reads (HAPE PRIME is 1.9 GB in total)
        pf = pq.ParquetFile(fs.open(f"datasets/{repo}/{pq_file}", "rb", block_size=1 << 22))
        rows = pf.read_row_group(0, columns=["image"]).to_pylist()
        for i, row in enumerate(random.Random(name).sample(rows, min(60, len(rows)))):
            out.add(
                f"hf_{name}_{i}", row["image"]["bytes"], "authentic", f"nft_hf:{name}", "nft_collection", repo, "MIT"
            )
        print(name, "done", flush=True)
        out.save()


def onchain(out: Out):
    """Tokens of the Phase 4 index from collections never used in training, full-size via Alchemy."""
    from sqlalchemy import text

    import db
    from ingest.alchemy import Alchemy

    with db.session() as s:
        rows = s.execute(
            text(
                "SELECT c.name, r.contract, r.token_id FROM reference_items r JOIN nft_collections c "
                "ON c.id = r.collection_id WHERE r.source = 'alchemy' AND r.status = 'ready' "
                "AND r.chain_id = 1 ORDER BY c.name, r.token_id"
            )
        ).all()
    by_coll = {}
    for name, contract, tid in rows:
        by_coll.setdefault((name, contract), []).append(str(int(tid)))
    al = Alchemy("eth-mainnet")
    http = requests.Session()
    for (name, contract), tids in sorted(by_coll.items()):
        if name in ONCHAIN_EXCLUDE:
            continue
        key0 = "".join(ch if ch.isalnum() else "_" for ch in name.lower())
        domain = "pixel_art" if name in PIXEL_ART else "nft_collection"
        got, rng = 0, random.Random(f"onchain-{contract}")
        for tid in rng.sample(tids, min(len(tids), ONCHAIN_PER_COLLECTION * 3)):
            if got >= ONCHAIN_PER_COLLECTION:
                break
            key = f"onchain_{key0}_{contract[-6:].lower()}_{tid}"
            if f"{key}.jpg" in out.seen:
                got += 1
                continue
            try:
                img = al.nft_metadata(contract, tid).get("image") or {}
                url = img.get("pngUrl") or img.get("cachedUrl")
                if not url:
                    continue
                r = http.get(url, timeout=60)
                if r.status_code != 200 or len(r.content) > 25_000_000:
                    continue
                if out.add(
                    key,
                    r.content,
                    "authentic",
                    f"onchain:{name}",
                    domain,
                    f"Ethereum {contract}",
                    "token owner's terms (evaluation only)",
                ):
                    got += 1
            except Exception as exc:
                print("onchain skip", name, tid, type(exc).__name__)
        print(f"{name}: {got}", flush=True)
        out.save()


def rijks(out: Out, n=300):
    local = download(RIJKS, "data/test-00000-of-00001.parquet")
    rows = [r for r in parquet_rows(local, ["id", "type", "image"])]
    for r in random.Random(11).sample(rows, min(n, len(rows))):
        out.add(
            f"rijks_{r['id']}".replace("/", "_"),
            r["image"]["bytes"],
            "authentic",
            "painting:rijksmuseum",
            "painting",
            RIJKS,
            "CC0 (Rijksmuseum open data)",
        )
    out.save()


def coco(out: Out, n=200):
    from huggingface_hub import hf_hub_download

    p = hf_hub_download(COCO, "data/validation-00000-of-00001-e3c37e369512a3aa.parquet", repo_type="dataset")
    rows = [
        r
        for r in pq.read_table(p, columns=["license", "coco_url", "image_id"]).to_pylist()
        if r["license"] in COCO_OK_LICENCES
    ]
    http = requests.Session()

    def get(r):
        for attempt in range(5):
            try:
                resp = http.get(r["coco_url"], timeout=60)
                if resp.status_code == 200:
                    return r, resp.content
            except requests.RequestException:
                time.sleep(1 + attempt)
        return r, None

    picked = random.Random(12).sample(rows, min(n, len(rows)))
    with ThreadPoolExecutor(8) as ex:
        for r, data in ex.map(get, picked):
            if data:
                out.add(
                    f"coco_{r['image_id']}",
                    data,
                    "authentic",
                    "photo:coco",
                    "photo",
                    "COCO val2017",
                    COCO_OK_LICENCES[r["license"]],
                )
    out.save()


def openfake(out: Out):
    """OpenFakeTiny core test: every still-image fake + the DOCCI photos (ImageNet reals are in-source)."""
    local = download(OPENFAKE, "core/test-00000-of-00001.parquet")
    pf = pq.ParquetFile(local)
    for rg in range(pf.num_row_groups):
        t = pf.read_row_group(rg, columns=["image", "label", "model", "type"]).to_pylist()
        for i, r in enumerate(t):
            key = f"openfake_{rg}_{i}"
            if r["label"] == "real":
                if r["model"] == "docci":
                    out.add(
                        key,
                        r["image"]["bytes"],
                        "authentic",
                        "photo:docci",
                        "photo",
                        OPENFAKE + " (DOCCI)",
                        "CC BY 4.0 (DOCCI)",
                    )
            elif r["type"] != "video":
                gen = r["model"]
                out.add(
                    key, r["image"]["bytes"], "ai", f"openfake:{gen}", "ai_photo_art", OPENFAKE, "CC BY-NC 4.0", gen
                )
        print("openfake row group", rg, flush=True)
        out.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="nft_hf,flux,rijks,photo,openfake,onchain")
    args = ap.parse_args()
    only = set(args.only.split(","))
    out = Out()
    if only & {"nft_hf", "flux"}:
        v1_and_flux(out, only)
    if "rijks" in only:
        rijks(out)
    if "photo" in only:
        coco(out)
    if "openfake" in only:
        openfake(out)
    if "onchain" in only:
        onchain(out)
    out.save()
    from collections import Counter

    print(Counter((r["label"], r["domain"]) for r in out.records))
    print(Counter(r["group"] for r in out.records))


if __name__ == "__main__":
    main()
