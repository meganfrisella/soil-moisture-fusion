"""Bind comparison inputs to complete local Zarr stores and reproducible commands."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import xarray as xr

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path):
    """Stream a file's SHA-256 digest."""
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run_record(command):
    """Record the invocation, relevant source files, revision and environment lock."""
    def git(*args):
        result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    paths = [p for package in ('comparison', 'smap', 'nisar', 'ingest', 'regridding')
             for p in (ROOT / 'src' / package).rglob('*.py')]
    paths += [ROOT / 'scripts' / name for name in ('prepare_nisar_smap.py', 'compare_soil_moisture.py')]
    return dict(command=list(command), run_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                git_revision=git('rev-parse', 'HEAD'), git_worktree_dirty=bool(git('status', '--porcelain')),
                python=sys.version.split()[0], pixi_lock_sha256=sha256_file(ROOT / 'pixi.lock'),
                code_sha256={str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(paths)})


def store_files(path):
    """Fingerprint every metadata and chunk file; refuse missing, empty or linked stores."""
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f'Missing or linked Zarr store: {path}')
    files = {}
    for p in sorted(path.rglob('*')):
        if p.is_symlink():
            raise ValueError(f'Linked Zarr component: {p}')
        if p.is_file():
            files[str(p.relative_to(path))] = sha256_file(p)
    if not files:
        raise ValueError(f'Empty Zarr store: {path}')
    return files


def publish_zarr(dataset, target):
    """Publish a new store and return its file hashes; never replace an existing store."""
    target = Path(target)
    partial = target.with_name(target.name + '.partial')
    if target.exists() or partial.exists():
        raise FileExistsError(f'Use a new output directory: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_zarr(partial, mode='w-', consolidated=False)
    hashes = store_files(partial)
    partial.rename(target)
    return hashes


def verify_store(store, record_path):
    """Reject changed chunks, added/missing files and mismatched embedded provenance."""
    record = json.loads(Path(record_path).read_text())
    expected = record.get('store_sha256')
    if not expected:
        raise ValueError(f'No store checksums in {record_path}; regenerate preparation in a new directory')
    if store_files(store) != expected:
        raise ValueError(f'Zarr checksum mismatch: {store}')
    with xr.open_zarr(store, consolidated=False) as dataset:
        embedded = json.loads(dataset.attrs['provenance'])
    if embedded != {k: v for k, v in record.items() if k != 'store_sha256'}:
        raise ValueError(f'Zarr provenance mismatch: {store}')
    return record


def write_json(path, value):
    """Write a new JSON record without overwriting prior results."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=1, default=str, allow_nan=False) + '\n')
