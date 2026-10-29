"""Aggregate verified NISAR native fields on 9 km EPSG:6933 cells under both quality screens."""

import json
import math
from pathlib import Path

import numpy as np
import xarray as xr

from smap.ease2 import NISAR_SUBCELLS, block_statistics, box_cells, cell_centers, nested_labels
from comparison.provenance import publish_zarr, run_record, sha256_file, write_json
from smap.catalog import BOX
from regridding.storage import RunStore

CANDIDATES = ("PMI", "TSR", "DSG", "MEAN")
SCREENS = ("all", "recommended")
SUBCELLS = NISAR_SUBCELLS ** 2
MIN_VALID_FRACTION = 0.5
MIN_VALID = math.ceil(MIN_VALID_FRACTION * SUBCELLS)


def screened_pixels(task):
    """Values, per-screen masks and sigma-valid mask for one native candidate slab."""
    values = task.soil_moisture.values
    valid = (task.moisture_valid.values == 1) & np.isfinite(values)
    flag = task.retrieval_quality_flag.values
    recommended = valid & (task.flag_valid.values == 1) & (flag >= 0) & ((flag & 1) == 0)
    sigma = task.reported_uncertainty.values
    sigma_ok = (task.uncertainty_valid.values == 1) & np.isfinite(sigma) & (sigma >= 0)
    return values, {"all": valid, "recommended": recommended}, sigma, sigma_ok


def aggregate(native, rows, columns):
    """Return (acquisition, candidate, screen, row, col) count/mean/SD/sigma arrays."""
    labels, offset_m = nested_labels(native.y.values, native.x.values, rows, columns)
    n_cells = len(rows) * len(columns)
    coverage = np.bincount(labels[labels >= 0], minlength=n_cells)
    if not np.all(coverage == SUBCELLS):
        raise ValueError("Native grid does not fully cover every 9 km box cell")
    if [str(c) for c in native.candidate.values] != list(CANDIDATES[:3]):
        raise ValueError("Native candidates are not PMI, TSR, DSG")
    shape = (native.sizes["acquisition"], len(CANDIDATES), len(SCREENS), len(rows), len(columns))
    count = np.zeros(shape, dtype=np.int32)
    mean, sd, sigma_mean = (np.full(shape, np.nan) for _ in range(3))
    for a in range(shape[0]):
        pixels = [screened_pixels(native.isel(acquisition=a, candidate=c)) for c in range(3)]
        for s, screen in enumerate(SCREENS):
            both = np.logical_and.reduce([p[1][screen] for p in pixels])
            combined = np.where(both, sum(np.where(both, p[0], 0) for p in pixels) / 3, np.nan)
            sigma_all = np.logical_and.reduce([p[3] for p in pixels])
            combined_sigma = np.where(sigma_all, sum(np.where(sigma_all, p[2], 0)
                                                     for p in pixels) / 3, np.nan)
            sources = [(p[0], p[1][screen], p[2], p[3]) for p in pixels]
            sources.append((combined, both, combined_sigma, sigma_all))
            for c, (values, mask, sigma, sigma_ok) in enumerate(sources):
                n, m, d = block_statistics(values, mask, labels, n_cells)
                count[a, c, s], mean[a, c, s], sd[a, c, s] = (v.reshape(shape[3:]) for v in (n, m, d))
                ns, sigma_bar, _ = block_statistics(sigma, mask & sigma_ok, labels, n_cells)
                keep = (n >= MIN_VALID) & (ns >= MIN_VALID)
                sigma_mean[a, c, s] = np.where(keep, sigma_bar, np.nan).reshape(shape[3:])
        print(f"[{a + 1}/{shape[0]}] {str(native.acquisition_start.values[a])[:19]} UTC", flush=True)
    return count, mean, sd, sigma_mean, offset_m



def prepare(run_dir, output_dir, command):
    """Verify source inputs and publish a new store with its checksum record."""
    rows, columns = box_cells(**BOX)
    run_dir, output_dir = Path(run_dir), Path(output_dir)
    manifest_path = run_dir / "run_manifest.json"
    identity = json.loads(manifest_path.read_text())["identity"]
    print("Verifying regridding run checksums...", flush=True)
    with RunStore(run_dir, identity, resume=True) as run:
        status = run.manifest["stores"]["native"]
        with xr.open_zarr(run_dir / "native.zarr", chunks=None, consolidated=False,
                          use_zarr_fill_value_as_mask=False) as native:
            if not np.all(native.completion_status.values == 2):
                raise ValueError("Native stage has incomplete tasks")
            count, mean, sd, sigma, offset_m = aggregate(native, rows, columns)
            start = native.acquisition_start.values
            end = native.acquisition_end.values
            ids = [str(v) for v in native.acquisition_id.values]
        native_record = {"run_manifest_sha256": sha256_file(manifest_path),
                         "native_static_files": len(status["static_files"]),
                         "native_tasks_verified": len(status["tasks"])}
    y, x = cell_centers(rows, columns)
    dims = ("acquisition", "candidate", "screen", "row", "col")
    ds = xr.Dataset(
        {"valid_count": (dims, count), "block_mean": (dims, mean), "within_cell_sd": (dims, sd),
         "soil_moisture": (dims, np.where(count >= MIN_VALID, mean, np.nan)),
         "sigma": (dims, sigma)},
        coords={"acquisition_id": ("acquisition", ids), "candidate": list(CANDIDATES),
                "screen": list(SCREENS),
                "acquisition_start": ("acquisition", start), "acquisition_end": ("acquisition", end),
                "acquisition_mid": ("acquisition", start + (end - start) / 2),
                "row": rows, "col": columns, "y": ("row", y), "x": ("col", x)})
    for name in ("block_mean", "within_cell_sd", "soil_moisture", "sigma"):
        ds[name].attrs["units"] = "m3 m-3"
    ds.soil_moisture.attrs["rule"] = f"block_mean where valid_count >= {MIN_VALID} of {SUBCELLS}"
    ds.sigma.attrs["rule"] = ("linear mean of reported native sigma over screened pixels: "
                              "9 km uncertainty if pixel errors are fully correlated (upper bound)")
    retained = np.isfinite(ds.soil_moisture.values)
    record = {"run": run_record(command),
              "native_input": {"run_dir": str(run_dir), **native_record},
              "screens": {"all": "R03: finite non-fill native moisture regardless of flags",
                          "recommended": "also valid candidate flag with bit 0 clear"},
              "mean_candidate": "200 m mean of PMI, TSR, DSG where all three pass the screen; "
                                "pixel sigma = mean of the three sigma (fully correlated)",
              "aggregation": {"subcells_per_cell": SUBCELLS, "min_valid": MIN_VALID,
                              "weights": "equal (EASE-Grid 2.0 cells are equal-area)",
                              "sd": "population SD of valid native values",
                              "sigma": "linear mean of valid reported sigma (fully correlated "
                                       "pixel errors); >= min_valid sigma pixels",
                              "native_edge_offset_m": offset_m},
              "box_wgs84": BOX, "grid_rows": [int(rows[0]), int(rows[-1])],
              "grid_columns": [int(columns[0]), int(columns[-1])],
              "retained_cells": {f"{c}/{k}": int(retained[:, i, j].sum())
                                 for i, c in enumerate(CANDIDATES) for j, k in enumerate(SCREENS)}}
    ds.attrs["provenance"] = json.dumps(record)
    record["store_sha256"] = publish_zarr(ds, output_dir / "nisar_9km.zarr")
    write_json(output_dir / "nisar_9km.json", record)
    print(json.dumps(record["retained_cells"]), flush=True)
    return 0
