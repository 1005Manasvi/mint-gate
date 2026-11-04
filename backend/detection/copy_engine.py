"""Phase-4 copy-detection engine (replaces pHash + CLIP copy score + ORB).

  1. exact     PDQ (8 dihedral query hashes) against every reference      -> copy_exact (bits)
  2. retrieve  ANN on the fused SSCD (+ DINOv2) vector -> top-50, plus SSCD region crops (collage)
  3. score     density-relative: cos - alpha * nn(ref) - beta * bg(query), per model, fused with w
               (nn = the reference's own nearest neighbour in the index, bg = how "generic" the
               query is against a background set of AI images, SSCD's score normalisation)
                                                                            -> copy_semantic
  4. verify    ALIKED + LightGlue + MAGSAC on the top-10                    -> copy_geometric (inliers)

Every parameter comes from `model/copy_engine.json`, written by `python -m benchmark.copydet
evaluate` from the VAL split of the benchmark, so the engine runs exactly what was measured.

Collection rule (v1 lesson, kept): members of a generative collection only count as copied on an
exact / near-exact match (PDQ) unless `collection_semantic` is enabled by the benchmark; siblings
share backgrounds, bodies and keypoints by design.
"""

import json
from dataclasses import dataclass, field

import numpy as np

from config import MODEL_DIR
from detection import fingerprints as fp

PARAMS_FILE = MODEL_DIR / "copy_engine.json"
BACKGROUND_FILE = MODEL_DIR / "copydet" / "background.npz"
NO_MATCH_BITS = 256


def load_params(path=PARAMS_FILE) -> dict:
    return json.loads(path.read_text())


@dataclass
class Fingerprint:
    pdq: bytes
    pdq_quality: int
    pdq8: np.ndarray  # (8, 32) uint8: the dihedral hashes (query side only)
    sscd: np.ndarray
    dino: np.ndarray | None = None
    crops: np.ndarray | None = None  # (n_windows, 512) SSCD region descriptors (query side only)

    def vector(self, w: float) -> np.ndarray:
        """The ANN vector: inner product of two of them = w * cos_sscd + (1 - w) * cos_dino."""
        if self.dino is None:
            return self.sscd
        return np.concatenate([np.sqrt(w) * self.sscd, np.sqrt(1 - w) * self.dino]).astype(np.float32)


@dataclass
class Reference:
    key: object  # index-specific id
    name: str  # filename / minted/<id> / chain:contract:token
    collection: bool
    sscd: np.ndarray
    dino: np.ndarray | None
    nn_sscd: float
    nn_dino: float
    image_path: object = None
    extra: dict = field(default_factory=dict)


class CopyEngine:
    def __init__(self, params: dict | None = None, device: str | None = None, matcher: bool = True, models=None):
        """`models` = (sscd, dino, matcher, background) injects stand-ins (tests); default: the real models."""
        self.p = params or load_params()
        if models is not None:
            self.sscd, self.dino, self.matcher, bg = models
        else:
            kw = {} if device is None else {"device": device}
            self.sscd = fp.SSCD(**kw)
            self.dino = fp.DinoV2("facebook/" + self.p["dino"], **kw) if self.p.get("dino") else None
            self.matcher = None
            if matcher and self.p.get("lightglue"):
                from detection.local_match import LocalMatcher

                self.matcher = LocalMatcher(**kw)
            bg = dict(np.load(BACKGROUND_FILE))
        self.bg = {k: np.asarray(v, np.float32) for k, v in bg.items()}
        # Region crops cost ~4 s on a 2-vCPU box: without a GPU the engine runs its CPU profile
        # (benchmark.copydet.cpu_crop_variant picks what fits the 1.5 s budget, possibly none).
        on_gpu = getattr(self.sscd, "device", "cpu") == "cuda"
        self.profile = "gpu" if on_gpu or models is not None else "cpu"
        self.regions = self.p.get("regions") if self.profile == "gpu" else self.p.get("regions_cpu")

    @property
    def version(self) -> str:
        return f"{self.p['version']}+{self.profile}"

    # ---- fingerprints -------------------------------------------------------------------------

    def fingerprint_many(self, images, query=False) -> list[Fingerprint]:
        """Reference fingerprints (query=True adds the dihedral PDQ hashes and region crops)."""
        sscd = self.sscd.encode(images)
        dino = self.dino.encode(images) if self.dino else [None] * len(images)
        out = []
        for i, im in enumerate(images):
            h, q = fp.pdq(im)
            out.append(
                Fingerprint(
                    pdq=h,
                    pdq_quality=q,
                    pdq8=fp.pdq_dihedral(im) if query else np.frombuffer(h, np.uint8)[None],
                    sscd=sscd[i],
                    dino=dino[i],
                    crops=self._crops(im) if query and self.regions else None,
                )
            )
        return out

    def _crops(self, im):
        r = self.regions
        windows = fp.region_crops(im, sizes=tuple(r.get("sizes", (0.5, 0.7))), steps=r.get("steps", 3))
        return self.sscd.encode(windows, size=r.get("px"))

    def fingerprint(self, img, query=False) -> Fingerprint:
        return self.fingerprint_many([img], query=query)[0]

    # ---- scoring --------------------------------------------------------------------------------

    def _bg(self, model: str, vec) -> float:
        p = self.p[model]
        if not p["beta"]:
            return 0.0
        sims = self.bg[model] @ vec
        return p["beta"] * float(np.sort(sims)[-self.p.get("bg_k", 10) :].mean())

    def scores(self, f: Fingerprint, refs: list[Reference]) -> np.ndarray:
        """Normalised fused score of the query against each reference (benchmark.copydet.normalised)."""
        if not refs:
            return np.zeros(0, np.float32)
        s = np.array([r.sscd for r in refs]) @ f.sscd
        s = s - self.p["sscd"]["alpha"] * np.array([r.nn_sscd for r in refs]) - self._bg("sscd", f.sscd)
        if self.dino is not None:
            d = np.array([r.dino for r in refs]) @ f.dino
            d = (
                d
                - self.p[self.p["dino"]]["alpha"] * np.array([r.nn_dino for r in refs])
                - self._bg(self.p["dino"], f.dino)
            )
            w = self.p["fusion"]["w"]
            s = np.maximum(s, d) if self.p["fusion"]["mode"] == "max" else w * s + (1 - w) * d
        if f.crops is not None:  # a copied work inside a collage: best window, minus the crop penalty
            c = (np.array([r.sscd for r in refs]) @ f.crops.T).max(1)
            c = c - self.p["sscd"]["alpha"] * np.array([r.nn_sscd for r in refs]) - self._bg("sscd", f.sscd)
            s = np.maximum(s, c - self.regions["d"])
        return s.astype(np.float32)

    # ---- exact copies -------------------------------------------------------------------------------

    def _exact(self, img, f: Fingerprint, index):
        """PDQ-close references, filtered per kind of reference:
        one-off works: PDQ quality >= pdq_min_quality (flat images make the hash unreliable);
        collection members: siblings are PDQ-close by design (CryptoPunks: 0-10 bits apart), so the
        match must be pixel-identical too: <= member_max_diff_px strongly differing pixels at 64x64
        (training/calibrate.py). The quality gate does not apply there: flat NFT art has low PDQ
        quality, and the pixel check is the stronger test anyway."""
        cands = index.exact(f.pdq8, self.p["pdq_max_bits"])
        max_px = self.p.get("member_max_diff_px", 0)
        out, q64, tried = [], None, 0
        for r, d in sorted(cands, key=lambda x: x[1]):
            if not r.collection:
                if f.pdq_quality >= self.p["pdq_min_quality"]:
                    out.append((r, d))
                continue
            if any(x.collection for x, _ in out) or r.image_path is None or tried >= 5:
                continue  # one verified member is enough evidence; at most 5 image loads
            tried += 1
            try:
                ref = fp.rgb_small(index.load_image(r))
            except (OSError, ValueError):
                continue
            q64 = fp.rgb_small(img) if q64 is None else q64
            if fp.differing_pixels(q64, ref) <= max_px:
                out.append((r, d))
        return out

    # ---- analysis ---------------------------------------------------------------------------------

    def analyse(self, img, index) -> dict:
        """Returns {"values": {copy_exact, copy_semantic, copy_geometric}, "details", "matches", "evidence"}."""
        f = self.fingerprint(img, query=True)
        exact = self._exact(img, f, index)
        cands = index.knn(f.vector(self.p["fusion"].get("w", 1.0)), self.p["retrieve_k"])
        if f.crops is not None:
            for c in f.crops:
                cands += index.knn_sscd(c, self.p["crop_k"])
        cands = list({r.key: r for r in cands}.values())
        sc = self.scores(f, cands)
        allowed = np.array([self.p.get("collection_semantic", False) or not r.collection for r in cands], bool)
        order = np.argsort(-sc)
        best_i = next((int(i) for i in order if allowed[i]), None)

        inliers, verified, evidence = {}, None, ""
        if self.matcher is not None and cands:
            q, qm = self.matcher.describe(img), self.matcher.describe(img, mirrored=True)
            for i in order[: self.p["verify_k"]]:
                r = cands[int(i)]
                if not (allowed[i] or self.p.get("collection_geometric", False)) or r.image_path is None:
                    continue
                try:
                    ref = self.matcher.describe(index.load_image(r))
                except (OSError, ValueError):
                    continue
                n, _mir, pq, pr = self.matcher.verify(q, qm, ref)
                inliers[r.key] = n
                if verified is None or n > verified[0]:
                    verified = (n, r, q if not _mir else qm, ref, pq, pr)
            if verified and verified[0] >= self.p["evidence_min_inliers"]:
                from detection.local_match import draw

                evidence = draw(verified[2], verified[3], verified[4], verified[5])

        exact_bits = min((d for _, d in exact), default=NO_MATCH_BITS)
        exact_ref = min(exact, key=lambda x: x[1])[0] if exact else None
        semantic = float(sc[best_i]) if best_i is not None else 0.0
        matches = [
            {
                "reference": cands[int(i)].name,
                "collection_member": cands[int(i)].collection,
                "copy_score": round(float(sc[i]), 4),
                "inliers": int(inliers.get(cands[int(i)].key, 0)),
                **cands[int(i)].extra,
            }
            for i in order[:5]
        ]
        values = {"copy_exact": int(exact_bits), "copy_semantic": round(semantic, 4)}
        details = {
            "copy_exact": {"nearest": exact_ref.name if exact_ref else None, "pdq_quality": f.pdq_quality},
            "copy_semantic": {"nearest": cands[best_i].name if best_i is not None else None},
        }
        if self.matcher is not None:  # only when the benchmark gate adopted LightGlue
            values["copy_geometric"] = int(verified[0]) if verified else 0
            details["copy_geometric"] = {"nearest": verified[1].name if verified else None}
        return {
            "values": values,
            "details": details,
            "matches": matches,
            "evidence": evidence,
            "fingerprint": f,
        }
