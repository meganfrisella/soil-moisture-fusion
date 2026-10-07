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
UTC acquisition dates, or destination. Downloads cover the entire frame on its
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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPBasicAuthHandler, HTTPCookieProcessor, HTTPPasswordMgrWithDefaultRealm,
    Request, build_opener, urlopen,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.download_utils import DownloadStats, copy_response, publish_file, utc_date_bounds


COLLECTION = "NISAR_L3_SME2_PROVISIONAL_V1"
TRACK_FRAMES = ((143, 65),)
START_DATE = "2026-06-24"
END_DATE = "2026-09-16"  # Inclusive, UTC.
OUTPUT_DIR = ROOT / "data/nisar"
CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
EARTHDATA_URL = "https://urs.earthdata.nasa.gov"
DOWNLOAD_HOST = "nisar.asf.earthdatacloud.nasa.gov"
PAGE_SIZE = 100
TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024
HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"


@dataclass(frozen=True)
class Granule:
    """Catalog identity and integrity information for one original acquisition."""

    name: str
    start: datetime
    track: int
    frame: int
    url: str
    checksum_algorithm: str
    checksum: str


def date_bounds():
    """Return inclusive-start/exclusive-end UTC bounds for whole calendar days."""
    return utc_date_bounds(START_DATE, END_DATE)


def fetch_json(address):
    """Query CMR without sending Earthdata credentials to the catalog."""
    with urlopen(Request(address, headers={"Accept": "application/json"}),
                 timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def parse_granule(item, track_frame):
    """Validate catalog filters and select the primary HDF5, excluding QA sidecars."""
    record = item["umm"]
    if record["CollectionReference"]["ShortName"] != COLLECTION:
        raise ValueError("CMR returned an unexpected collection")
    attributes = {a["Name"]: a["Values"] for a in record["AdditionalAttributes"]}
    pair = (int(attributes["TRACK_NUMBER"][0]), int(attributes["FRAME_NUMBER"][0]))
    if pair != track_frame:
        raise ValueError("CMR returned an unexpected track/frame")
    start = datetime.fromisoformat(
        record["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("CMR acquisition time is missing its timezone")
    start = start.astimezone(timezone.utc)
    lower, upper = date_bounds()
    if not lower <= start < upper:
        raise ValueError("CMR returned an acquisition outside the requested UTC dates")
    name = record["GranuleUR"]
    if not re.fullmatch(r"NISAR_[A-Za-z0-9_]+", name):
        raise ValueError("Unexpected granule filename")
    filename = name + ".h5"
    links = [u["URL"] for u in record["RelatedUrls"]
             if u["Type"] == "GET DATA"
             and urlparse(u["URL"]).scheme == "https"
             and urlparse(u["URL"]).hostname == DOWNLOAD_HOST
             and Path(urlparse(u["URL"]).path).name == filename]
    files = [f for f in record["DataGranule"]["ArchiveAndDistributionInformation"]
             if f["Name"] == filename]
    if len(links) != 1 or len(files) != 1:
        raise ValueError(f"Missing or ambiguous primary HDF5 product: {name}")
    checksum = files[0]["Checksum"]
    algorithm = checksum["Algorithm"].lower().replace("-", "")
    digest = hashlib.new(algorithm)
    if not re.fullmatch(r"[0-9a-fA-F]{" + str(digest.digest_size * 2) + r"}", checksum["Value"]):
        raise ValueError(f"Invalid archive checksum: {name}")
    return Granule(filename, start, *pair, links[0], algorithm, checksum["Value"].lower())


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


def earthdata_opener():
    """Use NASA's cookie-based login flow, with basic credentials scoped to URS."""
    username = os.environ.get("EARTHDATA_USER")
    password = os.environ.get("EARTHDATA_PASS")
    if not username or not password:
        raise ValueError("Export EARTHDATA_USER and EARTHDATA_PASS before running the script")
    passwords = HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, EARTHDATA_URL, username, password)
    return build_opener(HTTPBasicAuthHandler(passwords), HTTPCookieProcessor(CookieJar()))


def verify_file(path, granule):
    """Check HDF5 signature and the archive checksum, streaming bounded chunks."""
    digest = hashlib.new(granule.checksum_algorithm)
    with path.open("rb") as source:
        if source.read(len(HDF5_SIGNATURE)) != HDF5_SIGNATURE:
            raise ValueError(f"Not an HDF5 product: {path.name}")
        source.seek(0)
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != granule.checksum:
        raise ValueError(f"Archive checksum mismatch: {path.name}")


def download_granule(granule, opener, output_dir, stats=None):
    """Publish only a complete, verified original; never replace an existing file."""
    status = publish_file(output_dir / granule.name,
                          lambda out: copy_response(granule.url, out, stats, opener),
                          lambda path: verify_file(path, granule))
    return "verified existing" if status == "validated existing" else status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-only", action="store_true",
                        help="List catalog matches without authentication or file writes")
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
            status = download_granule(granule, opener, OUTPUT_DIR, stats)
            print(f"  {status}: {granule.name}", flush=True)
        print(f"Original HDF5 products stored in {OUTPUT_DIR}", flush=True)
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
