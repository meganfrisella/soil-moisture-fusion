#!/usr/bin/env python3
"""Download original NISAR SME2 HDF5 products, including uncertainties and flags.

Run from the repository root in the Pixi environment (standard library only):
    pixi run python scripts/pull_nisar_sme2.py --list-only
        Query the public catalog and list matching acquisitions without saving files.
    pixi run python scripts/pull_nisar_sme2.py
        Download HDF5 files to data/nisar/ using the exported environment
        variables EARTHDATA_USER and EARTHDATA_PASS.
    pixi run python -m unittest discover -s tests -p 'test_data_downloads.py'
        Run offline downloader tests.

Edit the constants below to change the collection, track/frame pairs, inclusive
UTC acquisition dates; --output-dir sets the destination (default: data/nisar).
Downloads cover the entire frame on its
native EASE-Grid 2.0 (EPSG:6933); there is no spatial subset or resampling.
Original files retain combined and available individual-algorithm soil moisture
and uncertainties (m^3/m^3), integer flags, fill values, and embedded metadata.
No quality filtering or flag reinterpretation is applied. All three source
downloaders retain native formats and use the same UTC date-boundary helper.

Only the primary HDF5 product is saved, without sidecars or converted exports.
Downloads are checksum-verified before publication; identical existing files
are skipped, and mismatched existing files are left untouched. Failed downloads
are cleaned up; interrupted transfers restart on the next run. Credentials and
authentication cookies are held in memory only.

The terminal summary reports response-body bytes,
average transfer speed, download duration, and total run duration. Catalog traffic
and skipped files are excluded from transfer totals; failed transfers are included.

Sources:
    https://nisar-docs.asf.alaska.edu/data-format/
    https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html
    https://urs.earthdata.nasa.gov/documentation/for_users/data_access/python
"""

import argparse
from datetime import timedelta
from http.client import HTTPException
import json
from pathlib import Path
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from ingest.downloads import utc_date_bounds
from utils.download_stats import DownloadStats
from nisar.catalog import (CMR_URL, TIMEOUT_SECONDS, parse_granule as parse_catalog_granule,
                           earthdata_opener, download_granule)


COLLECTION = "NISAR_L3_SME2_PROVISIONAL_V1"
TRACK_FRAMES = ((143, 65),)
START_DATE = "2026-06-24"
END_DATE = "2026-09-16"  # Inclusive, UTC.
OUTPUT_DIR = ROOT / "data/nisar"
PAGE_SIZE = 100


def date_bounds():
    """Return inclusive-start/exclusive-end UTC bounds for whole calendar days."""
    return utc_date_bounds(START_DATE, END_DATE)


def fetch_json(address):
    """Query CMR without sending Earthdata credentials to the catalog."""
    with urlopen(Request(address, headers={"Accept": "application/json"}),
                 timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def parse_granule(item, track_frame):
    """Apply this legacy workflow's track/frame and UTC date selection."""
    granule = parse_catalog_granule(item, COLLECTION, *date_bounds())
    if (granule.track, granule.frame) != track_frame:
        raise ValueError("CMR returned an unexpected track/frame")
    return granule


def search_granules(get_json=fetch_json):
    """Discover every matching acquisition, checking pagination and metadata."""
    lower, upper = date_bounds()
    temporal = ",".join(d.isoformat().replace("+00:00", "Z")
                        for d in (lower, upper - timedelta(microseconds=1)))
    found = {}
    for track, frame in TRACK_FRAMES:
        seen = set()
        page = 1
        expected = None
        while True:
            params = {
                "short_name": COLLECTION,
                "temporal": temporal,
                "producer_granule_id[]": f"NISAR_L3_PR_SME2_*_{track:03d}_*_{frame:03d}_*",
                "options[producer_granule_id][pattern]": "true",
                "sort_key[]": "start_date",
                "page_size": PAGE_SIZE,
                "page_num": page,
            }
            result = get_json(CMR_URL + "?" + urlencode(params))
            if "errors" in result:
                raise ValueError("CMR rejected the catalog query")
            hits = int(result["hits"])
            if expected is not None and hits != expected:
                raise RuntimeError("CMR results changed during pagination; rerun the script")
            expected = hits
            items = result["items"]
            if hits == 0:
                break
            if not items:
                raise RuntimeError("Incomplete CMR pagination")
            for item in items:
                granule = parse_granule(item, (track, frame))
                if granule.name in seen:
                    raise RuntimeError("Repeated granule during CMR pagination")
                seen.add(granule.name)
                found[granule.name] = granule
            if len(seen) == hits:
                break
            if len(seen) > hits:
                raise RuntimeError("Inconsistent CMR result count")
            page += 1
    return sorted(found.values(), key=lambda g: (g.start, g.track, g.frame, g.name))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-only", action="store_true",
                        help="List catalog matches without authentication or file writes")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help="Directory for downloaded HDF5 products (default: data/nisar)")
    args = parser.parse_args(argv)
    stats = DownloadStats()
    try:
        granules = search_granules()
        if not granules:
            raise ValueError("No matching SME2 acquisitions were found")
        print(f"Found {len(granules)} {COLLECTION} acquisitions ({START_DATE}–{END_DATE} UTC).",
              flush=True)
        for granule in granules:
            print(f"  {granule.start.isoformat()}  track {granule.track:03d} / "
                  f"frame {granule.frame:03d}  {granule.name}", flush=True)
        if args.list_only:
            return 0
        opener = earthdata_opener()
        for index, granule in enumerate(granules, 1):
            print(f"[{index}/{len(granules)}] {granule.start.date()}: verifying/downloading",
                  flush=True)
            status = download_granule(granule, opener, args.output_dir, stats)
            print(f"  {status}: {granule.name}", flush=True)
        print(f"Original HDF5 products stored in {args.output_dir}", flush=True)
        return 0
    except HTTPError as error:
        # Do not log redirect URLs, which can contain temporary authentication tokens.
        print(f"HTTP {error.code}: catalog or download request failed. "
              "For 401/403, check Earthdata credentials and application authorization.", file=sys.stderr)
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            print("TLS certificate verification failed. Use the project environment: "
                  "pixi run python scripts/pull_nisar_sme2.py", file=sys.stderr)
        else:
            print("Network request failed; check connectivity and rerun the script.", file=sys.stderr)
    except (TimeoutError, ConnectionError, HTTPException):
        print("Network request failed; check connectivity and rerun the script.", file=sys.stderr)
    except (ValueError, KeyError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Download interrupted; rerun to continue with unverified acquisitions.", file=sys.stderr)
    finally:
        if not args.list_only:
            stats.report()
    return 1


if __name__ == "__main__":
    sys.exit(main())
