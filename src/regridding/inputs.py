"""Audit frozen SME2 inputs under R01 without network access or file writes.

After ``pixi install``, run from the repository root:
    PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.inputs import audit_inputs; print(json.dumps(audit_inputs("outputs/eastern_washington/sme2_statistics.json", "data/nisar", "9db85b322d52e7e10000f607f41b08a16d609603044d8989f2666890db6c97b6"), indent=2))'

Arguments are the saved selection JSON, existing HDF5 cache directory, and the
approved selection file's SHA-256. All files must already exist; no credentials
are required. Fail immediately on a changed selection, missing/corrupt file, or
unexpected product version. Return a JSON-compatible audit record to the caller;
artifact publication and a CLI await R32/R33. This module has no CLI entry point.

Native support is nominally 200 m, EPSG:6933; acquisition times are UTC and
moisture/reported uncertainty units are m³/m³. This integrity audit does not read
moisture arrays, recompute coverage, interpret uncertainty, or build a target grid.
"""

import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
from types import SimpleNamespace

import h5py
import numpy as np
import pyproj
import shapely

from nisar.catalog import verify_file
from nisar.observations import native


ROOT = Path(__file__).resolve().parents[2]
VERSION_FIELDS = {
    "science/LSAR/identification/productVersion": "1.0.11",
    "science/LSAR/identification/productSpecificationVersion": "1.0.2",
    "science/LSAR/identification/compositeReleaseId": "P05023",
    "science/LSAR/SME2/metadata/processingInformation/algorithms/softwareVersion": "0.4.2",
    "science/LSAR/SME2/metadata/sourceData/productVersion": "1.0.11",
    "science/LSAR/SME2/metadata/sourceData/processingInformation/algorithms/softwareVersion": "0.25.16",
}


def sha256_file(path):
    """Fingerprint a local file without loading its entire contents into memory."""
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def audit_inputs(selection_path, cache_dir, expected_selection_sha256):
    """Verify every frozen original and return provenance, raising on first failure.

    The caller supplies the approved selection SHA-256, not a hash recomputed
    from a potentially changed selection. Saved coverage/frame counts are labeled
    as such; only file count, checksums, bytes, and version metadata are audited.
    No file is skipped, repaired, overwritten, or downloaded.
    """
    selection_path, cache_dir = Path(selection_path), Path(cache_dir)
    raw = selection_path.read_bytes()
    selection_sha256 = hashlib.sha256(raw).hexdigest()
    if selection_sha256 != expected_selection_sha256:
        raise ValueError("Frozen selection SHA-256 mismatch; explicit dataset revision required")
    selection = json.loads(raw)
    names = selection["selected_granule_names"]
    checksums = selection["provenance"]["source_checksums"]
    if (not names or len(names) != len(set(names))
            or len(names) != selection["selected_granules"]
            or set(names) != set(checksums)):
        raise ValueError("Selection names/count/checksums disagree")
    for name in names:
        if not re.fullmatch(r"NISAR_[A-Za-z0-9_]+\.h5", name):
            raise ValueError(f"Invalid original filename: {name}")
    files = []
    for name in names:
        path = cache_dir / name
        checksum = checksums[name]
        verify_file(path, SimpleNamespace(
            checksum_algorithm=checksum["algorithm"], checksum=checksum["value"]))
        with h5py.File(path, "r") as product:
            versions = {key: native(product[key][()]) for key in VERSION_FIELDS}
        if versions != VERSION_FIELDS:
            raise ValueError(f"Unexpected product versions in {name}: {versions}")
        files.append({"name": name, "bytes": path.stat().st_size,
                      "archive_checksum": checksum, "versions": versions})
    code_paths = [
        "src/regridding/__init__.py", "src/regridding/inputs.py",
        "src/nisar/__init__.py", "src/nisar/catalog.py", "src/nisar/observations.py",
        "src/nisar/geometry.py", "src/nisar/quality.py",
        "src/ingest/__init__.py", "src/ingest/downloads.py",
        "pixi.toml", "pixi.lock",
    ]
    return {
        "audit": "R01 frozen-local-inputs-stop-on-failure",
        "selection_path": str(selection_path), "selection_sha256": selection_sha256,
        "cache_dir": str(cache_dir),
        "verified_granules": len(files), "excluded_granules": 0,
        "verified_bytes": sum(item["bytes"] for item in files),
        "saved_selection": {key: selection[key] for key in (
            "bounds_wgs84", "selected_frames", "distinct_utc_dates",
            "acquisition_start_utc", "acquisition_end_utc")},
        "versions": VERSION_FIELDS,
        "files": files,
        "code": {
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "git_status": subprocess.check_output(
                ["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
            "sha256": {name: sha256_file(ROOT / name) for name in code_paths},
        },
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "h5py": h5py.__version__, "hdf5": h5py.version.hdf5_version,
            "numpy": np.__version__, "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str, "shapely": shapely.__version__,
        },
        "limitations": [
            "No coverage, grouping, spatial support, or physical field validation performed.",
            "Nominal native support: 200 m EPSG:6933; moisture/uncertainty: m³/m³.",
            "Saved UTC endpoints describe acquisition starts, not full acquisition spans.",
            "No uncertainty components quantified; A01/A02 remain untested modeling assumptions.",
            "Recorded HDF5 specification version does not establish correspondence to Rev C.",
            "Execution-manifest publication remains pending R32/R33.",
        ],
    }
