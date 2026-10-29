"""Overlay prepared point sensors and native SME2 acquisitions at their containing EPSG:6933 cells; UTC, m³/m³."""

from datetime import datetime, timezone
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

from insitu.validation import STATIONS, digest


FILENAME = "shallow_sensors_native_overlay.png"
COLORS = {"PMI": "#0072B2", "TSR": "#D55E00", "DSG": "#009E73"}
MARKERS = {"PMI": "o", "TSR": "s", "DSG": "^"}


def plot(preparation_dir, validation_dir, run_dir, output_dir, inventory_path):
    """Check frozen inputs and draw five station panels with all native acquisitions."""
    preparation_dir = Path(preparation_dir)
    validation_dir = Path(validation_dir)
    run_dir = Path(run_dir)
    output_dir = Path(output_dir)
    inventory_path = Path(inventory_path)
    destination = output_dir / FILENAME
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}")

    preparation = json.loads((preparation_dir / "preparation.json").read_text())
    metrics = json.loads((validation_dir / "metrics.json").read_text())
    prepared_path = preparation_dir / "prepared.csv"
    predictions_path = validation_dir / "station_predictions.csv"
    if digest(prepared_path) != preparation["outputs"]["prepared.csv"]:
        raise ValueError("Prepared station CSV failed checksum verification")
    for path, expected in ((preparation_dir / "preparation.json", metrics["provenance"]["preparation_sha256"]),
                           (prepared_path, metrics["provenance"]["prepared_csv_sha256"]),
                           (predictions_path, metrics["provenance"]["station_predictions_sha256"]),
                           (run_dir / "run_manifest.json", metrics["provenance"]["run_manifest_sha256"])):
        if digest(path) != expected:
            raise ValueError(f"Frozen validation input failed checksum verification: {path}")
    if (preparation["zero_policy"] != "exact zero becomes NaN"
            or preparation["constant_period_policy"] != "mask_all_members"
            or preparation["constant_threshold_elapsed_hours"] != 12
            or preparation["sharp_jump_policy"] != "retained"):
        raise ValueError("Unexpected station preparation policy")
    if set(metrics["stations"]) != set(STATIONS):
        raise ValueError("Validation station cohort differs from the five selected sensors")
    if digest(inventory_path) != preparation["identity"]["inventory_sha256"]:
        raise ValueError("Station inventory differs from prepared observations")
    inventory = json.loads(inventory_path.read_text())
    networks = {row["id"]: (row["name"], row["network"]) for row in inventory["stations"]}
    if not set(STATIONS) <= networks.keys():
        raise ValueError("A validation station is absent from the network inventory")

    prepared = pd.read_csv(prepared_path)
    prepared["time_utc"] = pd.to_datetime(prepared["time_utc"], utc=True)
    predictions = pd.read_csv(predictions_path)
    if predictions.duplicated(["station_id", "candidate", "acquisition_index"]).any():
        raise ValueError("Duplicate station/candidate/acquisition extraction")
    if set(predictions.station_id) != set(STATIONS):
        raise ValueError("Prediction table has an unexpected station cohort")

    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    for relative, expected in manifest["stores"]["native"]["static_files"].items():
        path = run_dir / "native.zarr" / relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"Native coordinate/metadata checksum mismatch: {relative}")
    native = xr.open_zarr(run_dir / "native.zarr", chunks=None, consolidated=False,
                          use_zarr_fill_value_as_mask=False)
    try:
        candidates = [str(value) for value in native.candidate.values]
        if candidates != ["PMI", "TSR", "DSG"] or native.sizes["acquisition"] != 69:
            raise ValueError("Unexpected frozen native candidate/acquisition axes")
        acquisitions = pd.DataFrame({
            "acquisition_index": np.arange(native.sizes["acquisition"]),
            "acquisition_id": [str(value) for value in native.acquisition_id.values],
            "start_utc": pd.to_datetime(native.acquisition_start.values, utc=True),
            "end_utc": pd.to_datetime(native.acquisition_end.values, utc=True),
        })
        acquisitions["midpoint_utc"] = acquisitions.start_utc + (
            acquisitions.end_utc - acquisitions.start_utc) / 2
    finally:
        native.close()
    predictions = predictions.merge(acquisitions, on=["acquisition_index", "acquisition_id"],
                                    how="left", validate="many_to_one")
    expected = len(STATIONS) * len(candidates) * len(acquisitions)
    if len(predictions) != expected or predictions.midpoint_utc.isna().any():
        raise ValueError("Missing or mismatched native acquisition records")

    stations = {row["station_id"]: row for row in preparation["stations"]}
    if any(stations[station_id]["name"] != networks[station_id][0] for station_id in STATIONS):
        raise ValueError("Station names disagree with the network inventory")
    window = preparation["study_window_utc"]
    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])
    fig, axes = plt.subplots(len(STATIONS), 1, figsize=(15, 12), sharex=True, sharey=True)
    for ax, station_id in zip(axes, STATIONS):
        sensor = prepared.loc[prepared.station_id == station_id].sort_values("time_utc")
        if sensor.empty:
            raise ValueError(f"No prepared rows for {station_id}")
        moisture = sensor.soil_moisture_m3_m3.to_numpy(dtype=float)
        if np.any(moisture == 0) or np.isfinite(moisture[sensor.constant_period_index.notna()]).any():
            raise ValueError(f"Unmasked zero or constant-period value for {station_id}")
        ax.plot(sensor.time_utc, moisture, color="#343A40", linewidth=1.05,
                label="Shallow sensor", zorder=2)
        station_predictions = predictions.loc[predictions.station_id == station_id]
        counts = []
        for candidate in candidates:
            rows = station_predictions.loc[station_predictions.candidate == candidate]
            values = rows.native_200m.to_numpy(dtype=float)
            finite = np.isfinite(values)
            counts.append(f"{candidate} {finite.sum()}")
            ax.scatter(rows.loc[finite, "midpoint_utc"], values[finite],
                       s=27, marker=MARKERS[candidate], color=COLORS[candidate],
                       edgecolors="white", linewidths=0.35, label=f"SME2 {candidate}", zorder=3)
        name, network = networks[station_id]
        ax.set_title(f"{name} ({network})  ·  native values: " + ", ".join(counts),
                     loc="left", fontsize=11)
        ax.set_ylabel("m³/m³")
        ax.grid(alpha=0.18)
    axes[0].legend(loc="upper right", ncol=4, frameon=False)
    axes[-1].set_xlim(start, end)
    axes[-1].set_ylim(0, 0.5)
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(tz=timezone.utc))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d", tz=timezone.utc))
    axes[-1].set_xlabel("Acquisition midpoint / station observation time (UTC)")
    fig.suptitle("Eastern Washington shallow sensors and native NISAR SME2", fontsize=16, y=0.985)
    fig.text(0.5, 0.012,
             "17 Jun–10 Oct 2026 UTC · 5–5.08 cm point sensors (EPSG:4326) vs containing nominal-200 m cells (EPSG:6933)"
             " · all 69 acquisitions · gaps indicate unavailable data",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.95, bottom=0.075, hspace=0.35)
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=200, facecolor="white")
    plt.close(fig)
    return destination
