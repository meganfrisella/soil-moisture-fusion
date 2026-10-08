"""Offline tests for native data preservation, UTC selection, and safe downloads."""

from datetime import datetime, timezone
from contextlib import ExitStack
import hashlib
from http.client import IncompleteRead
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NISAR = load("pull_nisar_sme2")
from utils import download_utils as UTILS


class DateTests(unittest.TestCase):
    def test_nisar_inclusive_utc_dates(self):
        """Preserve the NISAR inclusive UTC interval and reject reversed download dates."""
        bounds = (datetime(2026, 6, 24, tzinfo=timezone.utc),
                  datetime(2026, 9, 17, tzinfo=timezone.utc))
        for module in (NISAR,):
            with self.subTest(script=module.__name__):
                self.assertEqual(module.date_bounds(), bounds)
                with patch.object(module, "START_DATE", "2026-09-17"):
                    with self.assertRaisesRegex(ValueError, "START_DATE"):
                        module.date_bounds()


class StorageTests(unittest.TestCase):
    def test_transport_detects_short_body_and_non_200_response(self):
        """Reject response bodies shorter than Content-Length and HTTP statuses other than 200."""
        for status, size, body in ((200, "10", b"short"), (204, None, b""), (206, "3", b"abc")):
            response = io.BytesIO(body)
            response.status = status
            response.headers = {} if size is None else {"Content-Length": size}
            with patch.object(UTILS, "urlopen", return_value=response), self.assertRaises(ValueError):
                UTILS.copy_response("https://example.org", io.BytesIO())

    def test_atomic_publication_and_no_overwrite_on_rerun(self):
        """Publish validated bytes, skip valid existing files, and leave corrupt files untouched."""
        body = b"native bytes"
        def validate(path):
            if path.read_bytes() != body:
                raise ValueError("Invalid content")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "native"
            self.assertEqual(UTILS.publish_file(target, lambda out: out.write(body), validate), "downloaded")
            self.assertEqual(UTILS.publish_file(target, lambda _: self.fail("Unexpected download"), validate),
                             "validated existing")
            target.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                UTILS.publish_file(target, lambda out: out.write(body), validate)
            self.assertEqual(target.read_bytes(), b"corrupt")

    def test_failure_cleans_temporary_file(self):
        """Remove temporary files after interrupted transfers or failed content validation."""
        def interrupted(out):
            out.write(b"partial")
            raise ConnectionError("interrupted")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "native"
            for write in (interrupted, lambda out: out.write(b"bad")):
                with self.assertRaises((ConnectionError, ValueError)):
                    UTILS.publish_file(target, write, lambda _: (_ for _ in ()).throw(ValueError("invalid")))
                self.assertEqual(list(Path(directory).iterdir()), [])


PAYLOAD = NISAR.HDF5_SIGNATURE + b"synthetic bytes for transfer-integrity tests"
NAME = "NISAR_L3_PR_SME2_023_143_D_065_TEST"


def catalog_item(name=NAME, start="2026-06-24T03:02:12Z"):
    """Construct a small CMR record with both a primary product and a QA sidecar."""
    return {"umm": {
        "GranuleUR": name,
        "CollectionReference": {"ShortName": NISAR.COLLECTION},
        "AdditionalAttributes": [
            {"Name": "TRACK_NUMBER", "Values": ["143"]},
            {"Name": "FRAME_NUMBER", "Values": ["65"]},
        ],
        "TemporalExtent": {"RangeDateTime": {"BeginningDateTime": start}},
        "RelatedUrls": [
            {"Type": "GET DATA", "URL": f"https://{NISAR.DOWNLOAD_HOST}/{name}_QA_STATS.h5"},
            {"Type": "GET DATA", "URL": f"https://{NISAR.DOWNLOAD_HOST}/{name}.h5"},
        ],
        "DataGranule": {"ArchiveAndDistributionInformation": [{
            "Name": name + ".h5",
            "Checksum": {"Algorithm": "MD5", "Value": hashlib.md5(PAYLOAD).hexdigest()},
        }]},
    }}


def download_response(body=PAYLOAD, status=200):
    """Supply a context-managed byte stream with an HTTP status."""
    response = io.BytesIO(body)
    response.status = status
    response.headers = {"Content-Length": str(len(body))}
    return response


class DownloadStatsTests(unittest.TestCase):
    def test_totals_measure_response_bytes_and_request_time(self):
        """Sum response bytes and request durations to calculate average transfer speed."""
        with patch.object(UTILS, "perf_counter", side_effect=[0, 1, 3, 4, 7, 10]):
            stats = UTILS.DownloadStats()
            for body in (b"abcd", b"efghij"):
                with patch.object(UTILS, "urlopen", return_value=download_response(body)):
                    UTILS.copy_response("https://provider.example/query", io.BytesIO(), stats)
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                stats.report()
        self.assertEqual(stats.bytes_received, 10)
        self.assertEqual(stats.transfer_seconds, 5)
        report = output.getvalue()
        for expected in ("10 bytes", "2.00 B/s", "5.000 s", "10.000 s"):
            self.assertIn(expected, report)

    def test_partial_transfers_count_received_bytes_and_elapsed_time(self):
        """Include bytes delivered through IncompleteRead when the connection fails."""
        response = Mock()
        response.status = 200
        response.headers = {}
        response.read.side_effect = [b"abc", IncompleteRead(b"de", 10)]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(UTILS, "perf_counter", side_effect=[0, 1, 4]), \
                patch.object(UTILS, "urlopen", return_value=response):
            stats = UTILS.DownloadStats()
            with self.assertRaises(IncompleteRead):
                UTILS.copy_response("https://example.org/file", io.BytesIO(), stats)
        self.assertEqual(stats.bytes_received, 5)
        self.assertEqual(stats.transfer_seconds, 3)

    def test_nisar_reports_downloads_and_zero_transfer_on_rerun(self):
        """Exercise the NISAR entry point and ensure cached files do not inflate network totals."""
        for module, body in ((NISAR, PAYLOAD),):
            with self.subTest(script=module.__name__), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                output_dir = Path(directory)
                stack.enter_context(patch.object(module, 'OUTPUT_DIR', output_dir))
                granule = NISAR.parse_granule(catalog_item(), (143, 65))
                stack.enter_context(patch.object(NISAR, 'search_granules', return_value=[granule]))
                opener = Mock()
                opener.open.return_value = download_response(body)
                stack.enter_context(patch.object(NISAR, 'earthdata_opener', return_value=opener))
                fetch = opener.open
                with patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(module.main([]), 0)
                self.assertIn(f'({len(body):,} bytes of response bodies)', output.getvalue())
                with patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(module.main([]), 0)
                fetch.assert_called_once()
                self.assertIn('(0 bytes of response bodies)', output.getvalue())
                self.assertIn('N/A (no bytes transferred)', output.getvalue())

    def test_nisar_reports_stats_on_failure_without_retaining_partial_files(self):
        """Print summaries on unsuccessful transfers while preserving atomic cleanup."""
        for module in (NISAR,):
            with self.subTest(script=module.__name__), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                output_dir = Path(directory)
                stack.enter_context(patch.object(module, 'OUTPUT_DIR', output_dir))
                response = download_response(b'truncated')
                response.headers['Content-Length'] = '100'
                granule = NISAR.parse_granule(catalog_item(), (143, 65))
                stack.enter_context(patch.object(NISAR, 'search_granules', return_value=[granule]))
                opener = Mock()
                opener.open.return_value = response
                stack.enter_context(patch.object(NISAR, 'earthdata_opener', return_value=opener))
                with patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(module.main([]), 1)
                self.assertIn('(9 bytes of response bodies)', output.getvalue())
                self.assertEqual(list(output_dir.iterdir()), [])


class PullNisarTests(unittest.TestCase):
    def setUp(self):
        self.granule = NISAR.parse_granule(catalog_item(), (143, 65))

    def test_catalog_selection_preserves_end_date_and_excludes_sidecars(self):
        """Select only the original product and include acquisitions late on the final UTC day."""
        item = catalog_item(start="2026-09-16T23:59:59.999999Z")
        granule = NISAR.parse_granule(item, (143, 65))
        self.assertEqual(granule.name, NAME + ".h5")
        self.assertNotIn("QA_STATS", granule.url)
        self.assertEqual(granule.start.day, 16)
        for invalid in ("2026-06-23T23:59:59Z", "2026-09-17T00:00:00Z"):
            with self.subTest(start=invalid), self.assertRaisesRegex(ValueError, "outside"):
                NISAR.parse_granule(catalog_item(start=invalid), (143, 65))

    def test_catalog_rejects_wrong_collection_frame_and_download_host(self):
        """Reject unexpected products or download destinations even when CMR returns them."""
        item = catalog_item()
        item["umm"]["CollectionReference"]["ShortName"] = "NISAR_L3_SME2_BETA_V1"
        with self.assertRaisesRegex(ValueError, "collection"):
            NISAR.parse_granule(item, (143, 65))
        with self.assertRaisesRegex(ValueError, "track/frame"):
            NISAR.parse_granule(catalog_item(), (143, 64))
        item = catalog_item()
        item["umm"]["RelatedUrls"][1]["URL"] = f"https://example.org/{NAME}.h5"
        with self.assertRaisesRegex(ValueError, "primary HDF5"):
            NISAR.parse_granule(item, (143, 65))

    def test_acquisition_times_are_normalized_to_utc_before_selection(self):
        """Select acquisitions by UTC instant and reject timestamps without a timezone."""
        granule = NISAR.parse_granule(catalog_item(start="2026-06-23T17:00:00-07:00"), (143, 65))
        self.assertEqual(granule.start.isoformat(), "2026-06-24T00:00:00+00:00")
        with self.assertRaisesRegex(ValueError, "outside"):
            NISAR.parse_granule(catalog_item(start="2026-09-16T17:00:00-07:00"), (143, 65))
        with self.assertRaisesRegex(ValueError, "timezone"):
            NISAR.parse_granule(catalog_item(start="2026-06-24T00:00:00"), (143, 65))

    def test_pagination_collects_all_acquisitions_and_detects_incomplete_results(self):
        """Follow every CMR page and fail on duplicate or prematurely empty pages."""
        calls = []
        def get_page(address):
            query = parse_qs(urlparse(address).query)
            calls.append(query)
            page = int(query["page_num"][0])
            return {"hits": 2, "items": [catalog_item(NAME + str(page))]}
        self.assertEqual(len(NISAR.search_granules(get_page)), 2)
        self.assertEqual(calls[0]["short_name"], [NISAR.COLLECTION])
        self.assertEqual(calls[0]["producer_granule_id[]"], ["NISAR_L3_PR_SME2_*_143_*_065_*"])
        self.assertEqual(calls[0]["temporal"],
                         ["2026-06-24T00:00:00Z,2026-09-16T23:59:59.999999Z"])
        self.assertNotIn("point", calls[0])
        self.assertEqual(NISAR.search_granules(lambda _: {"hits": 0, "items": []}), [])
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            NISAR.search_granules(lambda _: {"hits": 1, "items": []})
        with self.assertRaisesRegex(RuntimeError, "Repeated"):
            NISAR.search_granules(lambda _: {"hits": 2, "items": [catalog_item()]})

    def test_download_preserves_bytes_and_skips_only_verified_files(self):
        """Publish byte-identical downloads and avoid network requests on a verified rerun."""
        opener = Mock()
        opener.open.return_value = download_response()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertEqual(NISAR.download_granule(self.granule, opener, output), "downloaded")
            target = output / self.granule.name
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertEqual(NISAR.download_granule(self.granule, opener, output), "verified existing")
            opener.open.assert_called_once()
            self.assertEqual(list(output.iterdir()), [target])
            target.write_bytes(NISAR.HDF5_SIGNATURE + b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                NISAR.download_granule(self.granule, opener, output)
            self.assertEqual(target.read_bytes(), NISAR.HDF5_SIGNATURE + b"corrupt")

    def test_invalid_downloads_never_become_products(self):
        """Discard login pages, truncated HDF5 payloads, and unexpected partial HTTP responses."""
        for payload, status in ((b"<html>Login</html>", 200), (PAYLOAD[:-1], 200), (PAYLOAD, 206)):
            with self.subTest(status=status, payload=payload), tempfile.TemporaryDirectory() as directory:
                opener = Mock()
                opener.open.return_value = download_response(payload, status)
                with self.assertRaises(ValueError):
                    NISAR.download_granule(self.granule, opener, Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_interrupted_transfer_removes_temporary_file(self):
        """Leave no partial product when a connection fails during the response body."""
        response = Mock()
        response.status = 200
        response.read.side_effect = [NISAR.HDF5_SIGNATURE, ConnectionError("interrupted")]
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConnectionError):
                NISAR.download_granule(self.granule, opener, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_list_only_needs_neither_credentials_nor_output_directory(self):
        """Allow catalog inspection without authentication or persistent data writes."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent"
            with patch.object(NISAR, "search_granules", return_value=[self.granule]), \
                    patch.object(NISAR, "earthdata_opener") as authenticate, \
                    patch.object(NISAR, "OUTPUT_DIR", output), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(NISAR.main(["--list-only"]), 0)
                authenticate.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
