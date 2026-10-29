"""Match retrievals in UTC and compute pairwise agreement on common 9 km cells."""

import warnings

import numpy as np

MAX_OFFSET_HOURS = 3.0
MIN_PAIRS_PER_CELL = 5


def match_smap(nisar_times, smap_times, swath, smap_moisture):
    """Pick, per acquisition and cell, the time-nearest SMAP pass within 3 h.

    Candidates are granules whose swath covers the cell. Exact |dt| ties choose
    the earlier pass. If the nearest pass has no finite moisture the pair is
    missing; no farther pass is searched (as in R30). Quality flags are not used. Returns
    moisture, signed offset in hours (SMAP - NISAR) and granule index (-1 none).
    """
    n_times, shape = len(nisar_times), smap_times.shape[1:]
    moisture = np.full((n_times, *shape), np.nan)
    offset = np.full((n_times, *shape), np.nan)
    granule = np.full((n_times, *shape), -1, dtype=np.int32)
    smap_ms = smap_times.astype("datetime64[ms]")
    covered = (swath == 1) & ~np.isnat(smap_ms)
    limit_ms = int(MAX_OFFSET_HOURS * 3600 * 1000)
    for t, when in enumerate(np.asarray(nisar_times).astype("datetime64[ms]")):
        dt = np.where(covered, (smap_ms - when).astype(np.int64), 0)
        key = np.where(covered, np.abs(dt) * 2 + (dt > 0), np.iinfo(np.int64).max)
        best = np.argmin(key, axis=0)
        best_dt = np.take_along_axis(dt, best[None], 0)[0]
        ok = np.take_along_axis(covered, best[None], 0)[0] & (np.abs(best_dt) <= limit_ms)
        rows, cols = np.nonzero(ok)
        g = best[rows, cols]
        granule[t, rows, cols] = g
        offset[t, rows, cols] = best_dt[rows, cols] / 3.6e6
        moisture[t, rows, cols] = smap_moisture[g, rows, cols]
    return moisture, offset, granule


def pearson(x, y, axis):
    """Pearson r along an axis over jointly finite values; NaN if either is constant."""
    both = np.isfinite(x) & np.isfinite(y)
    n = both.sum(axis)
    with np.errstate(invalid="ignore", divide="ignore"):
        xm = np.where(both, x, 0).sum(axis) / n
        ym = np.where(both, y, 0).sum(axis) / n
        dx = np.where(both, x - np.expand_dims(xm, axis), 0)
        dy = np.where(both, y - np.expand_dims(ym, axis), 0)
        sxx, syy = (dx ** 2).sum(axis), (dy ** 2).sum(axis)
        r = (dx * dy).sum(axis) / np.sqrt(sxx * syy)
    return np.where((sxx > 0) & (syy > 0), r, np.nan)


def compare(a, b, sigma_a=None):
    """Per-cell and pooled metrics for fields a and b (difference a - b), dims (time, row, col).

    Per cell, across matched times (cells with >= 5 pairs): pair count, mean bias,
    temporal Pearson r and temporal ubRMSD (RMS of the difference after removing
    the cell's mean bias). Pooled bias and RMSD give every matched cell-time pair
    equal weight.

    If sigma_a (stated 1-sigma of a, same dims) is given, coverage is the share of
    matched pairs with finite sigma_a where |a - b| <= sigma_a: "X% of differences
    fall within +/-1 sigma of a's stated uncertainty". b's own error is not
    included. Per cell it needs >= 5 such pairs; sigma_mean is the cell's mean
    sigma_a over those pairs. Pooled coverage weights every such pair equally.
    """
    common = np.isfinite(a) & np.isfinite(b)
    d = np.where(common, a - b, np.nan)
    n_pairs = common.sum(0)
    mapped = n_pairs >= MIN_PAIRS_PER_CELL
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # empty cells yield NaN
        bias_c = np.nanmean(d, 0)
        ub_c = np.sqrt(np.nanmean((d - bias_c) ** 2, 0))
    per_cell = {"n_pairs": n_pairs, "bias": np.where(mapped, bias_c, np.nan),
                "r_temporal": np.where(mapped, pearson(np.where(common, a, np.nan),
                                                       np.where(common, b, np.nan), 0), np.nan),
                "ubrmsd_temporal": np.where(mapped, ub_c, np.nan)}
    pooled = d[common]
    summary = {"matched_pairs": int(common.sum()),
               "times_with_pairs": int((common.sum((1, 2)) > 0).sum()),
               "cells_mapped": int(mapped.sum()),
               "bias": float(pooled.mean()) if pooled.size else None,
               "rmsd": float(np.sqrt((pooled ** 2).mean())) if pooled.size else None,
               "median_r_temporal": _median(per_cell["r_temporal"]),
               "median_ubrmsd_temporal": _median(per_cell["ubrmsd_temporal"])}
    if sigma_a is not None:
        use = common & np.isfinite(sigma_a)
        inside = use & (np.abs(np.where(use, d, 0)) <= np.where(use, sigma_a, 0))
        n_sigma = use.sum(0)
        checked = n_sigma >= MIN_PAIRS_PER_CELL
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            sigma_c = np.nanmean(np.where(use, sigma_a, np.nan), 0)
        per_cell.update(n_sigma_pairs=n_sigma,
                        sigma_mean=np.where(checked, sigma_c, np.nan),
                        coverage=np.where(checked, inside.sum(0) / np.maximum(n_sigma, 1), np.nan))
        summary.update(sigma_pairs=int(use.sum()),
                       coverage_1sigma=float(inside.sum() / use.sum()) if use.any() else None,
                       median_sigma=_median(per_cell["sigma_mean"]))
    return per_cell, summary


def _median(values):
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else None
