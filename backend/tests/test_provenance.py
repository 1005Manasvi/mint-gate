"""Provenance signals (detection/provenance.py) on committed fixtures
(tests/fixtures/provenance/make_fixtures.py builds them: real C2PA signatures from a throw-away
test CA, the real SD / SDXL invisible-watermark encoders, real metadata layouts)."""

import io

import pytest
from PIL import Image

from detection import provenance as pv
from detection.preprocessing import prepare
from tests.fakes import FIXTURES

D = FIXTURES / "provenance"


def run(name):
    raw = (D / name).read_bytes()
    return pv.analyse(raw, prepare(raw).rgb)


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setenv("C2PA_TRUST_ANCHORS", str(D / "test_ca.pem"))
    monkeypatch.setattr(pv, "_trust_settings", None)
    yield
    pv._trust_settings = None


@pytest.fixture(autouse=True)
def no_reverse_search(monkeypatch):
    monkeypatch.delenv("REVERSE_SEARCH", raising=False)
    monkeypatch.delenv("C2PA_TRUST_ANCHORS", raising=False)
    monkeypatch.setattr(pv, "_trust_settings", None)


@pytest.mark.parametrize("name", ["plain.png", "photo_plain.png", "description.png", "sdxl_watermark_jpeg.jpg"])
def test_no_evidence(name):
    r = run(name)
    assert r["value"] == pv.NOTHING, r["reason"]
    assert r["details"]["c2pa"] is None and r["details"]["watermarks"] == []


@pytest.mark.parametrize(
    "name, needle",
    [
        ("sd_watermark.png", "stable-diffusion-v1"),
        ("sdxl_watermark.png", "stable-diffusion-xl"),
        ("a1111.png", "Automatic1111"),
        ("comfyui.png", "ComfyUI"),
        ("xmp_ai.jpg", "trainedAlgorithmicMedia"),
        ("c2pa_ai.jpg", "C2PA credentials declare AI generation"),
    ],
)
def test_ai_evidence(name, needle):
    r = run(name)
    assert r["value"] == pv.AI_GENERATED
    assert needle in r["reason"]


def test_watermark_needs_few_bit_errors():
    (hit,) = run("sd_watermark.png")["details"]["watermarks"]
    assert hit["bits"] == 136 and hit["bit_errors"] <= 12


def test_c2pa_untrusted_capture_earns_no_credit():
    """A self-signed 'camera' manifest is easy to forge: valid, but no positive evidence."""
    r = run("c2pa_capture.jpg")
    c = r["details"]["c2pa"]
    assert c["valid"] and not c["trusted"] and c["source_types"] == ["digitalCapture"]
    assert r["value"] == pv.NOTHING


def test_c2pa_trusted_capture_is_positive_evidence(trusted):
    r = run("c2pa_capture.jpg")
    assert r["details"]["c2pa"]["trusted"]
    assert r["value"] == pv.TRUSTED_CAPTURE


def test_c2pa_ai_claim_counts_trusted_or_not(trusted):
    assert run("c2pa_ai.jpg")["value"] == pv.AI_GENERATED


def test_c2pa_tampered_pixels_invalidate_the_manifest(trusted):
    r = run("c2pa_tampered.jpg")
    c = r["details"]["c2pa"]
    assert c["present"] and not c["valid"] and "assertion.dataHash.mismatch" in c["failures"]
    assert r["value"] == pv.NOTHING


def test_algorithmic_media_is_not_ai():
    """Generative code art (Art Blocks) is `algorithmicMedia`, not trained AI."""
    c = {"valid": True, "trusted": False, "source_types": ["algorithmicMedia"], "claim_generator": "", "signer": ""}
    assert pv.c2pa_verdict(c) is None
    assert pv.c2pa_verdict({**c, "source_types": ["compositeWithTrainedAlgorithmicMedia"]})[0] == pv.AI_EDITED


def test_ai_evidence_overrides_trusted_capture():
    v = [(pv.TRUSTED_CAPTURE, "camera"), (pv.AI_GENERATED, "watermark")]
    assert max((x for x in v if x[0] > 0), key=lambda x: x[0])[0] == pv.AI_GENERATED


def test_watermark_skipped_where_no_native_generator_output_is_possible():
    assert pv.read_watermarks(Image.new("RGB", (200, 904))) == []  # too small to carry it
    assert pv.read_watermarks(Image.new("RGB", (2048, 2048))) == []  # larger than any native output
    assert pv.read_watermarks(Image.new("RGB", (513, 512))) == []  # not a multiple of 8: resized


def test_watermark_still_found_after_cropping_to_a_native_size():
    raw = (D / "sd_watermark.png").read_bytes()
    assert prepare(raw).rgb.size[0] % 8 == 0 and run("sd_watermark.png")["details"]["watermarks"]


def test_reverse_search_is_advisory(monkeypatch):
    """A web match is REVIEW-level evidence (the artist may have posted it first), never FAIL."""

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "responses": [
                    {
                        "webDetection": {
                            "fullMatchingImages": [{"url": "https://example.org/a.png"}],
                            "pagesWithMatchingImages": [
                                {
                                    "url": "https://example.org/post",
                                    "pageTitle": "t",
                                    "fullMatchingImages": [{"url": "https://example.org/a.png"}],
                                }
                            ],
                        }
                    }
                ]
            }

    sent = {}

    def fake_post(url, params=None, json=None, timeout=None, **kw):
        sent["url"], sent["key"] = url, (params or {}).get("key")
        return Resp()

    monkeypatch.setenv("REVERSE_SEARCH", "vision")
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "test-key")
    monkeypatch.setattr("requests.post", fake_post)
    r = run("plain.png")
    assert sent["url"].startswith("https://vision.googleapis.com/") and sent["key"] == "test-key"
    assert r["value"] == pv.AI_EDITED and "already published" in r["reason"]
    assert r["details"]["reverse_search"]["pages"][0]["url"] == "https://example.org/post"


def test_reverse_search_errors_are_not_evidence(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setenv("REVERSE_SEARCH", "vision")
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "k")
    monkeypatch.setattr("requests.post", boom)
    r = run("plain.png")
    assert r["value"] == pv.NOTHING and r["details"]["reverse_search"]["error"] == "TimeoutError"


def test_reverse_search_off_by_default():
    assert pv.reverse_search(b"x") is None


def test_metadata_reader_survives_garbage():
    assert pv.read_metadata(b"not an image")["generators"] == []
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="GIF")
    assert pv.read_metadata(buf.getvalue())["generators"] == []


def test_expired_signer_keeps_an_intact_ai_claim_but_earns_no_trust():
    """OpenAI's GPT-image manifests: content intact, signing certificate expired."""
    intact, trusted, failures = pv.classify_status(["signingCredential.expired"], "invalid")
    assert intact and not trusted and failures == []
    assert pv.classify_status(["signingCredential.expired", "assertion.dataHash.mismatch"], "invalid")[0] is False
    assert pv.classify_status([], "trusted") == (True, True, [])
    assert pv.classify_status(["signingCredential.untrusted"], "valid") == (True, False, [])
