"""Provenance evidence (Phase 5): cheap, high-precision signals read from the uploaded *file*.

The learned detectors look at pixels; these checks look at what the file says about its origin.
They run on the raw upload bytes (`preprocessing.prepare` strips metadata for every other check).

1. C2PA Content Credentials (`c2pa-python`, MIT / Apache-2.0): the manifest store is read and its
   signature chain validated. Only a manifest whose validation state is Valid / Trusted (no
   failure codes) counts. IPTC digital-source types decide what it says:
     trainedAlgorithmicMedia                  AI-generated        -> "ai_generated"  (FAIL)
     compositeWithTrainedAlgorithmicMedia,
     compositeSynthetic                       AI-edited           -> "ai_edited"     (REVIEW)
     digitalCapture, digitalArt, humanEdits,  capture / human     -> "trusted_capture", only when the
     ...                                                             signer chains to a configured
                                                                     trust anchor (C2PA_TRUST_ANCHORS)
   A self-signed "captured by a camera" manifest is easy to forge, so an untrusted chain never earns
   positive evidence; an AI claim is believed from any intact manifest (nobody forges evidence
   *against* their own upload).
   `algorithmicMedia` (pure algorithmic / generative code art, e.g. Art Blocks) is NOT AI.
2. Invisible watermarks (`invisible-watermark`, MIT): the DWT-DCT marks that the Stable Diffusion
   reference scripts ("StableDiffusionV1", 136 bits) and diffusers' SDXL pipelines (48 fixed bits)
   embed. A match needs <= 4 / 48 resp. <= 12 / 136 bit errors (false-match probability on a random
   image < 1e-9 resp. < 1e-20).
3. Generator metadata: PNG text chunks (A1111 `parameters`, ComfyUI `prompt` / `workflow`, NovelAI,
   InvokeAI), EXIF / XMP software tags and the IPTC `DigitalSourceType` in XMP.
4. Reverse image search (optional, `REVERSE_SEARCH=vision|tineye` + key): pages that already show
   the image. Advisory only (the artist may have posted it first): REVIEW, never FAIL.

The policy check `provenance` gets one value:
   1.0  AI-generation evidence (C2PA claim, watermark, generator metadata)          -> FAIL
   0.6  AI-edit claim, or the image is already published on the web               -> REVIEW
   0.0  nothing found
  -1.0  trusted C2PA capture / human-edit chain without AI ingredients            -> positive: the
       policy's `positive_evidence` rule caps the AI detector at REVIEW (a probe score alone cannot
       auto-reject an image whose trusted credentials say a camera / a human made it)
"""

import io
import json
import re
import time

import numpy as np
from PIL import Image

from config import env

VERSION = "provenance-1.0.0"
AI_GENERATED, AI_EDITED, NOTHING, TRUSTED_CAPTURE = 1.0, 0.6, 0.0, -1.0

IPTC = "digitalsourcetype/"
AI_TYPES = {"trainedAlgorithmicMedia"}
AI_EDIT_TYPES = {"compositeWithTrainedAlgorithmicMedia", "compositeSynthetic"}
HUMAN_TYPES = {
    "digitalCapture",
    "computationalCapture",
    "negativeFilm",
    "positiveFilm",
    "print",
    "minorHumanEdits",
    "humanEdits",
    "compositeCapture",
    "digitalArt",
    "virtualRecording",
    "screenCapture",
}

# ---- 1. C2PA ---------------------------------------------------------------------------------------

_trust_settings = None
# Status codes about the signer's CREDENTIAL (who signed, is the certificate still valid), not about
# whether the content matches the manifest. OpenAI's 2025 certificates have expired, so c2pa reports
# their manifests as "invalid" although every hash matches: the AI claim is still intact evidence.
# A credential problem never earns *trusted* positive evidence.
CREDENTIAL_CODES = {
    "signingCredential.untrusted",
    "signingCredential.expired",
    "signingCredential.ocsp.unknown",
    "signingCredential.ocsp.skipped",
    "timeStamp.untrusted",
    "timeStamp.outsideValidity",
}


def classify_status(codes, state):
    """(intact, trusted, integrity_failures) from c2pa validation codes + state."""
    integrity = [c for c in codes if c not in CREDENTIAL_CODES]
    intact = not integrity and state in ("valid", "trusted", "invalid")
    trusted = intact and state == "trusted" and not codes
    return intact, trusted, integrity


def _context():
    """c2pa Context with trust verification when C2PA_TRUST_ANCHORS (PEM file) is configured."""
    global _trust_settings
    import c2pa

    path = env("C2PA_TRUST_ANCHORS", "")
    if not path:
        return None
    if _trust_settings is None:
        from pathlib import Path

        pem = Path(path).read_text()
        _trust_settings = c2pa.Settings.from_dict({"verify": {"verify_trust": True}, "trust": {"trust_anchors": pem}})
    return c2pa.Context(_trust_settings)


def _source_types(manifest: dict) -> set[str]:
    out = set()
    for a in manifest.get("assertions", []) or []:
        if not str(a.get("label", "")).startswith("c2pa.actions"):
            continue
        for act in (a.get("data") or {}).get("actions", []) or []:
            t = act.get("digitalSourceType") or ""
            if t:
                out.add(t.rsplit(IPTC, 1)[-1].rsplit("/", 1)[-1])
    return out


def read_c2pa(raw: bytes) -> dict | None:
    """Summary of the C2PA manifest store, or None when the file carries none."""
    import c2pa

    try:
        ctx = _context()
        reader = c2pa.Reader(None, io.BytesIO(raw), context=ctx) if ctx else c2pa.Reader(None, io.BytesIO(raw))
    except Exception as exc:  # no manifest (the common case) or an unsupported container
        msg = str(exc)
        if "ManifestNotFound" in type(exc).__name__ or "not found" in msg.lower() or "JumbfNotFound" in msg:
            return None
        return {"present": True, "valid": False, "error": type(exc).__name__}
    try:
        store = json.loads(reader.json())
        state = (reader.get_validation_state() or store.get("validation_state") or "").lower()
    finally:
        reader.close()
    codes = [s.get("code") for s in store.get("validation_status") or [] if s.get("code")]
    intact, trusted, failures = classify_status(codes, state)
    active = store.get("manifests", {}).get(store.get("active_manifest") or "", {})
    types = set()
    for m in store.get("manifests", {}).values():  # the active manifest and every ingredient's
        types |= _source_types(m)
    sig = active.get("signature_info") or {}
    gen = active.get("claim_generator") or ", ".join(
        str(g.get("name", "")) for g in active.get("claim_generator_info", []) or [] if isinstance(g, dict)
    )
    return {
        "present": True,
        "valid": intact,  # content matches the signed manifest (credential issues aside)
        "trusted": trusted,
        "credential_issues": [c for c in codes if c in CREDENTIAL_CODES][:5],
        "state": state,
        "failures": failures[:10],
        "source_types": sorted(types),
        "claim_generator": gen[:200],
        "signer": str(sig.get("issuer") or sig.get("common_name") or "")[:200],
        "signed_at": sig.get("time"),
    }


def c2pa_verdict(c: dict | None) -> tuple[float, str] | None:
    if not c or not c.get("valid"):
        return None
    types = set(c["source_types"])
    if types & AI_TYPES:
        return AI_GENERATED, f"C2PA credentials declare AI generation ({c['claim_generator'] or c['signer']})"
    if types & AI_EDIT_TYPES:
        return AI_EDITED, "C2PA credentials declare AI-assisted editing"
    if c.get("trusted") and types and types <= HUMAN_TYPES:
        return TRUSTED_CAPTURE, f"trusted C2PA capture / edit chain ({c['signer']})"
    return None


# ---- 2. invisible watermarks -----------------------------------------------------------------------

SD1_MESSAGE = b"StableDiffusionV1"
SDXL_BITS = [int(b) for b in bin(0b101100111110110010010000011110111011000110011110)[2:]]  # diffusers
# name: (bits, max bit errors, max pixels). The marks are embedded at the generator's native output
# size (both sides divisible by 8: SD 1.x / 2.x up to ~768 x 1024, SDXL up to ~1.6 MP) and do not
# survive a resize, so a decoder only runs where a native output is possible. Decoding costs
# ~350 ms per megapixel on 2 CPU threads (docs/models.md), the size gate keeps it inside the budget.
WATERMARKS = {
    "stable-diffusion-v1/v2 (DWT-DCT 'StableDiffusionV1')": (
        np.unpackbits(np.frombuffer(SD1_MESSAGE, np.uint8)).tolist(),
        12,
        800_000,
    ),
    "stable-diffusion-xl (diffusers DWT-DCT)": (SDXL_BITS, 4, 1_600_000),
}
WATERMARK_MAX_PIXELS = max(m for _, _, m in WATERMARKS.values())


def read_watermarks(rgb: Image.Image) -> list[dict]:
    from imwatermark import WatermarkDecoder

    if min(rgb.size) < 256:  # the DWT-DCT embedder needs >= 256 px; smaller images cannot carry it
        return []
    px = rgb.width * rgb.height
    if px > WATERMARK_MAX_PIXELS or rgb.width % 8 or rgb.height % 8:  # not a native generator output
        return []
    bgr = np.asarray(rgb.convert("RGB"))[:, :, ::-1].copy()
    hits = []
    decoded = {}
    for name, (bits, max_err, max_px) in WATERMARKS.items():
        if px > max_px:
            continue
        n = len(bits)
        if n not in decoded:
            try:
                decoded[n] = np.asarray(WatermarkDecoder("bits", n).decode(bgr, "dwtDct"), int)
            except Exception:
                decoded[n] = None
        got = decoded[n]
        if got is None or len(got) != n:
            continue
        errors = int((got != np.asarray(bits)).sum())
        if errors <= max_err:
            hits.append({"watermark": name, "bit_errors": errors, "bits": n})
    return hits


# ---- 3. generator metadata -------------------------------------------------------------------------

# Structured traces only a generator writes: count in any text field.
_STRUCTURED = [
    (re.compile(r"Steps: \d+.*Sampler: ", re.S), "Automatic1111 / Forge generation parameters"),
    (re.compile(r'"class_type"\s*:'), "ComfyUI workflow"),
]
# Product names: only in software / tool fields (a human's description may mention them).
_TOOL_FIELDS = {"software", "exif_software", "source", "generation_data", "invokeai_metadata", "sd-metadata", "dream"}
_GEN_PATTERNS = [
    (re.compile(r"\bNovelAI\b", re.I), "NovelAI"),
    (re.compile(r"\binvokeai\b", re.I), "InvokeAI"),
    (re.compile(r"\bmidjourney\b", re.I), "Midjourney"),
    (re.compile(r"\bDALL[·\-. ]?E\b", re.I), "DALL-E"),
    (re.compile(r"\bstable[ -]?diffusion\b", re.I), "Stable Diffusion"),
    (re.compile(r"\bAdobe Firefly\b", re.I), "Adobe Firefly"),
    (re.compile(r"\b(?:Imagen|Gemini)\b.*\bGoogle\b|\bSynthID\b", re.I), "Google generative AI"),
    (re.compile(r"\bLeonardo\.?Ai\b", re.I), "Leonardo.Ai"),
]
_XMP_TYPE = re.compile(
    rb"DigitalSourceType[^>]{0,40}?(?:>|=\")\s*(?:https?://cv\.iptc\.org/newscodes/digitalsourcetype/)?(\w+)"
)
_TEXT_KEYS = (
    "parameters",
    "prompt",
    "workflow",
    "software",
    "description",
    "comment",
    "source",
    "generation_data",
    "invokeai_metadata",
    "sd-metadata",
    "dream",
    "title",
    "author",
)


def read_metadata(raw: bytes) -> dict:
    """Generator traces in PNG text chunks, EXIF and XMP. Values are truncated (they go into the report)."""
    found, fields = [], {}
    try:
        with Image.open(io.BytesIO(raw)) as im:
            info = {str(k).lower(): v for k, v in (im.info or {}).items() if isinstance(v, (str, bytes))}
            exif = im.getexif()
            for tag, name in ((305, "software"), (270, "image_description"), (315, "artist"), (37510, "user_comment")):
                if exif.get(tag):
                    info[f"exif_{name}"] = exif.get(tag)
    except Exception:
        info = {}
    for k, v in info.items():
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        if k in _TEXT_KEYS or k.startswith("exif_"):
            fields[k] = v[:300]
            pats = _STRUCTURED + (_GEN_PATTERNS if k in _TOOL_FIELDS else [])
            for pat, label in pats:
                if pat.search(v[:20000]) and label not in found:
                    found.append(label)
    xmp_types = sorted({m.decode() for m in _XMP_TYPE.findall(raw[:4_000_000])})
    return {"generators": found, "xmp_source_types": xmp_types, "fields": fields}


def metadata_verdict(md: dict) -> tuple[float, str] | None:
    types = set(md["xmp_source_types"])
    if types & AI_TYPES:
        return AI_GENERATED, "XMP metadata declares AI generation (IPTC trainedAlgorithmicMedia)"
    if md["generators"]:
        return AI_GENERATED, "file metadata written by an AI generator: " + ", ".join(md["generators"])
    if types & AI_EDIT_TYPES:
        return AI_EDITED, "XMP metadata declares AI-assisted editing"
    return None


# ---- 4. reverse image search (optional) ------------------------------------------------------------


def reverse_search(raw: bytes, timeout=8.0) -> dict | None:
    """Full matches of the image on the web (Google Cloud Vision Web Detection or TinEye).

    Off unless REVERSE_SEARCH and the provider's key are set: it sends the upload to a third party.
    Vision has no dates; TinEye returns crawl dates, so the earliest sighting is reported."""
    import base64

    import requests

    provider = env("REVERSE_SEARCH", "").lower()
    try:
        if provider == "vision" and env("GOOGLE_VISION_API_KEY", ""):
            body = {
                "requests": [
                    {
                        "image": {"content": base64.b64encode(raw).decode()},
                        "features": [{"type": "WEB_DETECTION", "maxResults": 20}],
                    }
                ]
            }
            r = requests.post(
                "https://vision.googleapis.com/v1/images:annotate",
                params={"key": env("GOOGLE_VISION_API_KEY")},
                json=body,
                timeout=timeout,
            )
            r.raise_for_status()
            web = (r.json().get("responses") or [{}])[0].get("webDetection") or {}
            pages = [p for p in web.get("pagesWithMatchingImages", []) if p.get("fullMatchingImages")]
            return {
                "provider": "google-vision",
                "full_matches": len(web.get("fullMatchingImages", [])),
                "pages": [{"url": p.get("url"), "title": (p.get("pageTitle") or "")[:120]} for p in pages[:5]],
                "earliest": None,
            }
        if provider == "tineye" and env("TINEYE_API_KEY", ""):
            r = requests.post(
                "https://api.tineye.com/rest/search/",
                headers={"x-api-key": env("TINEYE_API_KEY")},
                files={"image_upload": ("upload", raw)},
                timeout=timeout,
            )
            r.raise_for_status()
            matches = (r.json().get("results") or {}).get("matches", [])
            backlinks = [b for m in matches for b in m.get("backlinks", [])]
            backlinks.sort(key=lambda b: b.get("crawl_date") or "9999")
            return {
                "provider": "tineye",
                "full_matches": len(matches),
                "pages": [{"url": b.get("backlink"), "date": b.get("crawl_date")} for b in backlinks[:5]],
                "earliest": backlinks[0] if backlinks else None,
            }
    except Exception as exc:
        return {"provider": provider, "error": type(exc).__name__}
    return None


# ---- combined --------------------------------------------------------------------------------------


def analyse(raw: bytes, rgb: Image.Image) -> dict:
    """{'value', 'reason', 'details', 'ms'} for the `provenance` policy check (strongest evidence wins;
    any AI evidence overrides positive evidence)."""
    t0 = time.time()
    c = read_c2pa(raw)
    wm = read_watermarks(rgb)
    md = read_metadata(raw)
    web = reverse_search(raw)
    verdicts = [v for v in (c2pa_verdict(c), metadata_verdict(md)) if v]
    if wm:
        verdicts.append((AI_GENERATED, "invisible AI watermark: " + wm[0]["watermark"]))
    if web and web.get("full_matches"):
        verdicts.append((AI_EDITED, f"the image is already published on the web ({web['full_matches']} full matches)"))
    if any(v > 0 for v, _ in verdicts):
        value, reason = max((v for v in verdicts if v[0] > 0), key=lambda v: v[0])
    elif verdicts:
        value, reason = verdicts[0]
    else:
        value, reason = NOTHING, "no provenance evidence in the file"
    return {
        "value": value,
        "reason": reason,
        "details": {
            "c2pa": c,
            "watermarks": wm,
            "metadata": {k: md[k] for k in ("generators", "xmp_source_types")},
            "reverse_search": web,
        },
        "ms": int((time.time() - t0) * 1000),
    }
