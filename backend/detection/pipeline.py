"""Mint-Gate pipeline: preprocessing -> six authenticity checks -> policy verdict.

upload bytes
  -> preprocessing (validate, RGB, canonical PNG, SHA-256)
  -> detector view (same resize + JPEG normalisation as the training data)
  -> ResNet-18 | AI detector (CLIP probe + spectral) | copy-move        (image-only checks)
  -> copy engine (Phase 4) against every reference: seed collection, Mint-Gate mints and the
     on-chain NFT index (pgvector):
       copy_exact     PDQ Hamming distance (dihedral)
       copy_semantic  fused SSCD (+ DINOv2) score, density-relative, + region crops
       copy_geometric ALIKED + LightGlue inliers on the top-10
  -> policy engine -> PASS / REVIEW / FAIL + authenticity score
"""

import threading
import time
import uuid

from detection import copy_move
from detection.ai_detector import AIDetector
from detection.clip_similarity import ClipEncoder
from detection.copy_engine import CopyEngine
from detection.policy import Policy
from detection.preprocessing import detector_view, prepare
from detection.report_hash import canonical_hash
from detection.resnet_classifier import ResNetClassifier


class MintGatePipeline:
    def __init__(self, index=None):
        from detection.copy_index import PgCopyIndex

        t = time.time()
        self.policy = Policy()
        self.resnet = ResNetClassifier()
        self.ai = AIDetector()
        self.clip = ClipEncoder()  # features of the AI detector's probe
        self.copy = CopyEngine()
        self.index = index if index is not None else PgCopyIndex(fused=self.copy.dino is not None)
        self._lock = threading.Lock()  # models are not re-entrant on one GPU
        print(
            f"[pipeline] ready in {time.time() - t:.1f}s - copy engine {self.copy.version}, policy {self.policy.version}"
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

    def analyse(self, raw: bytes, filename: str = "", report_id: str | None = None) -> dict:
        img = prepare(raw)
        t0 = time.time()
        view = detector_view(img.rgb)  # same normalisation as the training data
        with self._lock:
            p_forged = self.resnet.predict(view)
            view_emb = self.clip.encode_one(view)
            ai = self.ai.predict(view, view_emb)
            cm = copy_move.analyse(img.rgb)
            cp = self.copy.analyse(img.rgb, self.index)
        values = {
            "resnet": round(p_forged, 4),
            "ai_detector": round(ai["probability"], 4),
            "copy_move": cm["score"],
            **cp["values"],
        }
        decision, score, checks, reasons = self.policy.decide(values)

        details = {
            **cp["details"],
            "copy_move": {"votes": cm["votes"], "shift": cm["shift"]},
            "ai_detector": {"spectral": ai["spectral"], "clip_probe": ai["clip_probe"]},
        }
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
                "resnet18": self.resnet.version,
                "clip": "openai/clip-vit-base-patch32",
                "ai_detector": getattr(self.ai, "version", "none"),
                "copy_engine": self.copy.version,
            },
            "checks": checks,
            "similar_nfts": cp["matches"],
            "evidence": {"copy_move_heatmap": copy_move.heatmap(img.rgb, cm), "copy_matches": cp["evidence"]},
            "timing_ms": int((time.time() - t0) * 1000),
        }
        report["report_hash"] = canonical_hash(report)
        return report, img
