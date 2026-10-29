#!/usr/bin/env python3
"""Prepare shallow stations and validate selected Eastern Washington SME2 fields.

Run from the repository root after ``pixi install`` and completing native,
nearest, and bilinear stores in ``--run-dir``::

    pixi run python scripts/validate_eastern_washington.py \
      --inventory metadata/regridding/soil_moisture_stations.json \
      --cache-dir data/insitu_coverage/2026-10-19 \
      --run-dir outputs/eastern_washington/regridding_washington \
      --proj-cache-dir data/proj \
      --output-dir outputs/eastern_washington/insitu_validation/replay

The pipeline verifies or downloads missing station responses, audits coverage,
applies the current zero/constant-period masks, plots selected shallow sensors,
matches both acquisition time bounds within ±30 minutes, and scores native,
nearest, and bilinear values on a common cohort. It also overlays the prepared
station series with every native SME2 acquisition, including unmatched dates. Prepared moisture and errors
are m³/m³, station coordinates EPSG:4326, timestamps UTC. Add
``--with-precipitation --precip-cache-dir DIR`` to inspect large moisture jumps
against local hourly gauges; that diagnostic does not change the validation.
Every output goes under a new --output-dir; source responses stay in --cache-dir.
"""

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from insitu.coverage import run as audit_coverage
from insitu.figures import run as plot_series
from insitu.preparation import run as prepare_series
from insitu.timestamp_workflow import run as match_timestamps
from insitu.validation import STATIONS, run as validate_fields
from insitu.native_overlay import plot as plot_native_overlay


def main(argv=None):
    """Run the ordered station audit, preparation, matching, and validation."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--proj-cache-dir", type=Path, default=Path("data/proj"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tolerance-minutes", type=float, default=30)
    parser.add_argument("--with-precipitation", action="store_true")
    parser.add_argument("--precip-cache-dir", type=Path)
    args = parser.parse_args(argv)
    if args.tolerance_minutes <= 0:
        parser.error("--tolerance-minutes must be positive")
    if args.with_precipitation and args.precip_cache_dir is None:
        parser.error("--with-precipitation requires --precip-cache-dir")
    if args.precip_cache_dir is not None and not args.with_precipitation:
        parser.error("--precip-cache-dir requires --with-precipitation")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    coverage_dir = output / "coverage"
    preparation_dir = output / "preparation"
    matches_dir = output / "matches"
    validation_dir = output / "validation"
    audit_coverage(args.inventory, args.cache_dir, coverage_dir)
    prepared = prepare_series(args.inventory, coverage_dir / "coverage.json", args.cache_dir,
                              preparation_dir)
    excluded = [station["station_id"] for station in prepared["stations"]
                if station["station_id"] not in STATIONS]
    plot_series(preparation_dir, output / "figures", excluded)
    match_timestamps(args.run_dir, preparation_dir, matches_dir, args.tolerance_minutes)
    result = validate_fields(args.run_dir, preparation_dir, matches_dir,
                             validation_dir, args.proj_cache_dir)
    plot_native_overlay(preparation_dir, validation_dir, args.run_dir,
                        output / "figures", args.inventory)
    if args.with_precipitation:
        from insitu.precipitation import run as inspect_precipitation
        inspect_precipitation(SimpleNamespace(
            inventory=args.inventory, preparation_dir=preparation_dir,
            validation_metrics=validation_dir / "metrics.json",
            cache_dir=args.precip_cache_dir, moisture_cache_dir=args.cache_dir,
            output_dir=output / "precipitation"))
    print(json.dumps({candidate: record["pooled"]["pairs"]
                      for candidate, record in result["metrics"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
