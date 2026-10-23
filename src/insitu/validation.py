"""Validate native, nearest, and bilinear SME2 against prepared shallow stations.

Run with ``pixi run python scripts/validate_eastern_washington.py --output-dir PATH`` from the
repository root. Inputs are local, verified run/preparation/match products. Station
coordinates are WGS84 degrees; native cells are EPSG:6933, target cells EPSG:5070
at 90 m, timestamps UTC, and moisture and errors m3/m3. Station points and cell
averages have different spatial support. Report descriptive point estimates and
counts without confidence intervals or significance tests.
"""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
import xarray as xr

METHODS = ("native_200m", "nearest", "bilinear")
STATIONS = ("2021:WA:SCAN", "2198:WA:SCAN", "599:WA:SNTL", "707:WA:SNTL", "USCRN.04136")
SPOKANE = "USCRN.04136"
SPOKANE_COORDINATES = (47.4174, -117.5264)


def digest(path):
    """Hash a local input or output without reading it all at once."""
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def containing_cell(x, y, x_bounds, y_bounds):
    """Find the half-open grid cell containing a projected station point."""
    column = int(np.searchsorted(x_bounds[:, 1], x, side="right"))
    row = int(np.searchsorted(-y_bounds[:, 1], -y, side="right"))
    if (column >= len(x_bounds) or row >= len(y_bounds)
            or not x_bounds[column, 0] <= x < x_bounds[column, 1]
            or not y_bounds[row, 1] < y <= y_bounds[row, 0]):
        raise ValueError("Station lies outside the grid")
    return row, column


def paired_rmse(rows, methods=METHODS):
    """Report RMSE on rows with finite sensor and all selected methods."""
    paired = [row for row in rows if row["sensor_moisture_m3_m3"] is not None
              and all(row[method] is not None for method in methods)]
    result = {"pairs": len(paired), "methods": {}}
    for method in methods:
        errors = np.asarray([row[method] - row["sensor_moisture_m3_m3"]
                             for row in paired], dtype=np.float64)
        result["methods"][method] = {
            "rmse_m3_m3": float(np.sqrt(np.mean(errors ** 2))) if len(errors) else None,
        }
    return result


def run(run_dir, preparation_dir, match_dir, output_dir, proj_cache_dir):
    """Extract three frozen fields at each selected station and report paired errors."""
    from regridding.reporting import stage_summary
    from regridding.resources import configure_local_grid
    from regridding.washington import FORWARD_PIPELINE

    run_dir, preparation_dir, match_dir = map(Path, (run_dir, preparation_dir, match_dir))
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("Use a new or empty output directory")
    run_manifest = json.loads((run_dir / "run_manifest.json").read_text())
    if any(run_manifest[f"{method}_stage_status"] != "complete" for method in ("native", "nearest", "bilinear")):
        raise ValueError("Require complete native, nearest and bilinear stages")
    for method in ("native", "nearest", "bilinear"):
        if len(run_manifest["stores"][method]["tasks"]) != 207:
            raise ValueError(f"Unexpected {method} task count")
    verified_stages = {method: stage_summary(run_dir, method)
                       for method in ("native", "nearest", "bilinear")}
    if any(stage["completed_tasks"] != 207 or stage["unfinished_tasks"]
           for stage in verified_stages.values()):
        raise ValueError("A method store failed complete checksum verification")
    preparation = json.loads((preparation_dir / "preparation.json").read_text())
    if digest(preparation_dir / "prepared.csv") != preparation["outputs"]["prepared.csv"]:
        raise ValueError("Prepared station table checksum mismatch")
    if (preparation["zero_policy"] != "exact zero becomes NaN"
            or preparation["sharp_jump_policy"] != "retained"
            or preparation.get("constant_threshold_elapsed_hours") != 12
            or preparation.get("constant_period_policy") != "mask_all_members"):
        raise ValueError("Station masks differ from the approved preparation")
    match_summary = json.loads((match_dir / "summary.json").read_text())
    if (match_summary["tolerance_minutes"] != 30
            or digest(match_dir / "matches.json") != match_summary["matches_sha256"]):
        raise ValueError("Frozen ±30-minute matches differ")
    matches = {(row["station_id"], row["acquisition_index"]): row for row in
               json.loads((match_dir / "matches.json").read_text())}
    station_metadata = {row["station_id"]: row for row in preparation["stations"]}
    if not set(STATIONS) <= station_metadata.keys():
        raise ValueError("Selected station absent from preparation")
    configure_local_grid(proj_cache_dir)
    geographic_to_native = Transformer.from_crs(4326, 6933, always_xy=True,
                                                allow_ballpark=False, only_best=True)
    native_to_target = Transformer.from_pipeline(FORWARD_PIPELINE)
    datasets = {method: xr.open_zarr(run_dir / f"{method}.zarr", chunks=None,
                                    consolidated=False, use_zarr_fill_value_as_mask=False)
                for method in ("native", "nearest", "bilinear")}
    try:
        native, target = datasets["native"], datasets["nearest"]
        if target.sizes != datasets["bilinear"].sizes:
            raise ValueError("Target stores have different dimensions")
        for method, dataset in datasets.items():
            if dataset.sizes["acquisition"] != 69 or dataset.sizes["candidate"] != 3:
                raise ValueError(f"Unexpected {method} dimensions")
            if not np.all(dataset.completion_status.values):
                raise ValueError(f"Incomplete {method} field store")
        cells = {}
        for station in STATIONS:
            details = station_metadata[station]
            latitude, longitude = (SPOKANE_COORDINATES if station == SPOKANE else
                                   (details["latitude"], details["longitude"]))
            native_x, native_y = geographic_to_native.transform(longitude, latitude, errcheck=True)
            target_x, target_y = native_to_target.transform(native_x, native_y, errcheck=True)
            nr, nc = containing_cell(native_x, native_y, native.x_bounds.values, native.y_bounds.values)
            tr, tc = containing_cell(target_x, target_y, target.x_bounds.values, target.y_bounds.values)
            if not (bool(native.center_eligible.isel(y=nr, x=nc).item())
                    and bool(target.center_eligible.isel(y=tr, x=tc).item())):
                raise ValueError(f"Station cell outside approved domain: {station}")
            cells[station] = {"native_row": nr, "native_column": nc,
                              "target_row": tr, "target_column": tc,
                              "latitude_wgs84": latitude, "longitude_wgs84": longitude,
                              "depth_cm": details["depth_cm"]}
        extracted = {}
        for station, cell in cells.items():
            positions = {"native_200m": ("native", cell["native_row"], cell["native_column"]),
                         "nearest": ("nearest", cell["target_row"], cell["target_column"]),
                         "bilinear": ("bilinear", cell["target_row"], cell["target_column"])}
            extracted[station] = {}
            for label, (method, row, column) in positions.items():
                extracted[station][label] = datasets[method].soil_moisture.isel(y=row, x=column).values
        candidates = [str(value) for value in native.candidate.values]
        rows = []
        for acquisition in range(69):
            for station in STATIONS:
                match = matches[(station, acquisition)]
                for candidate_index, candidate in enumerate(candidates):
                    record = {"station_id": station, "candidate": candidate,
                              "acquisition_index": acquisition,
                              "acquisition_id": str(native.acquisition_id.values[acquisition]),
                              "match_status": match["status"],
                              "sensor_time_utc": match.get("sensor_time_utc"),
                              "sensor_moisture_m3_m3": match.get("soil_moisture_m3_m3")}
                    for method in METHODS:
                        value = extracted[station][method][acquisition, candidate_index]
                        record[method] = float(value) if np.isfinite(value) else None
                    rows.append(record)
        metrics = {}
        for candidate in candidates:
            subset = [row for row in rows if row["candidate"] == candidate]
            metrics[candidate] = {"pooled": paired_rmse(subset),
                                  "stations": {station: paired_rmse([row for row in subset
                                      if row["station_id"] == station]) for station in STATIONS}}
        result = {"schema": "eastern-washington-three-method-insitu-v1",
                  "generated_utc": datetime.now(timezone.utc).isoformat(),
                  "methods": list(METHODS), "candidates": candidates,
                  "stations": cells, "excluded_station_ids": ["ODSW", "824:WA:SNTL"],
                  "time_window_utc": preparation["study_window_utc"],
                  "moisture_and_error_units": "m3/m3", "station_crs": "EPSG:4326",
                  "native_crs": "EPSG:6933", "target_crs": "EPSG:5070", "target_spacing_m": 90,
                  "matching": "nearest common sensor label to both acquisition bounds, inclusive ±30 minutes",
                  "masking": {"exact_zero": "missing", "constant_raw_period_elapsed_hours_at_least": 12,
                              "constant_period_policy": "mask_all_members", "sharp_jumps": "retained"},
                  "metrics": metrics,
                  "verified_stage_counts": {method: stage["completed_tasks"]
                                            for method, stage in verified_stages.items()},
                  "provenance": {"run_manifest_sha256": digest(run_dir / "run_manifest.json"),
                                 "preparation_sha256": digest(preparation_dir / "preparation.json"),
                                 "prepared_csv_sha256": digest(preparation_dir / "prepared.csv"),
                                 "matches_sha256": digest(match_dir / "matches.json"),
                                 "matching_summary_sha256": digest(match_dir / "summary.json"),
                                 "code_sha256": digest(__file__)}}
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "station_predictions.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        result["provenance"]["station_predictions_sha256"] = digest(output_dir / "station_predictions.csv")
        (output_dir / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        return result
    finally:
        for dataset in datasets.values():
            dataset.close()
