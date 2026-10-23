"""Inspect and cache the two approved R02 PROJ coordinate-correction resources.

After ``pixi install``, inspect its upstream checksum from the repository root:
    PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.resources import inspect_grid_catalog; print(json.dumps(inspect_grid_catalog(), indent=2))'

The inspection requires system curl and reads the official PROJ CDN catalog over HTTPS without credentials
or local output. Pass a grid filename to inspect_grid_catalog to select its entry;
the default remains the Washington grid. These static datum resources are not soil-moisture data and have
no acquisition time or moisture uncertainty interpretation. Its angular shifts
support the NAD83/WGS84 coordinate operations selected by the PROJ database.
"""

import hashlib
import json
from pathlib import Path
import subprocess
from time import perf_counter

from pyproj import datadir, network

from ingest.downloads import publish_file
from regridding.inputs import sha256_file


GRID_NAME = "us_noaa_WO.tif"
CATALOG_URL = "https://cdn.proj.org/files.geojson"
CURL_ARGS = ("curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "120")
# Official files.geojson entries inspected 2026-10-16 UTC; freeze original bytes.
GRID_SPECS = {
    GRID_NAME: (10706, "e6380daed501df8cfbb38149accb8b731e7b00e21368b2a8f6f696ac4eeb5f06"),
    "us_noaa_wmhpgn.tif": (11640, "2aa20b5f626de83639c1be455760b4c274bd4e6c53b35c667de7b244fe77fffd"),
}


def inspect_grid_catalog(grid_name=GRID_NAME):
    """Read one named catalog entry, rejecting missing or duplicate names."""
    raw = subprocess.check_output([*CURL_ARGS, CATALOG_URL])
    catalog = json.loads(raw)
    entries = [item for item in catalog["features"]
               if item["properties"]["name"] == grid_name]
    if len(entries) != 1:
        raise ValueError(f"Expected exactly one PROJ catalog entry for {grid_name}")
    return {"catalog_url": CATALOG_URL, "catalog_bytes": len(raw),
            "catalog_sha256": hashlib.sha256(raw).hexdigest(), "entry": entries[0]}


def verify_grid(path, grid_name=GRID_NAME):
    """Reject missing, truncated, or changed grid bytes against the pinned CDN identity."""
    path = Path(path)
    size, checksum = GRID_SPECS[grid_name]
    if path.stat().st_size != size or sha256_file(path) != checksum:
        raise ValueError(f"Pinned PROJ grid checksum/size mismatch: {path}")


def ensure_grid(cache_dir, grid_name=GRID_NAME):
    """Download only a missing grid, verify before publication, and never overwrite."""
    size, checksum = GRID_SPECS[grid_name]
    url = f"https://cdn.proj.org/{grid_name}"
    target = Path(cache_dir) / grid_name
    started = perf_counter()
    status = publish_file(
        target,
        lambda output: subprocess.run([*CURL_ARGS, url], stdout=output, check=True),
        lambda path: verify_grid(path, grid_name))
    elapsed = perf_counter() - started
    downloaded = size if status == "downloaded" else 0
    return {"path": str(target), "status": status, "url": url,
            "sha256": checksum, "file_bytes": size,
            "downloaded_bytes": downloaded, "elapsed_seconds": elapsed,
            "download_bytes_per_elapsed_second": downloaded / elapsed if downloaded else None}


def configure_local_grid(cache_dir):
    """Verify both approved grids, add the explicit search path, and keep PROJ offline."""
    directory = Path(cache_dir).resolve()
    for name in GRID_SPECS:
        verify_grid(directory / name, name)
    network.set_network_enabled(False)
    datadir.append_data_dir(str(directory))
    return directory
