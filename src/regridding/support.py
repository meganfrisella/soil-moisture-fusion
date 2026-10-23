"""Build approved R04 comparison masks and target coverage summaries.

Run synthetic checks from the repository root after ``pixi install``:
    pixi run python -m unittest discover -s tests -p 'test_regridding_support.py'

``comparison_masks(moisture, uncertainty, center_eligible)`` takes dictionaries
keyed by nearest and bilinear, containing aligned target
arrays in m³/m³ with fills decoded as NaN. Call separately for one candidate
and one combined UTC acquisition; do not stack candidates or dates. Arrays
must share the same 90 m EPSG:5070 grid, shape, and cell ordering. Returns
per-method masks and selected-method moisture/uncertainty intersections without
altering outputs. An empty common mask remains empty, never pairwise.

``coverage(valid, intersection_area_m2, center_eligible)`` takes matching
boolean masks and clipped areas in EPSG:6933 m². It reports count and area
coverage over all positive-area target intersections, including center-outside
cells in the denominator. Empty denominators return None percentages.

Strict source support reuses ``nisar.observations.window_layout`` and its
``mask``: only positive-area native 200 m EPSG:6933 domain intersections,
including partial cells, with no halo. ``read_window`` applies this mask.
No method may fetch out-of-window neighbors or extrapolate to fill holes.
These helpers perform no source downloads, field runs, or artifact writes.
Mask construction does not establish representativeness or calibration.
"""

import numpy as np

from regridding.quality import input_masks


METHODS = ("nearest", "bilinear")


def comparison_masks(moisture, uncertainty, center_eligible):
    """Intersect selected methods for one candidate/acquisition, retaining full masks."""
    if set(moisture) != set(METHODS) or set(uncertainty) != set(METHODS):
        raise ValueError("Require exactly nearest and bilinear")
    center_eligible = np.asarray(center_eligible)
    if center_eligible.dtype != np.bool_ or center_eligible.ndim not in (1, 2):
        raise ValueError("Center eligibility must be a one- or two-dimensional boolean mask")
    per_method = {}
    moisture_common = center_eligible.copy()
    uncertainty_common = center_eligible.copy()
    for method in METHODS:
        fields = input_masks(moisture[method], uncertainty[method],
                             moisture_fill_value=None, uncertainty_fill_value=None)
        if fields["moisture_valid"].shape != center_eligible.shape:
            raise ValueError("All method arrays must match the target mask shape")
        moisture_valid = center_eligible & fields["moisture_valid"]
        uncertainty_valid = moisture_valid & fields["uncertainty_valid"]
        per_method[method] = {"moisture": moisture_valid, "uncertainty": uncertainty_valid}
        moisture_common &= moisture_valid
        uncertainty_common &= uncertainty_valid
    return {"moisture_common": moisture_common, "uncertainty_common": uncertainty_common,
            "per_method": per_method}


def coverage(valid, intersection_area_m2, center_eligible):
    """Summarize a method or common mask with count and clipped-area denominators."""
    valid, areas, centers = (np.asarray(valid), np.asarray(intersection_area_m2, dtype=float),
                            np.asarray(center_eligible))
    if valid.shape != areas.shape or centers.shape != areas.shape:
        raise ValueError("Coverage arrays must have matching shapes")
    if valid.dtype != np.bool_ or centers.dtype != np.bool_:
        raise ValueError("Coverage validity and center eligibility must be boolean")
    if not np.isfinite(areas).all() or np.any(areas < 0):
        raise ValueError("Intersection areas must be finite and nonnegative")
    intersects = areas > 0
    included = valid & centers & intersects
    total_cells, valid_cells = int(intersects.sum()), int(included.sum())
    total_area, valid_area = float(areas.sum()), float(areas[included].sum())
    return {
        "intersecting_cells": total_cells, "valid_cells": valid_cells,
        "center_excluded_cells": int((intersects & ~centers).sum()),
        "intersection_area_m2": total_area, "valid_area_m2": valid_area,
        "count_coverage_percent": 100 * valid_cells / total_cells if total_cells else None,
        "area_coverage_percent": 100 * valid_area / total_area if total_area else None,
    }
