"""Write/check R32 local Zarr time series with explicit task completion and resume.

Run synthetic storage checks after ``pixi install --locked`` from repo root:
    pixi run python -m unittest discover -s tests -p 'test_regridding_storage.py'

Use ``with RunStore(output_dir, identity, resume=False) as run``. Identity is a
JSON record of frozen scientific configuration/input identities/environment;
callers must first validate actual inputs with regridding.inputs.audit_inputs.
``initialize(method, template, stage_identity=..., spatial_chunks=(ny,nx),
compressors=...)`` accepts schema.store_template output, stage-specific code/
configuration hashes, and REQUIRED chunk/codec settings (approved R33 values
are supplied by regridding.run_plan.storage_options).
It lazily allocates time-series fields: float64 m³/m³, native 200 m EPSG:6933
or target 90 m EPSG:5070. Explicit None disables compression for test fixtures.
No full time-series array is allocated. All stores use Zarr v3, unconsolidated.

``write_task(method, acquisition, candidate, values, provenance=...)`` writes
one entire candidate/acquisition slab, validates/readbacks it, inventories
chunks, and commits status last. Candidate is a PMI/TSR/DSG label. Values are
the exact required schema.task_fields dictionary of 2D arrays. Provenance is
a nonempty JSON record containing command/lineage and any all-missing reason;
native source mapping resides in its arrays. Method-stage callers must also
enforce the unresolved R10–R22 scientific contracts and record/link their
required weights/fit provenance before invoking completion. This module does
not implement operators or a covariance/fit metadata schema.

``read_task`` verifies completed-task checksums before returning a loaded xarray
Dataset for that slab. ``resume=True`` verifies every completed task in opened
stores; scientific identity, schema, stage code, metadata or chunk mismatch
fails rather than repairing completed work. Unfinished tasks restart by writing
ALL their fields; status 0=pending, 1=writing, 2=complete, 3=failed. Valid zeros
are never decoded as nodata. Raw xarray/Zarr reads bypass completion checks.

One local writer/reader session holds a nonblocking POSIX advisory lock, released
on process exit. A crashed initializer can leave an unreferenced .initializing
directory or an unregistered final directory; neither is reused automatically.
An unregistered final directory requires a new output root. No automatic deletion.
Atomic JSON replacement protects manifest/task records; a prepared record whose
status is complete can be verified and finalized after a crash. This provides
process-interruption recovery, not a guarantee against disk/power failures.
No CLI, network, field runs, figures, calibration or remote publishing here.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from numbers import Integral

import numpy as np
import xarray as xr
import zarr

from nisar.quality import ALGORITHMS
from regridding.inputs import sha256_file
from regridding.schema import SCHEMA_VERSION, canonical_json, task_fields, validate_task


def _write_json(path, value):
    """Replace one local JSON record atomically, with strict JSON numeric values."""
    data = canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".record-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _dataset_hash(ds):
    """Fingerprint the supplied static template without materializing dynamic fields."""
    digest = hashlib.sha256(canonical_json(ds.attrs).encode())
    for name in sorted(ds.variables):
        v = ds[name]
        digest.update(canonical_json([name, list(v.dims), v.attrs, str(v.dtype), list(v.shape)]).encode())
        data = np.asarray(v.values)
        if data.dtype.kind in "OUST":
            digest.update(canonical_json(data.tolist()).encode())
        else:
            digest.update(np.ascontiguousarray(data).tobytes())
    return digest.hexdigest()


def _inventory(root, paths):
    """Hash exact present files; absent/all-fill chunks remain absent in the inventory."""
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(paths) if p.is_file()}


class RunStore:
    """A locked local run directory with immutable completed task contents."""

    def __init__(self, output_dir, identity, *, resume=False):
        if "://" in str(output_dir):
            raise ValueError("Only a local output directory is supported")
        if not isinstance(identity, dict) or not identity:
            raise ValueError("A nonempty scientific/input/environment identity is required")
        self.root = Path(output_dir)
        self.identity = json.loads(canonical_json(identity))
        self.resume = resume
        self.lock = None
        self.stores = {}

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / ".writer.lock").open("a+")
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.root / "run_manifest.json"
            if path.exists():
                if not self.resume:
                    raise FileExistsError("Run exists; explicit resume is required")
                self.manifest = json.loads(path.read_text())
                if self.manifest["identity"] != self.identity or self.manifest["schema"] != SCHEMA_VERSION:
                    raise ValueError("Run identity/schema mismatch; use a new output directory")
                # Verify completed stores before accepting any new work.
                for method, record in self.manifest["stores"].items():
                    self._verify_store(method, record)
            else:
                if any(p.name != ".writer.lock" for p in self.root.iterdir()):
                    raise FileExistsError("Unrecognized nonempty output directory")
                self.manifest = {"schema": SCHEMA_VERSION, "identity": self.identity, "stores": {}}
                self._save()
            return self
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def __exit__(self, *error):
        self.stores.clear()
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def _save(self):
        _write_json(self.root / "run_manifest.json", self.manifest)

    def _require_open(self):
        if self.lock is None:
            raise RuntimeError("Use RunStore inside its context manager")

    def _static_inventory(self, method):
        path = self.root / f"{method}.zarr"
        dynamic = set(task_fields(method)) | {"completion_status"}
        return _inventory(path, (p for p in path.rglob("*")
                                if p.name == "zarr.json" or p.relative_to(path).parts[0] not in dynamic))

    def _task_inventory(self, method, a, c):
        path = self.root / f"{method}.zarr"
        files = []
        for name in task_fields(method):
            # Explicit default chunk-key encoding and leading chunks (1,1).
            files.extend((path / name / "c" / str(a) / str(c)).rglob("*"))
        return _inventory(path, files)

    def _verify_task(self, method, a, c, entry):
        path = self.root / "records" / method / f"{a}-{c}.json"
        if not path.is_file() or sha256_file(path) != entry["record_sha256"]:
            raise ValueError("Missing/corrupt task completion record")
        record = json.loads(path.read_text())
        if record["chunks"] != self._task_inventory(method, a, c):
            raise ValueError("Completed task chunk checksum mismatch")
        return record

    def _verify_store(self, method, record):
        if self._static_inventory(method) != record["static_files"]:
            raise ValueError("Store metadata/static checksum mismatch")
        group = zarr.open_group(self.root / f"{method}.zarr", mode="r", use_consolidated=False)
        status = group["completion_status"][:]
        if not np.isin(status, [0, 1, 2, 3]).all():
            raise ValueError("Invalid completion status")
        for a, c in np.ndindex(status.shape):
            entry = record["tasks"].get(f"{a}-{c}")
            if status[a, c] == 2 or (entry and entry["state"] == "complete"):
                if status[a, c] != 2 or not entry:
                    raise ValueError("Completion flag/record disagreement")
                self._verify_task(method, a, c, entry)
                if entry["state"] == "prepared":
                    entry["state"] = "complete"
                    self._save()

    def initialize(self, method, template, *, stage_identity, spatial_chunks, compressors):
        """Create or validate one stage; chunk/codec settings have no production default."""
        self._require_open()
        fields = task_fields(method)
        if template.attrs.get("method") != method or template.attrs.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Template method/schema mismatch")
        if not stage_identity or not isinstance(stage_identity, dict):
            raise ValueError("Stage-specific code/configuration identity is required")
        if len(spatial_chunks) != 2 or any(type(n) is not int or n < 1 for n in spatial_chunks):
            raise ValueError("Explicit positive spatial chunk sizes required")
        codec_config = [] if compressors is None else [codec.to_dict() for codec in compressors]
        identity = json.loads(canonical_json({"stage": stage_identity, "template": _dataset_hash(template),
                                             "spatial_chunks": list(spatial_chunks), "compressors": codec_config}))
        existing = self.manifest["stores"].get(method)
        if existing:
            if existing["identity"] != identity:
                raise ValueError("Stage/template/chunk identity mismatch; use a new output directory")
            self._verify_store(method, existing)
        else:
            target = self.root / f"{method}.zarr"
            if target.exists():
                raise FileExistsError("Unregistered store exists; refusing overwrite")
            temporary = Path(tempfile.mkdtemp(prefix=f".{method}.initializing-", dir=self.root))
            # Directory exists but the Zarr group does not: mode w- still rejects groups.
            encoding = {}
            for name, v in template.variables.items():
                chunks = tuple(min(template.sizes[d], spatial_chunks[0 if d == "y" else 1])
                               if d in ("x", "y") else max(1, template.sizes[d]) for d in v.dims)
                encoding[name] = {"chunks": chunks, "compressors": compressors, "_FillValue": None}
            template.to_zarr(temporary, mode="w-", zarr_format=3, consolidated=False, encoding=encoding)
            group = zarr.open_group(temporary, mode="r+", use_consolidated=False)
            dims = ("acquisition", "candidate", "y", "x")
            shape = tuple(template.sizes[d] for d in dims)
            chunks = (1, 1, *spatial_chunks)
            for name, (dtype, fill) in fields.items():
                attrs = {"grid_mapping": "spatial_ref"}
                if dtype == "float64":
                    attrs.update(units="m3 m-3", uncertainty_scope=template.attrs["uncertainty_scope"])
                group.create_array(name, shape=shape, dtype=dtype, chunks=chunks, fill_value=fill,
                                   dimension_names=dims, attributes=attrs, compressors=compressors,
                                   chunk_key_encoding={"name": "default", "configuration": {"separator": "/"}})
            group.create_array("completion_status", shape=shape[:2], dtype="uint8", chunks=(1, 1),
                               fill_value=0, dimension_names=dims[:2], compressors=None,
                               attributes={"flag_values": [0, 1, 2, 3],
                                           "flag_meanings": "pending writing complete failed"})
            os.rename(temporary, target)
            self.manifest["stores"][method] = {"identity": identity, "tasks": {},
                                                "static_files": self._static_inventory(method)}
            self._save()
        self.stores[method] = (zarr.open_group(self.root / f"{method}.zarr", mode="r+", use_consolidated=False), template)

    def _task(self, method, acquisition, candidate):
        self._require_open()
        if method not in self.stores:
            raise ValueError("Initialize/validate this method first")
        group, template = self.stores[method]
        if (isinstance(acquisition, (bool, np.bool_)) or not isinstance(acquisition, Integral)
                or not 0 <= acquisition < template.sizes["acquisition"] or candidate not in ALGORITHMS):
            raise ValueError("Invalid acquisition/candidate")
        return group, template, int(acquisition), ALGORITHMS.index(candidate)

    def write_task(self, method, acquisition, candidate, values, *, provenance):
        """Replace only an unfinished slab; verify values/chunks before committing it."""
        group, template, a, c = self._task(method, acquisition, candidate)
        stage = self.manifest["stores"][method]
        key = f"{a}-{c}"
        if int(group["completion_status"][a, c]) == 2 or stage["tasks"].get(key, {}).get("state") == "complete":
            raise FileExistsError("Completed tasks cannot be overwritten")
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError("Explicit task lineage/command provenance is required")
        canonical_json(provenance)
        checked = validate_task(method, values, template, a)
        counts = {name: int(checked[name].sum()) for name in ("moisture_valid", "uncertainty_valid")}
        if not counts["moisture_valid"] and not provenance.get("all_missing_reason"):
            raise ValueError("An all-missing result needs an explicit reason")
        group["completion_status"][a, c] = 1
        try:
            # All arrays/regions are rewritten, including fill-only regions left by a crash.
            for name, array in checked.items():
                group[name][a, c, :, :] = array
                if not np.array_equal(group[name][a, c, :, :], array, equal_nan=True):
                    raise ValueError(f"Readback mismatch for {name}")
            record = {"provenance": provenance, "counts": counts,
                      "chunks": self._task_inventory(method, a, c)}
            path = self.root / "records" / method / f"{key}.json"
            _write_json(path, record)
            entry = {"state": "prepared", "record_sha256": sha256_file(path)}
            stage["tasks"][key] = entry
            self._save()
            self._verify_task(method, a, c, entry)
            group["completion_status"][a, c] = 2
            entry["state"] = "complete"
            self._save()
        except Exception as error:
            # A finalized task must not be downgraded if the final manifest write failed.
            if int(group["completion_status"][a, c]) != 2:
                group["completion_status"][a, c] = 3
                stage.setdefault("failures", {}).setdefault(key, []).append(
                    {"error_type": type(error).__name__, "message": str(error),
                     "provenance": provenance, "counts": counts})
                self._save()
            raise

    def read_task(self, method, acquisition, candidate):
        """Return one verified completed slab; explicit decoding preserves mask zeros."""
        group, _, a, c = self._task(method, acquisition, candidate)
        if int(group["completion_status"][a, c]) != 2:
            raise ValueError("Requested task is incomplete")
        self._verify_store(method, self.manifest["stores"][method])
        with xr.open_zarr(self.root / f"{method}.zarr", chunks=None, consolidated=False,
                          use_zarr_fill_value_as_mask=False) as ds:
            return ds.isel(acquisition=a, candidate=c).load()
