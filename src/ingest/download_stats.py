"""Console transfer summaries used by download and visualization workflows.

Imported helper; run via ``pixi run python scripts/pull_nisar_sme2.py``.
"""

from time import perf_counter


def format_bytes(count):
    """Display binary units while retaining exact byte counts in the summary."""
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024



class DownloadStats:
    """Measure product response bodies, excluding catalog requests and cached files.

    Transfer time includes connection/authentication waits and streaming writes.
    Run time also includes discovery, validation, and local processing. Bytes
    include received partial/invalid bodies, before any CSV row selection, but
    exclude HTTP/TLS overhead.
    """

    def __init__(self):
        self.started = perf_counter()
        self.bytes_received = 0
        self.transfer_seconds = 0.0

    def report(self):
        """Print totals even for failed transfers or runs using only existing files."""
        elapsed = perf_counter() - self.started
        print("\nDownload statistics:", flush=True)
        print(f"  Total downloaded: {format_bytes(self.bytes_received)} "
              f"({self.bytes_received:,} bytes of response bodies)")
        speed = (format_bytes(self.bytes_received / self.transfer_seconds) + "/s"
                 if self.bytes_received and self.transfer_seconds > 0 else "N/A (no bytes transferred)")
        print(f"  Average download speed: {speed}")
        print(f"  Total download duration: {self.transfer_seconds:.3f} s (requests and streaming)")
        print(f"  Total run duration: {elapsed:.3f} s (including discovery and validation)", flush=True)

