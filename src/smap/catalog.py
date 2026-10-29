"""Freeze and download SMAP SPL2SMP_E v006 originals; Earthdata credentials required only for missing files."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ingest.downloads import copy_response, publish_file, utc_date_bounds
from ingest.download_stats import DownloadStats
from nisar.catalog import CMR_URL, TIMEOUT_SECONDS, earthdata_opener
from smap.l2e import COLLECTION_ID, SHORT_NAME, VERSION, Granule, parse_granule, verify_file

BOX = dict(south=46.057370, west=-121.241744, north=48.076483, east=-117.072163)
START_DATE = "2026-06-17"
END_DATE = "2026-10-10"  # Inclusive, UTC.
PAGE_SIZE = 2000
MANIFEST = "smap_l2e_manifest.json"


def query_url():
    """CMR granule query for the box and whole UTC study days."""
    lower, upper = utc_date_bounds(START_DATE, END_DATE)
    temporal = ",".join(d.isoformat().replace("+00:00", "Z")
                        for d in (lower, upper - timedelta(microseconds=1)))
    params = {"collection_concept_id": COLLECTION_ID, "temporal": temporal,
              "bounding_box": f"{BOX['west']},{BOX['south']},{BOX['east']},{BOX['north']}",
              "sort_key[]": "start_date", "page_size": PAGE_SIZE}
    return CMR_URL + "?" + urlencode(params)


def fetch(address):
    """Query CMR without credentials; return parsed JSON and the raw-body SHA-256."""
    with urlopen(Request(address, headers={"Accept": "application/json"}),
                 timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
    return json.loads(body), hashlib.sha256(body).hexdigest()


def search(get=fetch):
    """Return the parsed granules and a catalog record; one page must hold every hit."""
    address = query_url()
    result, digest = get(address)
    if "errors" in result:
        raise ValueError("CMR rejected the catalog query")
    granules = [parse_granule(item) for item in result["items"]]
    if int(result["hits"]) != len(granules):
        raise RuntimeError("CMR returned more hits than one page; raise PAGE_SIZE")
    names = [g.name for g in granules]
    if len(set(names)) != len(names):
        raise RuntimeError("Repeated granule in CMR response")
    if len({(g.orbit, g.direction) for g in granules}) != len(granules):
        raise RuntimeError("More than one file for a half orbit; review reprocessing counters")
    granules.sort(key=lambda g: (g.start, g.name))
    catalog = {"query_url": address, "hits": len(granules), "response_sha256": digest,
               "accessed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return granules, catalog


def write_manifest(path, granules, catalog):
    """Freeze the selection; refuse to replace a manifest with a different granule set."""
    record = {"product": {"short_name": SHORT_NAME, "version": VERSION,
                          "collection_concept_id": COLLECTION_ID, "provider": "NSIDC DAAC"},
              "box_wgs84": BOX, "utc_dates": [START_DATE, END_DATE], "catalog": catalog,
              "granules": [g.__dict__ for g in granules]}
    if path.exists():
        previous = json.loads(path.read_text())
        if previous["granules"] != record["granules"]:
            raise RuntimeError(f"{path} records a different selection; use a new --output-dir")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1) + "\n")
    return "written"


def download(cache_dir, output_dir, manifest_path=None):
    """Reuse a supplied frozen selection, verify cached originals and fetch only missing files."""
    cache_dir, output_dir = Path(cache_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path is not None:
        raw = Path(manifest_path).read_bytes()
        manifest = json.loads(raw)
        if (manifest['product']['short_name'], manifest['product']['version'],
                manifest['product']['collection_concept_id']) != (SHORT_NAME, VERSION, COLLECTION_ID):
            raise ValueError('Unexpected SMAP product selection')
        if manifest.get('box_wgs84') != BOX or manifest.get('utc_dates') != [START_DATE, END_DATE]:
            raise ValueError('SMAP selection has a different study box or UTC window')
        granules = [Granule(**item) for item in manifest['granules']]
        if not granules or any(Path(g.name).name != g.name for g in granules):
            raise ValueError('Empty selection or unsafe granule path')
        if len({g.name for g in granules}) != len(granules):
            raise ValueError('Duplicate granule')
        target = output_dir / MANIFEST
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Different frozen selection already exists')
        if not target.exists():
            target.write_bytes(raw)
    else:
        granules, catalog = search()
        write_manifest(output_dir / MANIFEST, granules, catalog)
    stats = DownloadStats()
    opener = None
    for g in granules:
        if not (cache_dir / g.name).exists() and opener is None:
            opener = earthdata_opener()
        publish_file(cache_dir / g.name,
                     lambda out, g=g: copy_response(g.url, out, stats, opener),
                     lambda path, g=g: verify_file(path, g.md5, g.size_bytes))
    stats.report()
