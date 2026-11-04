"""Geometric copy verification: ALIKED keypoints + LightGlue matching + MAGSAC homography (Phase 4).

Replaces ORB + brute-force matching. ALIKED (BSD-3) and LightGlue (Apache-2.0, ALIKED weights)
come from kornia. Like ORB+RANSAC, the evidence is the number of matches that agree on ONE
homography: many inliers mean one image is a geometric transform (crop / scale / rotation /
perspective / partial paste) of the other. The query is also matched mirrored, so flipped copies
are verified too.
"""

import base64

import cv2
import numpy as np
import torch
from PIL import Image, ImageOps

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MATCH_SIZE = 512  # longest side fed to ALIKED
MAX_KEYPOINTS = 1024
cv2.setNumThreads(1)


class LocalMatcher:
    def __init__(self, device=DEVICE, max_keypoints=MAX_KEYPOINTS):
        import kornia.feature as KF

        self.device = torch.device(device)
        self.extractor = KF.ALIKED.from_pretrained(
            "aliked-n16", max_num_keypoints=max_keypoints, detection_threshold=-1, device=self.device
        ).eval()
        self.matcher = KF.LightGlue("aliked").to(self.device).eval()

    @staticmethod
    def _resize(img: Image.Image) -> Image.Image:
        img = img.convert("RGB")
        s = MATCH_SIZE / max(img.size)
        return img.resize((max(16, round(img.width * s)), max(16, round(img.height * s))), Image.BILINEAR)

    @torch.no_grad()
    def describe(self, img: Image.Image, mirrored=False) -> dict:
        """Keypoints + descriptors on the CPU (fp16 descriptors keep reference caches small)."""
        img = self._resize(img)
        if mirrored:
            img = ImageOps.mirror(img)
        x = torch.from_numpy(np.asarray(img)).permute(2, 0, 1)[None].float().div_(255).to(self.device)
        f = self.extractor(x)[0]
        return {
            "kp": f.keypoints.float().cpu(),
            "desc": f.descriptors.half().cpu(),
            "size": (img.width, img.height),
            "gray": np.asarray(img.convert("L")),
        }

    @torch.no_grad()
    def match(self, a: dict, b: dict):
        """(inliers, matched_points_a, matched_points_b) after LightGlue + MAGSAC homography."""
        if len(a["kp"]) < 8 or len(b["kp"]) < 8:
            return 0, None, None

        def side(f):
            return {
                "keypoints": f["kp"][None].to(self.device),
                "descriptors": f["desc"][None].to(self.device).float(),
                "image_size": torch.tensor([f["size"]], device=self.device, dtype=torch.float32),
            }

        m = self.matcher({"image0": side(a), "image1": side(b)})["matches"][0].cpu().numpy()
        if len(m) < 8:
            return 0, None, None
        pa = a["kp"].numpy()[m[:, 0]]
        pb = b["kp"].numpy()[m[:, 1]]
        H, mask = cv2.findHomography(pa, pb, cv2.USAC_MAGSAC, 4.0, maxIters=2000, confidence=0.999)
        if H is None:
            return 0, None, None
        keep = mask.ravel().astype(bool)
        return int(keep.sum()), pa[keep], pb[keep]

    def verify(self, query: dict, query_mirrored: dict | None, ref: dict):
        """Best of the plain and mirrored query: (inliers, mirrored?, points_q, points_r)."""
        n, pq, pr = self.match(query, ref)
        best = (n, False, pq, pr)
        if query_mirrored is not None:
            n2, pq2, pr2 = self.match(query_mirrored, ref)
            if n2 > n:
                best = (n2, True, pq2, pr2)
        return best


def draw(query: dict, ref: dict, pq, pr, limit=80) -> str:
    """Side-by-side inlier visualisation as a base64 PNG (evidence image)."""
    g1, g2 = query["gray"], ref["gray"]
    h = max(g1.shape[0], g2.shape[0])
    canvas = np.full((h, g1.shape[1] + g2.shape[1]), 255, np.uint8)
    canvas[: g1.shape[0], : g1.shape[1]] = g1
    canvas[: g2.shape[0], g1.shape[1] :] = g2
    vis = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    if pq is not None:
        for (x1, y1), (x2, y2) in list(zip(pq, pr))[:limit]:
            p1, p2 = (int(x1), int(y1)), (int(x2) + g1.shape[1], int(y2))
            cv2.line(vis, p1, p2, (255, 200, 0), 1, cv2.LINE_AA)
            cv2.circle(vis, p1, 2, (0, 200, 255), -1)
            cv2.circle(vis, p2, 2, (0, 200, 255), -1)
    ok, png = cv2.imencode(".png", vis)
    return base64.b64encode(png.tobytes()).decode() if ok else ""
