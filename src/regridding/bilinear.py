"""R12 four-center bilinear mapping and strict retrieval-error propagation.

After ``pixi install --locked``, run from repository root:
    pixi run python -m unittest discover -s tests -p 'test_regridding_bilinear.py'

``bilinear_operator(x, y, native_x, native_y, eligible)`` takes matching target
center coordinates in EPSG:6933 metres, actual stored native center axes, and
boolean R04 target eligibility. It returns int64 native row-major indices and
float64 weights, with final dimension NW/NE/SW/SE. No coordinates are snapped
or extrapolated. Exact interior centers choose east/south intervals; the final
center uses the final inward interval. Outside rows are all -1/0.

``apply_bilinear(moisture, sigma, indices, weights, batch_size=65536)`` operates
on one native acquisition/candidate in m³/m³ and returns the four method fields.
All four moisture values must be finite, including zero-weight neighbors; sigma
additionally requires all four finite nonnegative native sigma values. Zero
sigma is valid. Variance is sum(w² sigma²), with no renormalization or clipping.
Shared indices retain W diag(sigma²) W.T under the R06 baseline; this module
does not estimate representation error, calibration or actual correlations.
"""

import numpy as np


RULE = 'four native centers; exact ties east/south, last center inward; all four valid including zero weights'


def bilinear_operator(x, y, native_x, native_y, eligible):
    """Locate native center rectangles and return raw tensor-product distance weights."""
    x, y, nx, ny, eligible = map(np.asarray, (x, y, native_x, native_y, eligible))
    if x.shape != y.shape or x.shape != eligible.shape or eligible.dtype != np.bool_:
        raise ValueError('Require aligned coordinates and boolean eligibility')
    if nx.ndim != 1 or ny.ndim != 1 or min(nx.size, ny.size) < 2:
        raise ValueError('Require at least two native centers on each axis')
    if not all(np.isfinite(a).all() for a in (x, y, nx, ny)):
        raise ValueError('Nonfinite coordinates')
    if not np.all(np.diff(nx) > 0) or not np.all(np.diff(ny) < 0):
        raise ValueError('Native centers must run eastward and southward')
    indices = np.full(x.shape + (4,), -1, dtype='int64')
    weights = np.zeros(x.shape + (4,), dtype='float64')
    valid = eligible & (x >= nx[0]) & (x <= nx[-1]) & (y <= ny[0]) & (y >= ny[-1])
    xx, yy = x[valid], y[valid]
    cols = np.searchsorted(nx, xx, side='right') - 1
    rows = np.searchsorted(-ny, -yy, side='right') - 1
    # The closed last-center boundary uses its inward interval. The query is
    # already inside the center rectangle: no exterior coordinate is clamped.
    cols = np.minimum(cols, len(nx) - 2)
    rows = np.minimum(rows, len(ny) - 2)
    tx = (xx - nx[cols]) / (nx[cols + 1] - nx[cols])
    ty = (ny[rows] - yy) / (ny[rows] - ny[rows + 1])
    origin = rows * len(nx) + cols
    indices[valid] = np.stack((origin, origin + 1, origin + len(nx), origin + len(nx) + 1), axis=-1)
    weights[valid] = np.stack(((1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty), axis=-1)
    if np.any(weights < 0) or np.any(weights > 1) or not np.isfinite(weights).all():
        raise ValueError('Invalid distance weights; no repair')
    return indices, weights


def apply_bilinear(moisture, sigma, indices, weights, batch_size=65536):
    """Evaluate bounded gather batches, with strict availability independent of zero weights."""
    moisture, sigma, indices, weights = map(np.asarray, (moisture, sigma, indices, weights))
    if moisture.ndim != 2 or sigma.shape != moisture.shape:
        raise ValueError('Require aligned two-dimensional native fields')
    if indices.shape != weights.shape or indices.ndim < 2 or indices.shape[-1] != 4:
        raise ValueError('Require four aligned operator slots')
    if indices.dtype.kind not in 'iu' or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError('Require integer indices and a positive batch size')
    shape = indices.shape[:-1]
    out, uncertainty = np.full(shape, np.nan), np.full(shape, np.nan)
    index_rows, weight_rows = indices.reshape(-1, 4), weights.reshape(-1, 4)
    for start in range(0, len(index_rows), batch_size):
        end = min(start + batch_size, len(index_rows))
        idx, w = index_rows[start:end], weight_rows[start:end]
        if np.any(idx < -1) or np.any(idx >= moisture.size) or not np.isfinite(w).all() or np.any((w < 0) | (w > 1)):
            raise ValueError('Invalid operator index/weight')
        present = np.all(idx >= 0, axis=-1)
        if np.any(np.any(idx >= 0, axis=-1) != present) or np.any(w[~present] != 0):
            raise ValueError('Unavailable operator rows must have four -1 indices and zero weights')
        sources = idx[present]
        values, std = moisture.ravel()[sources], sigma.ravel()[sources]
        mv = np.isfinite(values).all(axis=-1)
        uv = mv & (np.isfinite(std) & (std >= 0)).all(axis=-1)
        positions = np.flatnonzero(present) + start
        out.ravel()[positions[mv]] = np.sum(w[present][mv] * values[mv], axis=-1)
        uncertainty.ravel()[positions[uv]] = np.sqrt(np.sum(np.square(w[present][uv] * std[uv]), axis=-1))
        if not np.isfinite(out.ravel()[positions[mv]]).all() or not np.isfinite(uncertainty.ravel()[positions[uv]]).all():
            raise ValueError('Nonfinite arithmetic with available inputs')
    return dict(soil_moisture=out, retrieval_error_std=uncertainty,
                moisture_valid=np.isfinite(out), uncertainty_valid=np.isfinite(uncertainty))
