r"""Regenerate the small test fixtures from the demo samples.

cd backend; ..\venv\Scripts\python.exe -m tests.fixtures.make_fixtures
"""

from pathlib import Path

from PIL import Image

from config import DEMO_DIR

OUT = Path(__file__).resolve().parent
SIZE = 160
SOURCES = {
    "painting.png": "01_genuine_painting.png",  # one-off art (protected reference)
    "photo.png": "02_genuine_photo.png",  # photo (textured: good copy-move target)
    "punk.png": "03_genuine_nft_collection_art.png",  # generative-collection member
}

for name, src in SOURCES.items():
    im = Image.open(DEMO_DIR / src).convert("RGB")
    im.thumbnail((SIZE, SIZE), Image.LANCZOS)
    im.save(OUT / name, optimize=True)
    print(name, im.size)
