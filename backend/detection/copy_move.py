"""Block-matching copy-move (clone) detection with shift-vector voting.

Classic approach (Fridrich et al., 2003), simplified:
  1. slide an overlapping BxB window (step 1) over the grey image;
  2. skip low-texture blocks (flat backgrounds match trivially);
  3. describe each block by a coarse, quantised 4x4 thumbnail;
  4. group identical descriptors and, for every pair of matching blocks,
     vote for their displacement vector;
  5. a cloned region produces *many* block pairs sharing one displacement.
The score is the *peak ratio*: votes of the strongest displacement divided by
the runner-up, which separates a single clone from naturally repetitive art.
"""

import base64
import io
from collections import Counter, defaultdict

import numpy as np
from PIL import Image

WORK_SIZE = 256
BLOCK = 16
CELL = BLOCK // 4
MIN_STD = 10.0  # texture threshold
MIN_SHIFT = 24  # ignore overlapping / adjacent blocks
QUANT = 12
MAX_GROUP = 40  # bigger groups = repetitive texture, not a clone


def _box_sum(a: np.ndarray, k: int) -> np.ndarray:
    """Sum over every kxk window (valid positions) via an integral image."""
    s = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]


def _features(gray: np.ndarray):
    n = BLOCK * BLOCK
    mean = _box_sum(gray, BLOCK) / n
    var = _box_sum(gray * gray, BLOCK) / n - mean * mean
    ys, xs = np.nonzero(np.sqrt(np.maximum(var, 0)) >= MIN_STD)

    cells = _box_sum(gray, CELL) / (CELL * CELL)  # mean of every CELLxCELL window
    offs = [(i * CELL, j * CELL) for i in range(4) for j in range(4)]
    feats = np.stack([cells[ys + dy, xs + dx] for dy, dx in offs], axis=1)
    return np.round(feats / QUANT).astype(np.int16), xs, ys


def analyse(img: Image.Image):
    """Return dict(score, shift, boxes, size); boxes are cloned blocks in work-size coords."""
    work = img.convert("L")
    scale = WORK_SIZE / max(work.size)
    work = work.resize((max(BLOCK, round(work.width * scale)), max(BLOCK, round(work.height * scale))), Image.BILINEAR)
    gray = np.asarray(work, dtype=np.float64)

    feats, xs, ys = _features(gray)
    empty = {"score": 0.0, "votes": 0, "shift": None, "boxes": [], "size": list(work.size)}
    if len(feats) < 2:
        return empty

    _, inv, counts = np.unique(feats, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    keep = (counts[inv] >= 2) & (counts[inv] <= MAX_GROUP)
    groups = defaultdict(list)
    for g, x, y in zip(inv[keep], xs[keep], ys[keep]):
        groups[g].append((int(x), int(y)))

    votes = Counter()
    members = defaultdict(set)
    for pts in groups.values():
        p = np.array(sorted(pts))
        i, j = np.triu_indices(len(p), 1)
        d = p[j] - p[i]
        # canonical direction so (a->b) and (b->a) vote together
        flip = (d[:, 0] < 0) | ((d[:, 0] == 0) & (d[:, 1] < 0))
        d[flip] *= -1
        far = (d**2).sum(1) >= MIN_SHIFT**2
        for a, b, (dx, dy) in zip(i[far], j[far], d[far]):
            key = (int(dx), int(dy))
            votes[key] += 1
            members[key].update([tuple(p[a]), tuple(p[b])])

    if not votes:
        return empty
    ranked = votes.most_common(25)
    shift, top = ranked[0]
    # Runner-up peak, ignoring +-1px neighbours of the winning shift (resampling jitter).
    second = next((c for s, c in ranked[1:] if abs(s[0] - shift[0]) > 1 or abs(s[1] - shift[1]) > 1), 0)
    # A clone is ONE dominant displacement; natural repetition (pixel grids,
    # stripes, symmetric features) spreads votes over several shifts.
    ratio = top / max(second, 1)
    return {
        "score": round(float(ratio), 3),
        "votes": int(top),
        "shift": list(shift),
        "boxes": [list(b) for b in sorted(members[shift])],
        "size": list(work.size),
    }


def heatmap(img: Image.Image, result) -> str:
    """Overlay detected cloned blocks in red; returns base64 PNG."""
    base = img.convert("RGB").resize(tuple(result["size"]), Image.NEAREST).convert("RGBA")
    over = np.zeros((base.height, base.width, 4), dtype=np.uint8)
    for x, y in result["boxes"]:
        over[y : y + BLOCK, x : x + BLOCK] = (255, 0, 60, 110)
    comp = Image.alpha_composite(base, Image.fromarray(over, "RGBA")).convert("RGB")
    comp = comp.resize((comp.width * 2, comp.height * 2), Image.NEAREST)
    buf = io.BytesIO()
    comp.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
