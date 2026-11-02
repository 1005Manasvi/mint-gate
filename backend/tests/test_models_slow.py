"""Smoke tests for the real models (CLIP download, ResNet weights). CI skips these:
pytest -m slow
"""

import numpy as np
import pytest

from config import DEMO_DIR
from detection.preprocessing import detector_view, load_path

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def clip():
    from detection.clip_similarity import ClipEncoder

    return ClipEncoder()


def view(name):
    return detector_view(load_path(DEMO_DIR / name))


def test_clip_embeddings_are_normalised(clip):
    a, b = clip.encode([view("01_genuine_painting.png"), view("02_genuine_photo.png")])
    assert a.shape == (512,) and np.linalg.norm(a) == pytest.approx(1.0, abs=1e-4)
    assert float(a @ clip.encode_one(view("01_genuine_painting.png"))) > 0.999
    assert float(a @ b) < 0.9


def test_ai_detector_separates_demo_samples(clip):
    from detection.ai_detector import AIDetector

    det = AIDetector()
    assert det.ready and det.probe is not None

    def p(name):
        v = view(name)
        return det.predict(v, clip.encode_one(v))["probability"]

    genuine = [p("01_genuine_painting.png"), p("02_genuine_photo.png"), p("03_genuine_nft_collection_art.png")]
    ai = [p("04_ai_generated_art.png"), p("05_flux_unseen_generator.png")]
    assert max(genuine) < min(ai)


def test_resnet_outputs_probability():
    from detection.resnet_classifier import ResNetClassifier

    m = ResNetClassifier()
    assert m.classes.index("forged") == m.forged_idx
    assert 0.0 <= m.predict(view("02_genuine_photo.png")) <= 1.0
