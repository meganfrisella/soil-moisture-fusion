"""Build R02 geometry using standard transforms and fixed edge subdivisions.

Run offline routine checks from the repository root after pixi install:
    pixi run python -m unittest discover -s tests -p 'test_regridding_geometry.py'
Verify/download both approved resources through input preparation before the
real-domain invocation:
    pixi run python scripts/prepare_nisar_sme2.py --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_selected
    PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.geometry import prepare_grid; grid = prepare_grid((-121.241744, 46.057370, -117.072163, 48.076483), "data/proj"); print(json.dumps(grid["metadata"], indent=2))'

``prepare_grid(bounds_wgs84, cache_dir)`` takes west/south/east/north in degrees
and the verified PROJ resource cache. It returns north-up 90 m EPSG:5070 lattice
metadata plus the exact EPSG:6933 domain polygon and strict transformer objects.
``target_cells(grid, rows, columns)`` returns geometry for explicit matching
sequences of zero-based indices; cells remain on that full-domain lattice.
Coordinates/segment lengths are metres; clipped areas are m² in EPSG:6933.
Center-outside cells retain their geometry but are ineligible for every method.
No moisture, uncertainty, timestamps, or sampling assumptions are altered.
These callable helpers have no CLI or artifact writes; whole-domain execution,
chunking and exports await R32/R33. Fixed subdivisions do not establish an error
bound or physical geolocation accuracy (A07); R23 verification remains pending.
"""

from pathlib import Path

import numpy as np
from pyproj import Transformer
from pyproj.aoi import AreaOfInterest
import shapely
from shapely.geometry import Polygon, box

from nisar.geometry import BoundingBox
from regridding.inputs import sha256_file
from regridding.transforms import inspect_transformations
from regridding.washington import washington_operations


TARGET_RES_M = 90.0
BOUNDARY_SEGMENT_M = 1000.0
CELL_SEGMENT_M = 30.0


def snapped_bounds(bounds):
    """Snap projected bounds outward to GAIA's 90 m EPSG:5070 lattice."""
    xmin, ymin, xmax, ymax = bounds
    return (float(np.floor(xmin / TARGET_RES_M) * TARGET_RES_M),
            float(np.floor(ymin / TARGET_RES_M) * TARGET_RES_M),
            float(np.ceil(xmax / TARGET_RES_M) * TARGET_RES_M),
            float(np.ceil(ymax / TARGET_RES_M) * TARGET_RES_M))


def rectangle_vertices(bounds, max_segment_m):
    """Subdivide axis-aligned edges canonically, preserving shared-edge coordinates."""
    xmin, ymin, xmax, ymax = bounds
    if not np.isfinite(bounds).all() or not (xmin < xmax and ymin < ymax):
        raise ValueError("Require finite positive-area rectangle bounds")
    if not np.isfinite(max_segment_m) or max_segment_m <= 0:
        raise ValueError("Require positive finite edge-segment length")
    nx = int(np.ceil((xmax - xmin) / max_segment_m))
    ny = int(np.ceil((ymax - ymin) / max_segment_m))
    # Compute shared edges in the same direction before reversing their order.
    x = np.linspace(xmin, xmax, nx + 1, dtype=np.float64)
    y = np.linspace(ymin, ymax, ny + 1, dtype=np.float64)
    return np.concatenate((
        np.column_stack((x[:-1], np.full(nx, ymin))),
        np.column_stack((np.full(ny, xmax), y[:-1])),
        np.column_stack((x[:0:-1], np.full(nx, ymax))),
        np.column_stack((np.full(ny, xmin), y[:0:-1])),
        np.array([[xmin, ymin]], dtype=np.float64),
    ))


def clipped_geometry(polygon, domain):
    """Retain positive-area intersections and distinguish true partial cells."""
    clipped = shapely.intersection(polygon, domain, grid_size=0)
    area = float(clipped.area)
    outside_area = float(shapely.difference(polygon, domain, grid_size=0).area)
    return clipped, area, area > 0, area > 0 and outside_area > 0


def transform_rectangle(bounds, transformer, max_segment_m):
    """Transform fixed edge vertices once, rejecting nonfinite or invalid geometry."""
    coordinates = rectangle_vertices(bounds, max_segment_m)
    x, y = transformer.transform(coordinates[:, 0], coordinates[:, 1], errcheck=True)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Nonfinite transformed polygon coordinates")
    polygon = Polygon(np.column_stack((x, y)))
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Invalid transformed polygon; no automatic geometry repair")
    return polygon


def prepare_grid(bounds_wgs84, cache_dir):
    """Prepare the full-domain lattice and operation provenance without field arrays."""
    bounds = BoundingBox(*bounds_wgs84)
    availability = inspect_transformations(bounds.bounds, cache_dir)
    if availability["status"] != "ready":
        raise ValueError("Preferred coordinate operation unavailable; stop R02 geometry")
    arguments = dict(always_xy=True, area_of_interest=AreaOfInterest(*bounds.bounds),
                     allow_ballpark=False, only_best=True)
    geographic = Transformer.from_crs(4326, 6933, **arguments)
    pinned = washington_operations(bounds.bounds, cache_dir)
    forward, inverse = pinned['forward'], pinned['inverse']
    xmin, ymin = geographic.transform(bounds.west, bounds.south, errcheck=True)
    xmax, ymax = geographic.transform(bounds.east, bounds.north, errcheck=True)
    native_domain = box(xmin, ymin, xmax, ymax)
    projected = transform_rectangle(native_domain.bounds, forward, BOUNDARY_SEGMENT_M)
    grid_bounds = snapped_bounds(projected.bounds)
    left, bottom, right, top = grid_bounds
    rows = int(round((top - bottom) / TARGET_RES_M))
    columns = int(round((right - left) / TARGET_RES_M))
    # Exercise and record the reverse operation too; this is not a validation metric.
    inverse.transform(*projected.exterior.coords[0], errcheck=True)
    operations = {}
    for name, transformer in (("4326_to_6933", geographic),
                              ("6933_to_5070", forward), ("5070_to_6933", inverse)):
        operation = transformer.get_last_used_operation()
        operations[name] = {"pipeline": operation.definition,
                            "description": operation.description,
                            "reported_accuracy_m": operation.accuracy}
    metadata = {
        "bounds_wgs84": dict(zip(("west", "south", "east", "north"), bounds.bounds)),
        "target_crs": "EPSG:5070", "area_crs": "EPSG:6933", "coordinate_units": "m",
        "resolution_m": TARGET_RES_M, "bounds_5070": grid_bounds,
        "shape_rows_columns": [rows, columns],
        "affine_a_b_c_d_e_f": [TARGET_RES_M, 0.0, left, 0.0, -TARGET_RES_M, top],
        "convention": "Northwest outer corner origin; centers at column/row + 0.5",
        "native_domain_bounds_6933": list(native_domain.bounds),
        "domain_boundary_vertices": len(projected.exterior.coords),
        "last_executed_operations": operations,
        "transform_availability": availability,
        "pinned_operation": pinned['metadata'],
        "geometry_code_sha256": sha256_file(Path(__file__)),
        "settings": {"boundary_segment_m": BOUNDARY_SEGMENT_M,
                     "cell_segment_m": CELL_SEGMENT_M,
                     "edge_sampling": "fixed; no adaptive refinement"},
        "limitations": "Fixed edge subdivisions have no certified geometry error bound. A01/A03/A07 remain untested. Washington forward/inverse operations are fixed; full required-grid verification is a separate prerequisite before field execution.",
    }
    return {"metadata": metadata, "domain_6933": native_domain,
            "domain_5070": projected, "forward": forward, "inverse": inverse}


def target_cells(grid, rows, columns):
    """Compute explicit target cells' clipped support and common center eligibility.

    Returns one record per input index pair, including zero-area/outside cells.
    The caller retains ``intersects_domain`` cells in grid metadata and requires
    ``center_eligible`` for every method's moisture and propagated uncertainty.
    No center relocation, field interpolation, sliver deletion, or renormalization.
    Fixed subdivisions preserve shared edges across batches. R33 must still
    establish the execution/chunk strategy before production enumeration.
    """
    rows, columns = np.asarray(rows), np.asarray(columns)
    if rows.ndim != 1 or columns.shape != rows.shape:
        raise ValueError("Require matching one-dimensional row/column sequences")
    if not (np.issubdtype(rows.dtype, np.integer) and np.issubdtype(columns.dtype, np.integer)):
        raise ValueError("Grid indices must be integers")
    height, width = grid["metadata"]["shape_rows_columns"]
    if np.any((rows < 0) | (rows >= height) | (columns < 0) | (columns >= width)):
        raise ValueError("Requested cell outside the full-domain lattice")
    left, _, _, top = grid["metadata"]["bounds_5070"]
    rectangles = []
    for row, column in zip(rows, columns):
        xmin = left + int(column) * TARGET_RES_M
        xmax = left + (int(column) + 1) * TARGET_RES_M
        ymax = top - int(row) * TARGET_RES_M
        ymin = top - (int(row) + 1) * TARGET_RES_M
        rectangles.append((xmin, ymin, xmax, ymax))
    records = []
    for row, column, bounds in zip(rows, columns, rectangles):
        polygon = transform_rectangle(bounds, grid["inverse"], CELL_SEGMENT_M)
        xmin, ymin, xmax, ymax = bounds
        clipped, area, intersects, partial = clipped_geometry(polygon, grid["domain_6933"])
        center = (xmin + TARGET_RES_M / 2, ymax - TARGET_RES_M / 2)
        native_center = grid["inverse"].transform(*center, errcheck=True)
        eligible = bool(intersects and shapely.covers(
            grid["domain_6933"], shapely.Point(*native_center)))
        records.append({
            "row": int(row), "column": int(column), "center_5070": center,
            "polygon_6933": polygon, "clipped_polygon_6933": clipped,
            "intersection_area_m2": area, "intersects_domain": intersects,
            "partial_cell": partial, "center_eligible": eligible,
        })
    return records
