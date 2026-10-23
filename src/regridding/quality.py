"""Apply R03 validity and R05–R06 conditional uncertainty operations.

After ``pixi install``, run the synthetic checks from the repository root:
    pixi run python -m unittest discover -s tests -p 'test_regridding_quality.py'

``input_masks(moisture, uncertainty, moisture_fill_value=...,
uncertainty_fill_value=...)`` accepts matching arrays in m³/m³. Supply each
field's declared fill value, or None for fields already decoded by
``nisar.observations.read_frame``. Returns independent boolean masks without
modifying the arrays. Flags do not screen moisture; existing flag reporting
remains in ``nisar.quality``. Reported zeros remain valid uncertainty values.

``contributing_uncertainty_available(weights, uncertainty_valid)`` accepts
matching arrays of dimensionless coefficients and gathered boolean uncertainty
validity, with contributors on the last axis. A nonzero coefficient requires
valid uncertainty; zero-weight missing inputs have no effect. This is a
validity check, not a weighting algorithm or a covariance calculation.

``conditional_variance(uncertainty, uncertainty_valid)`` takes the original
reported m³/m³ values and their R03 boolean validity mask. Under approved A05,
it treats valid values as standard-deviation proxies and returns float64
variances in (m³/m³)², with NaN for unavailable uncertainty and zero preserved.
This conditional conversion does not establish supplier semantics or calibration.
All downstream propagated results must retain that qualification.

``baseline_variance(weights, uncertainty, uncertainty_valid)`` evaluates
sum(w² σ²) along the last (contributor) axis, conditional on A05/A06. All arrays
must match; each row must reference distinct native cells (combine repeated
references before calling). Coefficients are supplied by a later method, not
constructed here. Unavailable nonzero-weight uncertainty invalidates the row;
zero-weight missing uncertainty is ignored. No weight renormalization occurs.
Returns variance in (m³/m³)²; caller applies the method's moisture/geometry mask.
This returns marginal variances only. Preserve weights/source IDs to represent
shared-source target covariance W diag(σ²) Wᵀ; outputs are not independent.

Native inputs have 200 m EPSG:6933 support at their original UTC acquisition
times. These helpers do not run a regridding method, assert calibration,
determine common masks (R04), or resolve regression refit missingness.
These callable helpers have no CLI, downloads, or artifact writes.
"""

import numpy as np


def input_masks(moisture, uncertainty, *, moisture_fill_value, uncertainty_fill_value):
    """Return independent moisture/uncertainty masks and a reported-zero mask."""
    moisture, uncertainty = np.asarray(moisture), np.asarray(uncertainty)
    if moisture.shape != uncertainty.shape:
        raise ValueError("Moisture and uncertainty shapes must match")
    moisture_valid = np.isfinite(moisture)
    uncertainty_valid = np.isfinite(uncertainty) & (uncertainty >= 0)
    if moisture_fill_value is not None:
        moisture_valid &= moisture != moisture_fill_value
    if uncertainty_fill_value is not None:
        uncertainty_valid &= uncertainty != uncertainty_fill_value
    return {
        "moisture_valid": moisture_valid,
        "uncertainty_valid": uncertainty_valid,
        "reported_zero": uncertainty_valid & (uncertainty == 0),
    }


def conditional_variance(uncertainty, uncertainty_valid):
    """Square standard-deviation proxies under A05; unavailable values remain NaN."""
    sigma = np.asarray(uncertainty, dtype=np.float64)
    valid = np.asarray(uncertainty_valid)
    if sigma.shape != valid.shape or valid.dtype != np.bool_:
        raise ValueError("Require a matching boolean uncertainty-validity mask")
    if np.any(valid & (~np.isfinite(sigma) | (sigma < 0))):
        raise ValueError("Valid uncertainty must be finite and nonnegative")
    variance = np.full(sigma.shape, np.nan, dtype=np.float64)
    with np.errstate(over="raise", invalid="raise"):
        np.square(sigma, out=variance, where=valid)
    return variance


def contributing_uncertainty_available(weights, uncertainty_valid):
    """Require valid uncertainty for every nonzero coefficient in a nonempty row."""
    weights, uncertainty_valid = np.asarray(weights), np.asarray(uncertainty_valid)
    if weights.ndim < 1 or weights.shape != uncertainty_valid.shape:
        raise ValueError("Matching arrays with a contributor axis are required")
    if uncertainty_valid.dtype != np.bool_:
        raise ValueError("Uncertainty validity must be boolean")
    if not np.isfinite(weights).all():
        raise ValueError("Coefficients must be finite")
    contributes = weights != 0
    return contributes.any(axis=-1) & np.all(~contributes | uncertainty_valid, axis=-1)


def baseline_variance(weights, uncertainty, uncertainty_valid):
    """Propagate marginal variance for fixed coefficients and uncorrelated native errors."""
    weights = np.asarray(weights, dtype=np.float64)
    available = contributing_uncertainty_available(weights, uncertainty_valid)
    variance = conditional_variance(uncertainty, uncertainty_valid)
    if variance.shape != weights.shape:
        raise ValueError("Uncertainty and coefficient shapes must match")
    active = (weights != 0) & available[..., None]
    terms = np.zeros(weights.shape, dtype=np.float64)
    with np.errstate(over="raise", invalid="raise"):
        np.square(weights, out=terms, where=active)
        np.multiply(terms, variance, out=terms, where=active)
        result = terms.sum(axis=-1)
    return np.where(available, result, np.nan)
