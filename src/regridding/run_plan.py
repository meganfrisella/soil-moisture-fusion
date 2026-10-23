"""Approved R33 serial settings and a console-only, reproducible execution plan.

After ``pixi install --locked``, run from repository root:
    pixi run python scripts/regrid_eastern_washington.py --stage native --plan-only --output-dir outputs/eastern_washington/regridding

``configure_threads()`` must run in the CLI before importing numerical libraries.
``storage_options()`` supplies RunStore.initialize's approved spatial chunks and
compressors; ``serial_io()`` scopes Zarr's I/O/thread concurrency to one. Native
support is 200 m EPSG:6933; target is 90 m EPSG:5070; fields use m³/m³ and UTC.
These settings do not bound total RAM for a full native draw.

``describe_plan(stage, selection, cache_dir, proj_cache_dir, output_dir, resume)``
returns strict JSON-compatible configuration/provenance, not an input audit or
resource estimate. It hashes repository code/environment files but does not read
scientific inputs or create outputs. Actual stage-specific memory/disk preflight,
field execution and completion gates are implemented in native_stage for the
native reference, and sequentially after the relevant later method decisions.
"""

from contextlib import contextmanager
from importlib.metadata import version
import os
from pathlib import Path
import platform


SPATIAL_CHUNKS = (1024, 1024)
THREAD_VARIABLES = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS",
)
ZARR_SERIAL = {"async.concurrency": 1, "threading.max_workers": 1}
SELECTION_SHA256 = "9db85b322d52e7e10000f607f41b08a16d609603044d8989f2666890db6c97b6"


def configure_threads():
    """Set startup thread controls explicitly, overriding inherited parallel settings."""
    for name in THREAD_VARIABLES:
        os.environ[name] = "1"


def storage_options():
    """Return the approved lossless codec and spatial chunks for RunStore.initialize."""
    from zarr.codecs import ZstdCodec
    return {"spatial_chunks": SPATIAL_CHUNKS,
            "compressors": [ZstdCodec(level=3, checksum=True)]}


@contextmanager
def serial_io():
    """Apply Zarr concurrency controls for an entire CLI session, restoring on exit."""
    import zarr
    with zarr.config.set(ZARR_SERIAL):
        yield


def describe_plan(stage, selection, cache_dir, proj_cache_dir, output_dir, resume):
    """Describe approved configuration with code/package identities and explicit gaps."""
    from nisar.quality import ALGORITHMS
    from regridding.inputs import ROOT, sha256_file
    from regridding.support import METHODS

    stages = ("native", *METHODS)
    if stage not in stages:
        raise ValueError(f"Stage must be one of {stages}")
    paths = dict(selection=selection, cache_dir=cache_dir, proj_cache_dir=proj_cache_dir,
                 output_dir=output_dir)
    if any("://" in str(value) for value in paths.values()):
        raise ValueError("This experiment requires local paths")
    options = storage_options()
    code = [ROOT / "scripts/regrid_eastern_washington.py", ROOT / "pixi.toml", ROOT / "pixi.lock"]
    for directory in ("src/regridding", "src/nisar", "src/ingest"):
        code.extend(sorted((ROOT / directory).glob("*.py")))
    return {
        "status": "plan-only; inputs not audited; no field execution",
        "requested_stage": stage, "stage_order": list(stages),
        "paths": {name: str(Path(value)) for name, value in paths.items()}, "resume": resume,
        "frozen_selection_sha256": SELECTION_SHA256,
        "matrix": {"acquisitions": "all 69 frozen combined acquisitions", "candidates": list(ALGORITHMS),
                   "covariance": "uncorrelated-native-error baseline only"},
        "support": {"native": "200 m EPSG:6933", "target": "90 m EPSG:5070",
                    "field_units": "m3 m-3", "time": "UTC"},
        "storage": {"format": 3, "consolidated": False, "sharded": False,
                    "chunks": [1, 1, *options["spatial_chunks"]],
                    "compressors": [codec.to_dict() for codec in options["compressors"]]},
        "execution": {"compute_tasks": 1, "writers": 1, "zarr": ZARR_SERIAL.copy(),
                      "numerical_threads": {name: os.environ.get(name) for name in THREAD_VARIABLES},
                      "geometry_batch_shape": list(SPATIAL_CHUNKS),
                      "failure_policy": "stop; explicit resume; never skip or overwrite completed tasks"},
        "remaining_gates": ["Native stage and R35 figure inspection before nearest",
                            "stage-specific memory/storage estimates and disk feasibility before each run",
                            "all tasks/provenance complete and figure inspected before next method"],
        "code_sha256": {str(path.relative_to(ROOT)): sha256_file(path) for path in code},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "packages": {name: version(name) for name in
                                     ("numpy", "xarray", "zarr", "numcodecs", "h5py", "pyproj", "shapely")}},
    }
