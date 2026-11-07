"""Mint-Gate pipeline: preprocessing -> authenticity checks -> policy verdict (policy 5.x, Phase 5).

upload bytes
  -> provenance (raw bytes): C2PA credentials, SD / SDXL invisible watermarks, generator metadata,
     optional reverse image search                                       -> `provenance`
  -> preprocessing (validate, RGB, canonical PNG, SHA-256)
  -> detector view (same resize + JPEG normalisation as the training data)
  -> AI detector: linear probes on frozen CLIP-B/32 + SigLIP 2 B/16     -> `ai_detector`
  -> TruFor (GPU profile, non-commercial; REVIEW-only)                   -> `manipulation`
  -> copy-move block matching                                            -> `copy_move`
  -> copy engine (Phase 4) against the seed collection, Mint-Gate mints and the on-chain NFT index:
       copy_exact     PDQ Hamming distance (dihedral)
       copy_semantic  fused SSCD (+ DINOv2) score, density-relative, + region crops
  -> policy engine -> PASS / REVIEW / FAIL + authenticity score
The ResNet-18 of v1-v4 is retired (chance level on real forgeries, docs/models.md).
"""

import threading
import time
import uuid

import config
from detection import copy_move, provenance
from detection.ai_detector import AIDetector
from detection.copy_engine import CopyEngine
from detection.policy import Policy
from detection.preprocessing import detector_view, prepare
from detection.report_hash import canonical_hash


def trufor_enabled() -> bool:
    """TruFor runs only where it is allowed (licence) and affordable (GPU), unless forced."""
    if config.PERMISSIVE_ONLY or config.TRUFOR_MODE == "0":
        return False
    if config.TRUFOR_MODE == "1":
        return True
    import torch

    from detection.trufor import WEIGHTS

    return torch.cuda.is_available() and WEIGHTS.exists()


class MintGatePipeline:
    def __init__(self, index=None):
        from detection.copy_index import PgCopyIndex

        t = time.time()
        self.policy = Policy()
        self.ai = AIDetector()  # owns its frozen backbone(s) (detection/backbones.py)
        self.copy = CopyEngine()
        self.index = index if index is not None else PgCopyIndex(fused=self.copy.dino is not None)
        self.trufor = None
        if trufor_enabled():
            from detection.trufor import TruFor

            self.trufor = TruFor()
        self._lock = threading.Lock()  # models are not re-entrant on one GPU
        print(
            f"[pipeline] ready in {time.time() - t:.1f}s - copy engine {self.copy.version}, "
            f"AI {self.ai.version}, TruFor {'on' if self.trufor else 'off'}, policy {self.policy.version}"
        )

    def fingerprint(self, img):
        """Copy-engine fingerprint of a reference image, as stored in `reference_items`."""
        with self._lock:
            return self.copy.fingerprint(img)

    def add_reference(self, name: str, f, path) -> bool:
        """Make a freshly fingerprinted reference searchable. The pgvector index reads the table
        itself (nothing to do); an in-memory index (tests, small deployments) appends it."""
        add = getattr(self.index, "add", None)
        if add is None:
            return True
        with self._lock:
            if name in self.index.names:
                return False
            add(name, f, path, collection=False)
            return True

    @staticmethod
    def _trufor_heatmap(rgb, tf):
        from detection.trufor import heatmap

        return heatmap(rgb, tf)

    def analyse(self, raw: bytes, filename: str = "", report_id: str | None = None) -> dict:
        img = prepare(raw)
        t0 = time.time()
        view = detector_view(img.rgb)  # same normalisation as the training data
        with self._lock:
            ai = self.ai.predict(view)
            tf = self.trufor.analyse(img.rgb) if self.trufor is not None else None
            cm = copy_move.analyse(img.rgb)
            cp = self.copy.analyse(img.rgb, self.index)
        prov = provenance.analyse(raw, img.rgb)  # raw bytes: metadata / C2PA / watermark (no GPU)
        values = {
            "ai_detector": round(ai["probability"], 4),
            "copy_move": cm["score"],
            **cp["values"],
            "provenance": prov["value"],
        }
        if tf is not None:
            values["manipulation"] = tf["score"]
        decision, score, checks, reasons = self.policy.decide(values)

        details = {
            **cp["details"],
            "copy_move": {"votes": cm["votes"], "shift": cm["shift"]},
            "ai_detector": {"probes": ai["probes"]},
            "provenance": {"reason": prov["reason"], **prov["details"]},
        }
        if tf is not None:
            details["manipulation"] = {"manipulated_area": round(float((tf["map"] >= 0.5).mean()), 4)}
        details["copy_exact"]["uploaded_pdq"] = cp["fingerprint"].pdq.hex()
        for name, extra in details.items():
            checks[name].update(extra)
        for name, c in checks.items():
            c["label"] = self.policy.checks[name]["label"]

        report = {
            "report_id": report_id or uuid.uuid4().hex,
            "created_at": int(time.time()),
            "filename": filename[:200],
            "image_sha256": img.sha256,
            "image": {"width": img.width, "height": img.height, "format": img.source_format},
            "decision": decision,
            "score": score,
            "reasons": reasons,
            "policy_version": self.policy.version,
            "policy_sha256": self.policy.sha256,
            "models": {
                "ai_detector": getattr(self.ai, "version", "none"),
                "ai_backbones": list(getattr(self.ai, "keys", [])),
                "provenance": provenance.VERSION,
                "copy_engine": self.copy.version,
                "manipulation": self.trufor.version if self.trufor is not None else None,
            },
            "checks": checks,
            "similar_nfts": cp["matches"],
            "evidence": {
                "copy_move_heatmap": copy_move.heatmap(img.rgb, cm),
                "copy_matches": cp["evidence"],
                **({"manipulation_heatmap": self._trufor_heatmap(img.rgb, tf)} if tf is not None else {}),
            },
            "timing_ms": int((time.time() - t0) * 1000),
        }
        report["report_hash"] = canonical_hash(report)
        return report, img
