"""Prepare verified SMAP originals on complete 9 km EPSG:6933 box cells; UTC, m³/m³."""

import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
import xarray as xr

from smap.ease2 import box_cells, cell_centers
from comparison.provenance import publish_zarr, run_record, sha256_file, write_json
from smap.catalog import BOX, MANIFEST
from smap.l2e import FLAG_FIELDS, FLOAT_FIELDS, read_box, verify_file

UNITS = {"soil_moisture": "m3 m-3", "soil_moisture_error": "m3 m-3",
         "vegetation_water_content": "kg m-2", "surface_temperature": "K",
         "static_water_body_fraction": "1"}
GRID_TOLERANCE_DEG = 0.01
VALID_RANGE = (0.02, 0.5)  # Provider valid_min/valid_max, counted for reference, not applied.


def check_grid(fields, rows, columns):
    """Reported cell-centre lat/lon must match the EASE row/column geometry."""
    y, x = cell_centers(rows, columns)
    lon, lat = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True).transform(
        *np.meshgrid(x, y))
    present = np.isfinite(fields["latitude"])
    error = max(np.abs(fields["latitude"] - lat)[present].max(initial=0),
                np.abs(fields["longitude"] - lon)[present].max(initial=0))
    if error > GRID_TOLERANCE_DEG:
        raise ValueError(f"SMAP lat/lon disagree with EASE indices by {error:.4f} deg")


def build(manifest, cache_dir, rows, columns):
    """Stack verified granules into one dataset; also return per-granule counts."""
    granules = manifest["granules"]
    stacks = {name: [] for name in (*FLOAT_FIELDS, *FLAG_FIELDS, "tb_time_utc", "swath_cell")}
    counts = []
    for index, g in enumerate(granules, 1):
        path = Path(cache_dir) / g["name"]
        verify_file(path, g["md5"], g["size_bytes"])
        fields = read_box(path, rows, columns)
        check_grid(fields, rows, columns)
        for name in stacks:
            stacks[name].append(fields[name])
        moisture = fields["soil_moisture"]
        counts.append({"name": g["name"], "swath_cells": int(fields["swath_cell"].sum()),
                       "finite_moisture": int(np.isfinite(moisture).sum()),
                       "outside_valid_range": int((np.isfinite(moisture)
                                                   & ((moisture < VALID_RANGE[0])
                                                      | (moisture > VALID_RANGE[1]))).sum()),
                       "masked_millisecond_times": fields["masked_milliseconds"]})
        print(f"[{index}/{len(granules)}] {g['name']}: {counts[-1]['finite_moisture']} finite "
              f"of {counts[-1]['swath_cells']} swath cells", flush=True)
    y, x = cell_centers(rows, columns)
    dims = ("granule", "row", "col")
    data = {name: (dims, np.stack(values)) for name, values in stacks.items()}
    ds = xr.Dataset(data, coords={
        "granule": [g["name"] for g in granules], "row": rows, "col": columns,
        "y": ("row", y), "x": ("col", x),
        "direction": ("granule", [g["direction"] for g in granules]),
        "orbit": ("granule", [g["orbit"] for g in granules])})
    for name, unit in UNITS.items():
        ds[name].attrs["units"] = unit
    ds.tb_time_utc.attrs["description"] = "Per-cell brightness-temperature time, UTC"
    ds.x.attrs.update(units="m", crs="EPSG:6933")
    ds.y.attrs.update(units="m", crs="EPSG:6933")
    return ds, counts



def prepare(cache_dir, output_dir, command):
    """Verify source inputs and publish a new store with its checksum record."""
    cache_dir, output_dir = Path(cache_dir), Path(output_dir)
    manifest_path = output_dir / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    rows, columns = box_cells(**BOX)
    ds, counts = build(manifest, cache_dir, rows, columns)
    record = {"run": run_record(command),
              "manifest_sha256": sha256_file(manifest_path),
              "product": manifest["product"], "box_wgs84": BOX,
              "grid": {"crs": "EPSG:6933", "name": "EASE-Grid 2.0 Global 9 km",
                       "rows": [int(rows[0]), int(rows[-1])],
                       "columns": [int(columns[0]), int(columns[-1])],
                       "rule": "cells lying entirely inside the box"},
              "screen": "none: every finite non-fill soil_moisture is usable; flags not applied",
              "granules": counts}
    ds.attrs["provenance"] = json.dumps(record)
    record["store_sha256"] = publish_zarr(ds, output_dir / "smap_l2e_box.zarr")
    write_json(output_dir / "smap_l2e_box.json", record)
    print(f"{len(rows)} x {len(columns)} cells, {len(counts)} granules -> "
          f"{output_dir / 'smap_l2e_box.zarr'}", flush=True)
    return 0
