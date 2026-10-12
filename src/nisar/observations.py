"""Shared native-window SME2 ingestion for visualization and coverage analysis.

Native cells/edges are EPSG:6933 metres, times UTC, and moisture/reported uncertainty
m³/m³. Missing cells are NaN; no resampling or uncertainty recalibration occurs.
The same grid alignment and missing-value rules apply to both workflows.
"""

from pathlib import Path

import h5py
import numpy as np

from nisar.geometry import INVERSE
from nisar.quality import ALGORITHMS

GRID = 'science/LSAR/SME2/grids'


def native(value):
    """Convert HDF5 scalar metadata to JSON-compatible values."""
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, np.ndarray):
        return [native(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    return value


def grid_definition(grid):
    """Read and validate the native EASE grid origin, posting and integer indices."""
    dx = float(grid['xCoordinateSpacing'][()])
    dy = float(grid['yCoordinateSpacing'][()])
    if not np.isfinite([dx, dy]).all() or dx <= 0 or dy >= 0:
        raise ValueError('Expected positive x and negative y grid posting')
    if 'projection' in grid and int(grid['projection'][()]) != 6933:
        raise ValueError('Expected SME2 grid in EPSG:6933')
    rows, cols = grid['EASEGridRowIndex'][()], grid['EASEGridColumnIndex'][()]
    if not len(rows) or not len(cols) or np.any(np.diff(rows) != 1) or np.any(np.diff(cols) != 1):
        raise ValueError('Expected contiguous EASE row/column indices')
    x, y = grid['xCoordinates'][()], grid['yCoordinates'][()]
    xo, yo = float(x[0]) - int(cols[0]) * dx, float(y[0]) - int(rows[0]) * dy
    if not (np.allclose(x, xo + cols * dx, rtol=0, atol=.01)
            and np.allclose(y, yo + rows * dy, rtol=0, atol=.01)):
        raise ValueError('Native coordinates disagree with EASE indices')
    return dx, dy, xo, yo, rows, cols


def window_layout(bounds, reference):
    """Build the full requested window, including cells outside the reference granule.

    All cells with positive-area intersection count equally toward the coverage
    denominator, including partially clipped boundary cells.
    """
    dx, dy, xo, yo, _, _ = grid_definition(reference[GRID])
    rectangle = bounds.projected
    xmin, ymin, xmax, ymax = rectangle.bounds
    c0 = int(np.floor((xmin - xo) / dx - .5)) + 1
    c1 = int(np.ceil((xmax - xo) / dx + .5))
    r0 = int(np.floor((ymax - yo) / dy - .5)) + 1
    r1 = int(np.ceil((ymin - yo) / dy + .5))
    xx, yy = np.meshgrid(xo + np.arange(c0, c1) * dx, yo + np.arange(r0, r1) * dy)
    mask = ((xx + dx / 2 > xmin) & (xx - dx / 2 < xmax)
            & (yy - dy / 2 > ymin) & (yy + dy / 2 < ymax))
    if not mask.any():
        raise ValueError('No native cells intersect the viewing box')
    centre = rectangle.centroid
    return dict(r0=r0, r1=r1, c0=c0, c1=c1, xx=xx, yy=yy, mask=mask,
                dx=dx, dy=dy, xo=xo, yo=yo, square=rectangle,
                bounds_wgs84=bounds.bounds, centre_xy=(centre.x, centre.y),
                centre_lon_lat=INVERSE.transform(centre.x, centre.y))


def read_window(grid, field, layout, rows, cols):
    """Read available cells and pad outside-granule or absent fields with NaN."""
    result = np.full(layout['mask'].shape, np.nan)
    if field not in grid:
        return result
    dataset = grid[field]
    if dataset.shape != (len(rows), len(cols)):
        raise ValueError(f'Unexpected native array shape: {field}')
    ra, rb = max(layout['r0'], int(rows[0])), min(layout['r1'], int(rows[-1]) + 1)
    ca, cb = max(layout['c0'], int(cols[0])), min(layout['c1'], int(cols[-1]) + 1)
    if ra >= rb or ca >= cb:
        return result
    values = dataset[ra-int(rows[0]):rb-int(rows[0]), ca-int(cols[0]):cb-int(cols[0])].astype(float)
    values[values == float(dataset.attrs.get('_FillValue', -9999))] = np.nan
    result[ra-layout['r0']:rb-layout['r0'], ca-layout['c0']:cb-layout['c0']] = values
    result[~layout['mask']] = np.nan
    return result


def read_frame(path, layout):
    """Read one granule without requiring its grid to contain the entire box."""
    with h5py.File(path) as f:
        grid = f[GRID]
        dx, dy, xo, yo, rows, cols = grid_definition(grid)
        if not np.allclose([dx, dy, xo, yo], [layout[k] for k in ('dx', 'dy', 'xo', 'yo')],
                           rtol=0, atol=1e-5):
            raise ValueError(f'Native grid alignment differs: {Path(path).name}')
        identity = f['science/LSAR/identification']
        keys = ('zeroDopplerStartTime', 'zeroDopplerEndTime', 'absoluteOrbitNumber',
                'orbitPassDirection', 'trackNumber', 'frameNumber', 'productVersion',
                'productSpecificationVersion', 'compositeReleaseId', 'processingDateTime')
        metadata = {k: native(identity[k][()]) if k in identity else 'not supplied' for k in keys}
        metadata['granule'] = Path(path).name
        data = {}
        for algorithm in ALGORITHMS:
            prefix = f'algorithmCandidates/{algorithm}'
            arrays = {field: read_window(grid, f'{prefix}/{field}', layout, rows, cols)
                      for field in ('soilMoisture', 'soilMoistureUncertainty', 'retrievalQualityFlag')}
            arrays['soilMoistureUncertainty'][arrays['soilMoistureUncertainty'] < 0] = np.nan
            arrays['available'] = prefix in grid
            data[algorithm] = arrays
    count = sum(int(np.isfinite(a['soilMoisture']).sum()) for a in data.values())
    return {'metadata': metadata, 'data': data, 'numeric_moisture_cells': count}
