"""Mint-Gate pipeline: preprocessing -> six authenticity checks -> policy verdict.

upload bytes
  -> preprocessing (validate, RGB, canonical PNG, SHA-256)
  -> detector view (same resize + JPEG normalisation as the training data)
  -> ResNet-18 | AI ensemble (spectral + CLIP probe) | copy-move   (image-only checks)
  -> pHash + CLIP search over the minted-NFT index         (plagiarism checks)
  -> ORB + RANSAC on the shortlisted candidates
  -> policy engine -> PASS / REVIEW / FAIL + authenticity score
"""

import hashlib
import json
import threading
import time
import uuid

import config
from detection import copy_move, feature_match
from detection.ai_detector import AIDetector
from detection.clip_similarity import ClipEncoder
from detection.hashing import phash_int
from detection.policy import Policy
from detection.preprocessing import detector_view, load_path, prepare
from detection.reference_index import ReferenceIndex
from detection.resnet_classifier import ResNetClassifier

ATTESTED_FIELDS = (
    "report_id",
    "created_at",
    "image_sha256",
    "decision",
    "score",
    "policy_version",
    "policy_sha256",
    "models",
    "checks",
)


def canonical_hash(report: dict) -> str:
    """SHA-256 over the attested subset of the report (this goes on-chain)."""
    body = {k: report[k] for k in ATTESTED_FIELDS}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class MintGatePipeline:
    def __init__(self):
        t = time.time()
        self.policy = Policy()
        self.index = ReferenceIndex()
        self.resnet = ResNetClassifier()
        self.ai = AIDetector()
        self.clip = ClipEncoder()
        self._lock = threading.Lock()  # models are not re-entrant on one GPU
        print(
            f"[pipeline] ready in {time.time() - t:.1f}s - {len(self.index)} reference NFTs, "
            f"policy {self.policy.version}"
        )

    def register_minted(self, report_id: str, img=None):
        """Add a minted artwork to the reference index (duplicate protection).

        The image is stored as uploads/<report_id>.png so it can be shown and ORB-matched.
        """
        path = config.UPLOADS_DIR / f"{report_id}.png"
        if img is None:
            img = load_path(path)
        elif not path.exists():
            img.save(path)
        with self._lock:
            if f"minted/{report_id}.png" in self.index.files:
                return
            emb = self.clip.encode_one(img)
            self.index.add(f"{report_id}.png", phash_int(img), emb)

    def sync_minted_from_chain(self, chain, fetch_image):
        """Rebuild minted-artwork entries from on-chain VerifiedMint events.

        Hosting disks can be wiped on restart; the blockchain (+ IPFS) is the
        durable source of truth, so duplicate protection survives redeploys.
        `fetch_image(token)` returns (report_id, PIL image) or None.
        """
        added = 0
        for token in chain.minted_tokens():
            try:
                got = fetch_image(token)
                if got:
                    self.register_minted(*got)
                    added += 1
            except Exception as exc:  # one unreachable IPFS object must not stop the rest
                print(f"[sync] token {token.get('token_id')}: {exc}")
        print(f"[sync] {added} minted artworks restored from chain")

    def analyse(self, raw: bytes, filename: str = "") -> dict:
        img = prepare(raw)
        t0 = time.time()
        view = detector_view(img.rgb)  # same normalisation as the training data
        with self._lock:
            p_forged = self.resnet.predict(view)
            view_emb = self.clip.encode_one(view)
            ai = self.ai.predict(view, view_emb)
            cm = copy_move.analyse(img.rgb)
            ph = phash_int(img.rgb)
            emb = self.clip.encode_one(img.rgb)
        p_ai = ai["probability"]

        sims, dists, cand = self.index.search(ph, emb)
        sparse = self.index.sparse
        q = feature_match.describe(img.rgb)
        best_orb, orb_vis, matches = (0, None, None), "", []
        for i in cand:
            ref = feature_match.describe(load_path(self.index.path(i)))
            inl, good, inliers = feature_match.match(q, ref)
            matches.append(
                {
                    "filename": self.index.files[i],
                    "clip_similarity": round(float(sims[i]), 4),
                    "phash_distance": int(dists[i]),
                    "orb_inliers": inl,
                }
            )
            # Keypoint matches only count as copy evidence against sparse references.
            if sparse[i] and inl > best_orb[0]:
                best_orb = (inl, i, (ref, inliers))
        matches.sort(key=lambda m: -m["clip_similarity"])

        nearest_hash, hash_ref = self.index.sparse_min_distance(dists)
        copy_score, copy_ref = self.index.copy_score(sims)
        values = {
            "resnet": round(p_forged, 4),
            "ai_detector": round(p_ai, 4),
            "phash": nearest_hash,
            "orb": int(best_orb[0]),
            "copy_move": cm["score"],
            "clip": round(copy_score, 4),
        }
        decision, score, checks, reasons = self.policy.decide(values)

        if best_orb[0] >= self.policy.checks["orb"]["review"]:
            ref, inliers = best_orb[2]
            orb_vis = feature_match.draw(q, ref, inliers)

        details = {
            "phash": {
                "nearest": self.index.files[hash_ref] if hash_ref is not None else None,
                "uploaded_phash": f"{ph:016x}",
            },
            "clip": {
                "nearest": self.index.files[copy_ref],
                "similarity": round(float(sims[copy_ref]), 4),
                "reference_neighbour_similarity": round(float(self.index.nn_sim[copy_ref]), 4),
            },
            "orb": {"nearest": self.index.files[best_orb[1]] if best_orb[1] is not None else None},
            "copy_move": {"votes": cm["votes"], "shift": cm["shift"]},
            "ai_detector": {"spectral": ai["spectral"], "clip_probe": ai["clip_probe"]},
        }
        for name, extra in details.items():
            checks[name].update(extra)
        for name, c in checks.items():
            c["label"] = self.policy.checks[name]["label"]

        report = {
            "report_id": uuid.uuid4().hex,
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
            },
            "checks": checks,
            "similar_nfts": matches[:5],
            "evidence": {"copy_move_heatmap": copy_move.heatmap(img.rgb, cm), "orb_matches": orb_vis},
            "timing_ms": int((time.time() - t0) * 1000),
        }
        report["report_hash"] = canonical_hash(report)
        return report, img
