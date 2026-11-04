"""Download the copy-detection model weights (resumable; the network here is slow).

  SSCD sscd_disc_mixup (TorchScript, MIT)       -> model/copydet/
  DINOv2 ViT-S/14 + ViT-B/14 (Apache-2.0)       -> Hugging Face cache
  ALIKED-n16 (BSD-3) + LightGlue-ALIKED (Apache) -> torch hub cache (what kornia loads)

Run from backend/:   python -m benchmark.fetch_models [--only sscd,dinov2,aliked]
"""

import argparse
import time
from pathlib import Path

import requests

from config import MODEL_DIR

COPYDET_DIR = MODEL_DIR / "copydet"
SSCD_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"
SSCD_FILE = COPYDET_DIR / "sscd_disc_mixup.torchscript.pt"
DINOV2 = ("facebook/dinov2-small", "facebook/dinov2-base")


def download(url: str, dest: Path, chunk=1 << 20, retries=20):
    """HTTP download with Range resume into `<dest>.part`; renamed only when complete."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"[fetch] {dest.name}: present ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        try:
            with requests.get(url, headers={"Range": f"bytes={have}-"}, stream=True, timeout=60) as r:
                if r.status_code == 416:  # already complete
                    break
                r.raise_for_status()
                if have and r.status_code != 206:  # server ignored Range: start over
                    have = 0
                total = have + int(r.headers.get("Content-Length", 0))
                with open(part, "ab" if have else "wb") as f:
                    for i, block in enumerate(r.iter_content(chunk)):
                        f.write(block)
                        if i % 10 == 0:
                            print(f"[fetch] {dest.name}: {f.tell() / 1e6:.1f} / {total / 1e6:.1f} MB", flush=True)
            break
        except (requests.RequestException, OSError) as e:
            print(f"[fetch] {dest.name}: attempt {attempt + 1} failed ({e}); retrying", flush=True)
            time.sleep(min(60, 5 * (attempt + 1)))
    else:
        raise RuntimeError(f"download failed: {url}")
    part.replace(dest)
    print(f"[fetch] {dest.name}: done ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="sscd,dinov2,aliked")
    only = set(ap.parse_args().only.split(","))
    if "sscd" in only:
        download(SSCD_URL, SSCD_FILE)
    if "aliked" in only:
        import kornia.feature as KF

        for attempt in range(20):  # kornia caches both files in the torch hub directory
            try:
                KF.ALIKED.from_pretrained("aliked-n16")
                KF.LightGlue("aliked")
                print("[fetch] ALIKED + LightGlue: cached")
                break
            except Exception as e:  # noqa: BLE001 - flaky network, retry anything
                print(f"[fetch] ALIKED / LightGlue: attempt {attempt + 1} failed ({e})", flush=True)
                time.sleep(10)
    if "dinov2" in only:
        from huggingface_hub import snapshot_download

        for repo in DINOV2:
            for attempt in range(20):  # snapshot_download resumes partial files itself
                try:
                    p = snapshot_download(repo, allow_patterns=["*.json", "*.safetensors"])
                    print(f"[fetch] {repo}: {p}")
                    break
                except Exception as e:  # noqa: BLE001 - flaky network, retry anything
                    print(f"[fetch] {repo}: attempt {attempt + 1} failed ({e})", flush=True)
                    time.sleep(10)


if __name__ == "__main__":
    main()
