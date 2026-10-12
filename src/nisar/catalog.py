"""Shared SME2 catalog identity, Earthdata access and original-file integrity.

Acquisition times are UTC. Catalog footprints select possible overlap; numeric
coverage is checked later from native arrays. Transfers are atomic and verified
against archive checksums. Earthdata credentials are read only for missing files.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
from http.cookiejar import CookieJar
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import (HTTPBasicAuthHandler, HTTPCookieProcessor,
                            HTTPPasswordMgrWithDefaultRealm, build_opener)

from ingest.downloads import copy_response, publish_file

COLLECTION = "NISAR_L3_SME2_PROVISIONAL_*"
CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
EARTHDATA_URL = "https://urs.earthdata.nasa.gov"
DOWNLOAD_HOST = "nisar.asf.earthdatacloud.nasa.gov"
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
    catalog_item: dict = field(default_factory=dict, repr=False, compare=False)


def parse_granule(item, collection="NISAR_L3_SME2_PROVISIONAL_*", lower=None, upper=None):
    """Validate catalog filters and select the primary HDF5, excluding QA sidecars."""
    record = item["umm"]
    if not fnmatchcase(record["CollectionReference"]["ShortName"], collection):
        raise ValueError("CMR returned an unexpected collection")
    attributes = {a["Name"]: a["Values"] for a in record["AdditionalAttributes"]}
    pair = (int(attributes["TRACK_NUMBER"][0]), int(attributes["FRAME_NUMBER"][0]))
    start = datetime.fromisoformat(
        record["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("CMR acquisition time is missing its timezone")
    start = start.astimezone(timezone.utc)
    if (lower is not None and start < lower) or (upper is not None and start >= upper):
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
    return Granule(filename, start, *pair, links[0], algorithm, checksum["Value"].lower(), item)


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
