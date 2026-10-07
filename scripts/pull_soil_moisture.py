#!/usr/bin/env python3
"""Download native AgriMet soil-moisture CSV, selecting inclusive UTC dates.

Run from the repository root in the Pixi environment (standard library only):
    pixi run python scripts/pull_soil_moisture.py --list-only
        List planned requests without network calls or saving files.
    pixi run python scripts/pull_soil_moisture.py
        Download CSV files to data/soil_moisture/ without login.
    pixi run python -m unittest discover -s tests -p 'test_data_downloads.py'
        Run offline downloader tests.

Edit the constants below to change the stations, station timezones, parameters,
inclusive UTC observation dates, or destination. USBR serves database-generated
CSV over HTTPS; its backend cloud/server is not publicly established. Selected
rows retain the native header, local timestamps, value strings, missing markers,
and any returned flags byte-for-byte. No reformatting, unit conversion, gap
filling, averaging, or quality filtering is applied. All three source downloaders
retain native formats and use the same UTC date-boundary helper.

The source DateTime column uses station local time, including daylight saving
time. Enclosing local dates are queried, then rows are selected using UTC bounds.
For ODSW, the default interval selects 2026-06-23 17:00 through 2026-09-16 16:45
America/Los_Angeles at the native 15-minute cadence. Ambiguous/nonexistent local
DST timestamps fail rather than guessing UTC times.

All XSM parameters retain the published unit label "percent", including values
such as 0.17. The CSV does not embed units; PARAMETER_UNITS below records this
source convention. Depths are inches below the surface. No uncertainties are
supplied or inferred. Missing records/values are reported without imputation.

Only native CSV is saved, without sidecars or converted exports. Downloads are
validated for structure and UTC selection before publication; valid existing
files are skipped, and invalid existing files are left untouched. The query API
does not supply archive checksums. Failed downloads are cleaned up; interrupted
transfers restart on the next run.

The terminal summary reports response-body bytes
before row selection, average transfer speed, download duration, and total run
duration. Skipped files are excluded; failed transfers are included.

Sources:
    https://www.usbr.gov/pn/agrimet/HydrometWebService.doc
    https://www.usbr.gov/pn/agrimet/aginfo/station_params.html#ODSW
"""

import argparse
import csv
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
import io
from pathlib import Path
import sys
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.download_utils import DownloadStats, copy_response, publish_file, utc_date_bounds


STATIONS = {"ODSW": "America/Los_Angeles"}
PARAMETERS = ("XSM2", "XSM8", "XSM20", "XSM40")
PARAMETER_UNITS = {parameter: "percent" for parameter in PARAMETERS}
DEPTHS_INCHES = {"XSM2": 2, "XSM8": 8, "XSM20": 20, "XSM40": 40}
START_DATE = "2026-06-24"
END_DATE = "2026-09-16"  # Inclusive, UTC.
OUTPUT_DIR = ROOT / "data/soil_moisture"
INSTANT_URL = "https://www.usbr.gov/pn-bin/instant.pl"
INTERVAL_MINUTES = 15


def date_bounds():
    return utc_date_bounds(START_DATE, END_DATE)


def request_url(station, zone):
    lower, upper = date_bounds()
    local_zone = ZoneInfo(zone)
    return INSTANT_URL + "?" + urlencode({
        "list": ",".join(f"{station.lower()} {p.lower()}" for p in PARAMETERS),
        "start": lower.astimezone(local_zone).date().isoformat(),
        "end": (upper - timedelta(microseconds=1)).astimezone(local_zone).date().isoformat(),
        "format": "csv", "flags": "true",
    })


def timestamp_utc(value, zone):
    """Interpret a native unzoned local timestamp without guessing at DST folds."""
    local = datetime.strptime(value, "%Y-%m-%d %H:%M")
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError(f"Ambiguous or nonexistent local timestamp: {value} ({zone})")
    return candidates.pop()


def select_csv(payload, station, zone):
    """Select original CSV lines by UTC instant; never parse/rewrite moisture values."""
    lines = payload.splitlines(keepends=True)
    if not lines:
        raise ValueError("Empty AgriMet CSV")
    header = next(csv.reader([lines[0].decode("utf-8-sig")]))
    columns = [f"{station.lower()}_{p.lower()}" for p in PARAMETERS]
    if not header or header[0] != "DateTime" or len(header) != len(set(header)):
        raise ValueError("Invalid AgriMet CSV header")
    if not set(columns) <= set(header):
        raise ValueError("Missing requested soil-moisture columns")
    indices = [header.index(column) for column in columns]
    lower, upper = date_bounds()
    local_zone = ZoneInfo(zone)
    selected = [lines[0]]
    timestamps = []
    missing = dict.fromkeys(columns, 0)
    previous = None
    for line in lines[1:]:
        row = next(csv.reader([line.decode("utf-8")]))
        if len(row) != len(header):
            raise ValueError("Malformed AgriMet CSV row")
        instant = timestamp_utc(row[0], local_zone)
        if previous is not None and instant <= previous:
            raise ValueError("Duplicate or unordered AgriMet timestamps")
        previous = instant
        if lower <= instant < upper:
            if (instant - lower).total_seconds() % (INTERVAL_MINUTES * 60):
                raise ValueError("AgriMet timestamp is off the configured sampling grid")
            selected.append(line)
            timestamps.append(instant)
            for column, index in zip(columns, indices):
                # Count common source missing markers; preserve them verbatim.
                if row[index].strip().lower() in {"", "nan", "null", "m", "--", "-999", "-9999", "-999.00", "-9999.00"}:
                    missing[column] += 1
    if not timestamps:
        raise ValueError("No AgriMet rows in the requested UTC interval")
    expected = int((upper - lower).total_seconds() / (INTERVAL_MINUTES * 60))
    return b"".join(selected), {"rows": len(timestamps), "expected_rows": expected,
                                "missing_rows": expected - len(timestamps),
                                "first_utc": timestamps[0].isoformat(),
                                "last_utc": timestamps[-1].isoformat(), "missing_values": missing}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-only", action="store_true", help="Print requests without downloading")
    args = parser.parse_args(argv)
    stats = DownloadStats()
    try:
        date_bounds()
        for station, zone in STATIONS.items():
            address = request_url(station, zone)
            target = OUTPUT_DIR / f"{station}.{'-'.join(PARAMETERS)}.{START_DATE}_{END_DATE}.UTC-window.csv"
            print(f"{station}: {START_DATE}–{END_DATE} inclusive UTC; source timestamps {zone}; units percent")
            if args.list_only:
                print(f"{target.name}\n  {address}")
                continue

            def write(destination):
                response = io.BytesIO()
                copy_response(address, response, stats)
                selected, _ = select_csv(response.getvalue(), station, zone)
                destination.write(selected)

            def validate(path):
                original = path.read_bytes()
                selected, summary = select_csv(original, station, zone)
                if selected != original:
                    raise ValueError(f"Existing CSV contains rows outside the UTC interval: {path.name}")
                print(f"  Coverage: {summary}", flush=True)

            status = publish_file(target, write, validate)
            print(f"{status}: {target.name}", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, csv.Error, HTTPException) as error:
        print(f"Error: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted; rerun to continue. Completed files are retained.", file=sys.stderr)
    finally:
        if not args.list_only:
            stats.report()
    return 1


if __name__ == "__main__":
    sys.exit(main())
