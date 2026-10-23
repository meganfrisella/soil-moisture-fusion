"""Build the approved R32 float64 time-series schema without field computation.

After ``pixi install --locked``, run from repository root:
    pixi run python -m unittest discover -s tests -p 'test_regridding_storage.py'

``acquisition_records(groups, sources, native_layout)`` takes ordered lists of
source-table indices, JSON source records (filename, checksum, start_utc,
end_utc and optional product metadata), and JSON native layout identity.
It returns sorted, uniquely identified combined acquisitions. Exact source
times and layout enter the R07 stream identity; UTC microsecond conversion
rejects precision loss. Zone-less product timestamps are explicitly UTC.

``store_template(method, grid, acquisitions, sources)`` returns only static
geometry/coordinates and provenance as an xarray Dataset, not a time-series
allocation. grid supplies x/y center arrays, EPSG integer, intersection_area_m2,
center_eligible and, for native, native_rows/native_columns. Native support is
nominal 200 m EPSG:6933; pass actual HDF5 dx_m/dy_m posting (defaults 200/-200
for synthetic fixtures). Target is exactly 90 m EPSG:5070. Areas are EPSG:6933 m². Data fields
defined by ``task_fields`` are created lazily by regridding.storage, in m³/m³.
Missing/zero masks retain distinct meanings. These helpers neither download
data nor run a method; they have no CLI or artifact writes.
"""

import hashlib
import json

import numpy as np
import pandas as pd
from pyproj import CRS
import xarray as xr

from nisar.quality import ALGORITHMS
from regridding.support import METHODS

SCHEMA_VERSION = "eastern-washington-zarr-v1"
DEFAULT_OUTPUT_DIR = "outputs/eastern_washington/regridding_selected"
TIME_UNITS = "microseconds since 1970-01-01 00:00:00 UTC"


def canonical_json(value):
    """Encode provenance deterministically, rejecting NaN/Infinity and non-JSON values."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def utc_microseconds(value):
    """Normalize a product timestamp to UTC without rounding sub-microsecond values."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Acquisition/source time cannot be missing")
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return int(stamp.as_unit("us", round_ok=False).asm8.view("i8"))


def acquisition_records(groups, sources, native_layout):
    """Create stable stream identities, retaining distinct groups with equal start times."""
    canonical_json(native_layout)
    canonical_json(sources)
    if not sources or len({s["filename"] for s in sources}) != len(sources):
        raise ValueError("Source filenames must be unique and nonempty")
    records = []
    for group in groups:
        indices = list(group)
        if (not indices or len(set(indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < len(sources) for i in indices)):
            raise ValueError("Require distinct valid integer source indices")
        ordered = sorted(indices, key=lambda i: (utc_microseconds(sources[i]["start_utc"]),
                                                 sources[i]["filename"]))
        for i in ordered:
            if not sources[i]["checksum"] or utc_microseconds(sources[i]["end_utc"]) < utc_microseconds(sources[i]["start_utc"]):
                raise ValueError("Source checksum and valid start/end are required")
        identity = {"schema": SCHEMA_VERSION, "native_layout": native_layout,
                    "sources": [sources[i] for i in ordered]}
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        records.append({"id": f"native:{digest}", "source_indices": ordered,
                        "start_us": min(utc_microseconds(sources[i]["start_utc"]) for i in ordered),
                        "end_us": max(utc_microseconds(sources[i]["end_utc"]) for i in ordered)})
    records.sort(key=lambda r: (r["start_us"], [sources[i]["filename"] for i in r["source_indices"]]))
    if not records or len({r["id"] for r in records}) != len(records):
        raise ValueError("Require unique nonempty acquisitions")
    return records


def task_fields(method):
    """Return required task fields as dtype/fill pairs; no lossy encoding is used."""
    # The shared storage contract still has an active caller in the archived
    # regression exploration. The production CLI selects only native/nearest/bilinear.
    if method not in ("native", *METHODS, "regression"):
        raise ValueError("Unknown regridding method")
    fields = {"soil_moisture": ("float64", np.nan),
              "reported_uncertainty" if method == "native" else "retrieval_error_std": ("float64", np.nan),
              "moisture_valid": ("uint8", 0), "uncertainty_valid": ("uint8", 0)}
    if method == "native":
        fields.update(reported_zero=("uint8", 0), retrieval_quality_flag=("int64", -1),
                      flag_valid=("uint8", 0), source_index=("int32", -1))
    return fields


def store_template(method, grid, acquisitions, sources):
    """Describe static geometry, UTC coordinates and provenance for one method store."""
    task_fields(method)
    native = method == "native"
    epsg = 6933 if native else 5070
    dx, dy = (float(grid.get('dx_m', 200.)), float(grid.get('dy_m', -200.))) if native else (90., -90.)
    if not np.isfinite([dx,dy]).all() or dx <= 0 or dy >= 0:
        raise ValueError('Require finite positive x and negative y posting')
    if grid["epsg"] != epsg:
        raise ValueError("Grid CRS disagrees with method support")
    x, y = (np.asarray(grid[k], dtype=np.float64) for k in ("x", "y"))
    for coordinates, delta in ((x, dx), (y, dy)):
        if (coordinates.ndim != 1 or not coordinates.size or not np.isfinite(coordinates).all()
                or not np.allclose(np.diff(coordinates), delta, rtol=0,
                                   atol=8*np.finfo(float).eps*max(1.,np.abs(coordinates).max()))):
            raise ValueError("Require finite, regularly spaced north-up grid centers")
    shape = (len(y), len(x))
    area = np.asarray(grid["intersection_area_m2"], dtype=np.float64)
    centers = np.asarray(grid["center_eligible"])
    if area.shape != shape or centers.shape != shape or centers.dtype != np.bool_:
        raise ValueError("Require matching areas and boolean center eligibility")
    if not np.isfinite(area).all() or np.any(area < 0) or np.any(centers & (area <= 0)):
        raise ValueError("Invalid clipped geometry/eligibility")
    canonical_json(acquisitions)
    canonical_json(sources)
    ids = [r["id"] for r in acquisitions]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Acquisition IDs must be unique")
    ds = xr.Dataset(coords={
        "acquisition": np.arange(len(ids), dtype=np.int32),
        "candidate": np.asarray(ALGORITHMS, dtype="T"), "y": y, "x": x,
        "bounds": np.arange(2, dtype=np.uint8),
        "acquisition_id": ("acquisition", np.asarray(ids, dtype="T")),
        "acquisition_start": ("acquisition", np.array([r["start_us"] for r in acquisitions], dtype=np.int64)),
        "acquisition_end": ("acquisition", np.array([r["end_us"] for r in acquisitions], dtype=np.int64)),
    })
    for name, standard_name in (("x", "projection_x_coordinate"), ("y", "projection_y_coordinate")):
        ds[name].attrs.update(units="m", standard_name=standard_name, bounds=f"{name}_bounds")
    ds["x_bounds"] = (("x", "bounds"), np.column_stack((x-dx/2, x+dx/2)))
    ds["y_bounds"] = (("y", "bounds"), np.column_stack((y-dy/2, y+dy/2)))
    for name in ("acquisition_start", "acquisition_end"):
        ds[name].attrs.update(units=TIME_UNITS, calendar="proleptic_gregorian", timezone="UTC")
    crs = CRS.from_epsg(epsg)
    ds["spatial_ref"] = xr.DataArray(np.int32(0), attrs={**crs.to_cf(), "spatial_ref": crs.to_wkt()})
    ds["domain_intersection_area"] = (("y", "x"), area, {"units": "m2", "area_crs": "EPSG:6933"})
    ds["domain_intersects"] = (("y", "x"), (area > 0).astype("uint8"))
    ds["center_eligible"] = (("y", "x"), centers.astype("uint8"))
    if native:
        for name, dim in (("native_rows", "y"), ("native_columns", "x")):
            values = np.asarray(grid[name])
            if values.shape != (ds.sizes[dim],) or values.dtype.kind not in "iu" or not np.all(np.diff(values) == 1):
                raise ValueError("Require contiguous native row/column identities")
            ds[name] = (dim, values.astype("int64"))
    ds.attrs.update(schema_version=SCHEMA_VERSION, method=method, crs=f"EPSG:{epsg}",
                    affine=[dx, 0., float(x[0]-dx/2), 0., dy, float(y[0]-dy/2)],
                    posting_m=[dx,dy], nominal_posting_m=200 if native else 90,
                    sources=sources, acquisitions=acquisitions,
                    uncertainty_scope="Conditional native retrieval error only; representation/model error and bias unquantified",
                    assumptions=["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"])
    for var in ds.variables.values():
        var.encoding["_FillValue"] = None
    return ds


def validate_task(method, values, template, acquisition):
    """Reject inconsistent masks/provenance before writing a complete candidate slab."""
    fields = task_fields(method)
    if set(values) != set(fields):
        raise ValueError("Task fields do not match the approved schema")
    shape = (template.sizes["y"], template.sizes["x"])
    result = {}
    for name, (dtype, _) in fields.items():
        array = np.asarray(values[name])
        if array.shape != shape:
            raise ValueError(f"Wrong shape for {name}")
        if dtype == "uint8" and not np.isin(array, [0, 1]).all():
            raise ValueError(f"Invalid boolean mask {name}")
        if dtype.startswith("int") and (array.dtype.kind not in "iu" or np.any(array < -1)):
            raise ValueError(f"Invalid integer field {name}")
        if dtype.startswith("int") and (np.any(array > np.iinfo(dtype).max)):
            raise ValueError(f"Integer overflow in {name}")
        result[name] = array.astype(dtype)
    moisture = result["soil_moisture"]
    sigma = result["reported_uncertainty" if method == "native" else "retrieval_error_std"]
    mv, uv = (result[name].astype(bool) for name in ("moisture_valid", "uncertainty_valid"))
    if np.isinf(moisture).any() or np.isinf(sigma).any() or np.any(np.isfinite(sigma) & (sigma < 0)):
        raise ValueError("Invalid scientific values")
    if not np.array_equal(mv, np.isfinite(moisture)) or not np.array_equal(uv, np.isfinite(sigma)):
        raise ValueError("Values and independent masks disagree")
    eligible = template["domain_intersects" if method == "native" else "center_eligible"].values.astype(bool)
    if np.any((mv | uv) & ~eligible) or (method != "native" and np.any(uv & ~mv)):
        raise ValueError("Values outside approved support")
    if method == "native":
        fv, flags, source = result["flag_valid"].astype(bool), result["retrieval_quality_flag"], result["source_index"]
        if not np.array_equal(fv, flags >= 0) or np.any(fv & ~eligible):
            raise ValueError("Flag validity/provenance mismatch")
        allowed = template.attrs["acquisitions"][acquisition]["source_indices"]
        if not np.isin(source, [-1, *allowed]).all() or np.any((mv | uv | fv) & (source < 0)):
            raise ValueError("Source tuple missing or from another acquisition")
        if np.any((source >= 0) & ~eligible):
            raise ValueError("Source outside native domain")
        if not np.array_equal(result["reported_zero"].astype(bool), uv & (sigma == 0)):
            raise ValueError("Reported-zero mask disagrees with uncertainty")
    return result
