"""EASE-Grid 2.0 Global 9 km cells (EPSG:6933) and nested NISAR 200 m blocks."""

import numpy as np
from pyproj import Transformer

CELL_M = 9008.055210146
X_ORIGIN = -17367530.44516138
Y_ORIGIN = 7314540.830638585
N_COLUMNS = 3856
N_ROWS = 1624
NISAR_SUBCELLS = 45
TOLERANCE_M = 0.01
# NISAR edges sit ~0.03 m from the NSIDC M09 boundaries (float precision at
# |x| ~ 1.2e7 m); 1 m is far below the 100 m centre-to-edge distance, so cell
# membership is unambiguous. The measured offset is returned for the record.
ALIGNMENT_TOLERANCE_M = 1.0


def box_cells(south, west, north, east):
    """Return (rows, columns) of 9 km cells lying entirely inside a WGS84 box.

    EPSG:6933 is cylindrical, so latitude maps only to y and longitude only to x
    and the box is an axis-aligned rectangle in grid coordinates.
    """
    to_grid = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    (x_west, x_east), (y_south, y_north) = to_grid.transform([west, east], [south, north])
    first_col = int(np.ceil((x_west - X_ORIGIN) / CELL_M - TOLERANCE_M / CELL_M))
    last_col = int(np.floor((x_east - X_ORIGIN) / CELL_M + TOLERANCE_M / CELL_M)) - 1
    first_row = int(np.ceil((Y_ORIGIN - y_north) / CELL_M - TOLERANCE_M / CELL_M))
    last_row = int(np.floor((Y_ORIGIN - y_south) / CELL_M + TOLERANCE_M / CELL_M)) - 1
    if first_col > last_col or first_row > last_row:
        raise ValueError("The box contains no complete 9 km cell")
    if first_col < 0 or first_row < 0 or last_col >= N_COLUMNS or last_row >= N_ROWS:
        raise ValueError("The box extends outside the global 9 km grid")
    return np.arange(first_row, last_row + 1), np.arange(first_col, last_col + 1)


def cell_centers(rows, columns):
    """EPSG:6933 centre coordinates for global row and column indices."""
    x = X_ORIGIN + (np.asarray(columns) + 0.5) * CELL_M
    y = Y_ORIGIN - (np.asarray(rows) + 0.5) * CELL_M
    return y, x


def nested_labels(native_y, native_x, rows, columns):
    """Label native centres with a flat index into the (rows, columns) block, else -1.

    Returns (labels, edge offset in metres). Fails unless the native posting is exactly CELL_M / 45 and every native edge
    falls on a 9 km sub-boundary, so blocks are exact rather than approximate.
    """
    native_y, native_x = np.asarray(native_y, float), np.asarray(native_x, float)
    posting = CELL_M / NISAR_SUBCELLS
    for centres, step in ((native_x, posting), (native_y, -posting)):
        if not np.allclose(np.diff(centres), step, rtol=0, atol=TOLERANCE_M):
            raise ValueError("Native posting is not 1/45 of the 9 km cell")
    edges = ((native_x[0] - posting / 2 - X_ORIGIN) / posting,
             (Y_ORIGIN - native_y[0] - posting / 2) / posting)
    offset_m = float(np.max(np.abs(np.array(edges) - np.round(edges))) * posting)
    if offset_m > ALIGNMENT_TOLERANCE_M:
        raise ValueError(f"Native grid is {offset_m:.3f} m from 9 km cell boundaries")
    column = np.floor((native_x - X_ORIGIN) / CELL_M).astype(int) - columns[0]
    row = np.floor((Y_ORIGIN - native_y) / CELL_M).astype(int) - rows[0]
    inside_col = (column >= 0) & (column < len(columns))
    inside_row = (row >= 0) & (row < len(rows))
    labels = row[:, None] * len(columns) + column[None, :]
    return np.where(inside_row[:, None] & inside_col[None, :], labels, -1), offset_m


def block_statistics(values, valid, labels, n_cells):
    """Per-cell count, mean and population SD of valid native values (equal-area cells)."""
    use = valid & (labels >= 0)
    lab, v = labels[use], values[use]
    count = np.bincount(lab, minlength=n_cells)
    total = np.bincount(lab, weights=v, minlength=n_cells)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
        squares = np.bincount(lab, weights=(v - mean[lab]) ** 2, minlength=n_cells)
        sd = np.sqrt(squares / count)
    return count, mean, sd
