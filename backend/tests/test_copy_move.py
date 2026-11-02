"""Copy-move detection: clean images vs synthetic clones (detection/manipulations.py)."""

import base64
import io
import random

import pytest
from PIL import Image

from detection import copy_move, manipulations
from detection.policy import Policy
from tests.conftest import fixture_image

THRESHOLDS = Policy().checks["copy_move"]


@pytest.mark.parametrize("name", ["photo.png", "painting.png", "punk.png"])
def test_clean_images_stay_below_review(name):
    assert copy_move.analyse(fixture_image(name))["score"] < THRESHOLDS["review"]


def test_flat_image_has_no_evidence():
    r = copy_move.analyse(Image.new("RGB", (200, 200), (90, 90, 90)))
    assert r == {"score": 0.0, "votes": 0, "shift": None, "boxes": [], "size": [256, 256]}


@pytest.mark.parametrize("seed", range(5))
def test_synthetic_clone_fails(seed):
    forged = manipulations.copy_move(fixture_image("photo.png"), random.Random(seed))
    r = copy_move.analyse(forged)
    assert r["score"] >= THRESHOLDS["fail"]
    assert r["votes"] > 0 and r["boxes"] and r["shift"] is not None


def test_detected_shift_matches_clone():
    img = Image.effect_noise((160, 160), 80).convert("RGB")
    forged = img.copy()
    forged.paste(img.crop((10, 10, 60, 60)), (90, 100))  # displacement (80, 90) at full size
    r = copy_move.analyse(forged)
    scale = r["size"][0] / 160
    assert r["score"] >= THRESHOLDS["fail"]
    assert r["shift"] == pytest.approx([80 * scale, 90 * scale], abs=1.5)


def test_heatmap_is_png():
    forged = manipulations.copy_move(fixture_image("photo.png"), random.Random(0))
    r = copy_move.analyse(forged)
    png = Image.open(io.BytesIO(base64.b64decode(copy_move.heatmap(forged, r))))
    assert png.format == "PNG" and png.size == (2 * r["size"][0], 2 * r["size"][1])
