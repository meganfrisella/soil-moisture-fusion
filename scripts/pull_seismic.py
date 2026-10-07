#!/usr/bin/env python3
"""Download native PNSN miniSEED waveforms and response-level StationXML.

Run from the repository root in the Pixi environment (ObsPy required):
    pixi run python scripts/pull_seismic.py --list-only
        List planned requests without network calls or saving files.
    pixi run python scripts/pull_seismic.py
        Download miniSEED and StationXML files to data/seismic/ without login.
    pixi run python -m unittest discover -s tests -p 'test_data_downloads.py'
        Run offline downloader tests.

Edit the constants below to change the stations, location, channels, inclusive
UTC observation dates, or destination. Waveform requests cover one station/day
through EarthScope's FDSN HTTPS API; the UW archive is hosted on AWS S3 (us-east-2).
Original files retain native counts, sample rates, gaps, and miniSEED quality
codes. StationXML retains coordinates, orientations, response units, and matching
instrument epochs. No response removal, filtering, resampling, dv/v calculation,
or uncertainty estimation is applied. All three source downloaders retain native
formats and use the same UTC date-boundary helper.

FDSN may return whole records extending slightly beyond a requested boundary;
these native records are retained and must be trimmed during later processing.

Only native miniSEED and StationXML files are saved, without converted exports.
Downloads are structurally validated before publication; valid existing files
are skipped, and invalid existing files are left untouched. The query API does
not supply archive checksums. Failed downloads are cleaned up; interrupted
transfers restart on the next run.

The terminal summary reports response-body bytes,
average transfer speed, download duration, and total run duration. Skipped files
are excluded from transfer totals; failed transfers are included.

Sources:
    https://service.earthscope.org/fdsnws/dataselect/1/
    https://service.earthscope.org/fdsnws/station/1/
    https://docs.earthscope.org/sponsored-open-data
"""

import argparse
from datetime import timedelta
from http.client import HTTPException
from pathlib import Path
import sys
import warnings
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.download_utils import DownloadStats, copy_response, publish_file, utc_date_bounds


STATIONS = (("UW", "OD2"), ("UW", "WOLL"))
LOCATION = ""  # FDSN encodes the blank location as "--".
CHANNELS = ("HHE", "HHN", "HHZ")
START_DATE = "2026-06-24"
END_DATE = "2026-09-16"  # Inclusive, UTC.
OUTPUT_DIR = ROOT / "data/seismic"
DATASELECT_URL = "https://service.earthscope.org/fdsnws/dataselect/1/query"
STATION_URL = "https://service.earthscope.org/fdsnws/station/1/query"


def date_bounds():
    return utc_date_bounds(START_DATE, END_DATE)


def request_url(base, network, station, start, end, **extra):
    """Avoid requesting the next interval's first sample (FDSN end is inclusive)."""
    params = dict(net=network, sta=station, loc=LOCATION or "--",
                  cha=",".join(CHANNELS), starttime=start.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                  endtime=(end - timedelta(microseconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                  nodata=404)  # FDSN's unzoned wire timestamps are UTC.
    return base + "?" + urlencode(params | extra)


def requests():
    lower, upper = date_bounds()
    for network, station in STATIONS:
        folder = OUTPUT_DIR / f"{network}.{station}"
        stem = f"{network}.{station}.{LOCATION or '--'}.{'-'.join(CHANNELS)}"
        yield (folder / f"{stem}.{START_DATE}_{END_DATE}.xml",
               request_url(STATION_URL, network, station, lower, upper,
                           level="response", format="xml"),
               "stationxml", network, station, lower, upper)
        day = lower
        while day < upper:
            end = day + timedelta(days=1)
            yield (folder / f"{stem}.{day.date()}.mseed",
                   request_url(DATASELECT_URL, network, station, day, end, format="miniseed"),
                   "miniseed", network, station, day, end)
            day = end


def validate_file(path, kind, network, station, start, end):
    """Parse native files with ObsPy; check identities and time overlap, not signal quality."""
    from obspy import UTCDateTime, read, read_inventory

    expected = {f"{network}.{station}.{LOCATION}.{channel}" for channel in CHANNELS}
    if kind == "stationxml":
        inventory = read_inventory(str(path), format="STATIONXML")
        selected = inventory.select(network=network, station=station, location=LOCATION,
                                    starttime=UTCDateTime(start),
                                    endtime=UTCDateTime(end - timedelta(microseconds=1)))
        actual = set()
        for net in selected:
            for sta in net:
                for channel in sta:
                    actual.add(f"{net.code}.{sta.code}.{channel.location_code}.{channel.code}")
                    if channel.response is None or not channel.response.response_stages:
                        raise ValueError(f"Missing instrument response: {path.name}")
        if actual != expected:
            raise ValueError(f"Unexpected/missing StationXML channels: {path.name}")
    else:
        # libmseed can warn and return the readable prefix of a truncated file.
        # Such a prefix must not be accepted as a completed native download.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                stream = read(str(path), format="MSEED", headonly=True)
            except Warning as error:
                raise ValueError(f"Invalid miniSEED: {path.name}: {error}") from error
        actual = {trace.id for trace in stream}
        if actual != expected:
            raise ValueError(f"Unexpected/missing waveform channels: {path.name}")
        for trace in stream:
            if (trace.stats.npts <= 0 or trace.stats.sampling_rate <= 0
                    or trace.stats.starttime >= UTCDateTime(end)
                    or trace.stats.endtime < UTCDateTime(start)):
                raise ValueError(f"Empty or out-of-window waveform: {path.name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-only", action="store_true", help="Print requests without downloading")
    args = parser.parse_args(argv)
    stats = DownloadStats()
    try:
        plan = list(requests())
        print(f"{START_DATE}–{END_DATE} inclusive UTC; {len(plan)} native files.")
        for target, address, kind, network, station, start, end in plan:
            if args.list_only:
                print(f"{target.name}\n  {address}")
                continue
            status = publish_file(target, lambda out: copy_response(address, out, stats),
                                  lambda p: validate_file(p, kind, network, station, start, end))
            print(f"{status}: {target.name}", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, HTTPException) as error:
        print(f"Error: {error}", file=sys.stderr)
    except ImportError:
        print("ObsPy is required; run with pixi run python scripts/pull_seismic.py", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted; rerun to continue. Completed files are retained.", file=sys.stderr)
    finally:
        if not args.list_only:
            stats.report()
    return 1


if __name__ == "__main__":
    sys.exit(main())
