"""Shared UTC selection, streaming transfers and atomic download storage.

Imported by the pull scripts; no standalone entry point. Run offline checks with:
    pixi run python -m unittest discover -s tests -p 'test_data_downloads.py'
"""

from datetime import date, datetime, timedelta, timezone
from http.client import IncompleteRead
import os
from pathlib import Path
import tempfile
from time import perf_counter
from urllib.request import urlopen


TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024


def utc_date_bounds(start_date, end_date):
    """Inclusive UTC calendar dates become a half-open datetime interval."""
    start = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), timezone.utc)
    end = datetime.combine(date.fromisoformat(end_date) + timedelta(days=1),
                           datetime.min.time(), timezone.utc)
    if start >= end:
        raise ValueError("START_DATE must not be after END_DATE")
    return start, end


def copy_response(address, destination, stats=None, opener=None):
    """Stream original bytes and reject incomplete transfers.

    An optional caller-owned stats object accumulates bytes_received and
    transfer_seconds; this library does not format or print progress.
    """
    started = perf_counter()
    open_url = opener.open if opener is not None else urlopen
    try:
        with open_url(address, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ValueError(f"Expected HTTP 200, received {response.status}")
            count = 0
            while chunk := response.read(CHUNK_BYTES):
                if stats is not None:
                    stats.bytes_received += len(chunk)
                destination.write(chunk)
                count += len(chunk)
            expected = response.headers.get("Content-Length")
            if count == 0 or (expected is not None and count != int(expected)):
                raise ValueError("Empty or truncated response")
    except IncompleteRead as error:
        if stats is not None:
            stats.bytes_received += len(error.partial)
        raise
    finally:
        if stats is not None:
            stats.transfer_seconds += perf_counter() - started


def publish_file(target, write, validate):
    """Validate before atomic publication; never overwrite an existing product.

    The caller's validation function checks both existing and downloaded files:
    archive checksums for NISAR, structural validation for the other providers.
    """
    target = Path(target)
    if target.exists():
        validate(target)
        return "validated existing"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + ".",
                                         suffix=".part", delete=False) as destination:
            temporary = Path(destination.name)
            write(destination)
        validate(temporary)
        os.link(temporary, target)
        return "downloaded"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
