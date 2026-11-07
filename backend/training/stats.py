"""Confidence intervals for every reported number (Phase 5: "every headline number has n and a 95 % CI").

- rates (pass rate, false-alarm rate, detection rate): Wilson score interval (exact enough at the
  sizes used here and well-behaved at 0 % / 100 %, where a percentile bootstrap collapses to a point)
- ROC-AUC: stratified percentile bootstrap (positives and negatives resampled separately)
- model comparisons: *paired* bootstrap of the difference on the same images, so the CI reflects
  the difference itself and not two overlapping marginal intervals

All bootstraps are seeded: re-running a script reproduces its intervals.
"""

import math

import numpy as np

Z95 = 1.959963984540054
N_BOOT = 2000


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rate(mask) -> dict:
    """{'rate', 'k', 'n', 'ci95': [lo, hi]} for a boolean array."""
    m = np.asarray(mask, bool)
    k, n = int(m.sum()), int(m.size)
    lo, hi = wilson(k, n)
    return {"rate": round(k / n, 4) if n else None, "k": k, "n": n, "ci95": [round(lo, 4), round(hi, 4)]}


def auc(y, s) -> float:
    """ROC-AUC via the rank-sum statistic (ties count half)."""
    from scipy.stats import rankdata

    y = np.asarray(y).astype(bool)
    s = np.asarray(s, float)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _strat_indices(y, rng, n_boot):
    pos, neg = np.flatnonzero(y), np.flatnonzero(~y)
    for _ in range(n_boot):
        yield np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])


def auc_ci(y, s, n_boot=N_BOOT, seed=0) -> dict:
    y = np.asarray(y).astype(bool)
    s = np.asarray(s, float)
    rng = np.random.default_rng(seed)
    boots = [auc(y[i], s[i]) for i in _strat_indices(y, rng, n_boot)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "auc": round(auc(y, s), 4),
        "n_pos": int(y.sum()),
        "n_neg": int((~y).sum()),
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
    }


def paired_diff(metric, y, s_new, s_old, n_boot=N_BOOT, seed=0, strata=None) -> dict:
    """Paired bootstrap of metric(y, s_new) - metric(y, s_old), resampling images (stratified by
    `strata`, default the label). Returns the point difference, its 95 % CI and P(diff <= 0)."""
    y = np.asarray(y)
    strata = np.asarray(y if strata is None else strata)
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(strata == g) for g in np.unique(strata)]
    d0 = metric(y, s_new) - metric(y, s_old)
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        i = np.concatenate([rng.choice(g, len(g)) for g in groups])
        diffs[b] = metric(y[i], s_new[i]) - metric(y[i], s_old[i])
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "diff": round(float(d0), 4),
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
        "p_not_better": round(float((diffs <= 0).mean()), 4),
    }


def fmt_rate(r: dict) -> str:
    """'93.1 % [90.2, 95.2] (n=300)'"""
    return f"{100 * r['rate']:.1f} % [{100 * r['ci95'][0]:.1f}, {100 * r['ci95'][1]:.1f}] (n={r['n']})"
