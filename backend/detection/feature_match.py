"""ORB keypoint matching with RANSAC geometric verification.

A raw "good match" count is unreliable (two different artworks share many
local corners). Requiring the matches to agree on a single homography makes
the count meaningful: many RANSAC inliers means one image is a geometric
transform (crop / resize / rotation) of the other.
"""

import base64

import cv2
import numpy as np
from PIL import Image

ORB_SIZE = 512
cv2.setNumThreads(1)
_orb = cv2.ORB_create(nfeatures=1500)


def _gray(img: Image.Image) -> np.ndarray:
    g = np.asarray(img.convert("L"))
    scale = ORB_SIZE / max(g.shape)
    return cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)


def describe(img: Image.Image):
    g = _gray(img)
    kp, des = _orb.detectAndCompute(g, None)
    return g, kp, des


def match(query, ref):
    """Return (inliers, good_matches, homography_mask_matches) for two `describe()` outputs."""
    _, kp1, d1 = query
    _, kp2, d2 = ref
    if d1 is None or d2 is None or len(kp1) < 8 or len(kp2) < 8:
        return 0, 0, []
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 8:
        return 0, len(good), []
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        return 0, len(good), []
    inl = [m for m, k in zip(good, mask.ravel()) if k]
    return len(inl), len(good), inl


def draw(query, ref, inliers, limit=60) -> str:
    """Side-by-side visualisation of inlier matches as a base64 PNG."""
    g1, kp1, _ = query
    g2, kp2, _ = ref
    vis = cv2.drawMatches(
        g1,
        kp1,
        g2,
        kp2,
        inliers[:limit],
        None,
        matchColor=(0, 200, 255),
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )
    ok, png = cv2.imencode(".png", vis)
    return base64.b64encode(png.tobytes()).decode() if ok else ""
