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
from zoneinfo import ZoneInfo

import numpy as np
from obspy import Stream, Trace, UTCDateTime
from obspy.core.inventory import Channel, Inventory, Network, Site, Station
from obspy.core.inventory.response import Response


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEISMIC = load("pull_seismic")
SOIL = load("pull_soil_moisture")
NISAR = load("pull_nisar_sme2")
from utils import download_utils as UTILS


HEADER = b"DateTime,odsw_xsm2,odsw_xsm8,odsw_xsm20,odsw_xsm40\r\n"


class DateAndSoilTests(unittest.TestCase):
    def test_all_three_downloaders_share_inclusive_utc_dates(self):
        """Use the same inclusive UTC date range and reject reversed dates in every downloader."""
        bounds = (datetime(2026, 6, 24, tzinfo=timezone.utc),
                  datetime(2026, 9, 17, tzinfo=timezone.utc))
        for module in (SEISMIC, SOIL, NISAR):
            with self.subTest(script=module.__name__):
                self.assertEqual(module.date_bounds(), bounds)
                with patch.object(module, "START_DATE", "2026-09-17"):
                    with self.assertRaisesRegex(ValueError, "START_DATE"):
                        module.date_bounds()

    def test_soil_queries_enclosing_local_dates(self):
        """Request local dates enclosing the UTC interval, with all soil parameters and flags."""
        query = parse_qs(urlparse(SOIL.request_url("ODSW", "America/Los_Angeles")).query)
        self.assertEqual(query["start"], ["2026-06-23"])
        self.assertEqual(query["end"], ["2026-09-16"])
        self.assertEqual(query["flags"], ["true"])
        self.assertEqual(query["list"], ["odsw xsm2,odsw xsm8,odsw xsm20,odsw xsm40"])

    def test_soil_selects_utc_interval_without_changing_native_rows_or_units(self):
        """Select UTC boundaries while preserving native rows and units and counting missing data."""
        rows = [b"2026-06-23 16:45,0.1,0.2,0.3,0.4\r\n",
                b"2026-06-23 17:00,0.170,0.21,,M\r\n",
                b"2026-09-16 16:45,0.10,-9999,0.150,0.14\r\n",
                b"2026-09-16 17:00,0.1,0.2,0.3,0.4\r\n"]
        selected, summary = SOIL.select_csv(HEADER + b"".join(rows), "ODSW", "America/Los_Angeles")
        self.assertEqual(selected, HEADER + rows[1] + rows[2])
        self.assertEqual(summary["expected_rows"], 8160)
        self.assertEqual(summary["missing_rows"], 8158)
        self.assertEqual(summary["missing_values"], {
            "odsw_xsm2": 0, "odsw_xsm8": 1, "odsw_xsm20": 1, "odsw_xsm40": 1})
        self.assertEqual(set(SOIL.PARAMETER_UNITS.values()), {"percent"})
        self.assertEqual(summary["first_utc"], "2026-06-24T00:00:00+00:00")
        self.assertEqual(summary["last_utc"], "2026-09-16T23:45:00+00:00")

    def test_soil_preserves_returned_flag_columns(self):
        """Retain provider flag columns and their values byte-for-byte during row selection."""
        payload = (HEADER.rstrip(b"\r\n") + b",quality\n"
                   b"2026-06-23 17:00,0.17,0.21,0.20,0.16,Q\n")
        selected, _ = SOIL.select_csv(payload, "ODSW", "America/Los_Angeles")
        self.assertEqual(selected, payload)

    def test_local_timezone_uses_dst_and_rejects_ambiguous_times(self):
        """Apply seasonal UTC offsets and reject ambiguous or nonexistent local timestamps."""
        zone = ZoneInfo("America/Los_Angeles")
        self.assertEqual(SOIL.timestamp_utc("2026-01-01 16:00", zone).hour, 0)
        self.assertEqual(SOIL.timestamp_utc("2026-06-23 17:00", zone).hour, 0)
        for local in ("2026-11-01 01:30", "2026-03-08 02:30"):
            with self.subTest(local=local), self.assertRaisesRegex(ValueError, "local timestamp"):
                SOIL.timestamp_utc(local, zone)

    def test_soil_rejects_bad_response_empty_selection_and_duplicate_timestamps(self):
        """Reject malformed CSV, empty selections, duplicate timestamps, and off-grid samples."""
        row = b"2026-06-23 17:00,0.17,0.21,0.20,0.16\n"
        for payload in (b"<html>Error</html>", HEADER, HEADER + row + row,
                        HEADER + b"2026-06-23 17:00,0.17\n",
                        HEADER + b"2026-06-23 17:01,0.17,0.21,0.20,0.16\n"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                SOIL.select_csv(payload, "ODSW", "America/Los_Angeles")


class NativeSeismicTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = SEISMIC.date_bounds()

    def waveform(self, station="OD2", channels=SEISMIC.CHANNELS, start=None):
        stream = Stream()
        for channel in channels:
            stream += Trace(np.arange(200, dtype=np.int32), header={
                "network": "UW", "station": station, "location": "", "channel": channel,
                "sampling_rate": 100, "starttime": UTCDateTime(start or self.start)})
        buffer = io.BytesIO()
        stream.write(buffer, format="MSEED", reclen=512)
        return buffer.getvalue()

    def test_daily_requests_have_no_next_day_sample_or_missing_final_day(self):
        """Cover every requested station/day with the selected channels and exclusive day boundaries."""
        plan = list(SEISMIC.requests())
        self.assertEqual(len(plan), 172)
        waveforms = [entry for entry in plan if entry[2] == "miniseed"]
        self.assertEqual(len(waveforms), 170)
        for station in ("OD2", "WOLL"):
            entries = [entry for entry in waveforms if entry[4] == station]
            self.assertEqual(entries[0][5], self.start)
            self.assertEqual(entries[-1][6], self.end)
            query = parse_qs(urlparse(entries[-1][1]).query)
            self.assertEqual(query["endtime"], ["2026-09-16T23:59:59.999999"])
            self.assertEqual(query["loc"], ["--"])
            self.assertEqual(query["cha"], ["HHE,HHN,HHZ"])

    def test_waveforms_validate_identity_and_truncation_without_rewriting(self):
        """Preserve valid miniSEED bytes and reject truncation, wrong channels, and invalid times."""
        good = self.waveform()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.mseed"
            path.write_bytes(good)
            SEISMIC.validate_file(path, "miniseed", "UW", "OD2", self.start, self.end)
            self.assertEqual(path.read_bytes(), good)
            for bad in (good[:-10], self.waveform(station="WOLL"),
                        self.waveform(channels=("HHZ",)),
                        self.waveform(start=self.end), b"<html>login</html>"):
                path.write_bytes(bad)
                with self.subTest(size=len(bad)), self.assertRaises(Exception):
                    SEISMIC.validate_file(path, "miniseed", "UW", "OD2", self.start, self.end)

    def test_stationxml_requires_all_channels_and_instrument_responses(self):
        """Accept StationXML for the requested channels and reject missing instrument responses."""
        channels = [Channel(code=code, location_code="", latitude=47.387351,
                            longitude=-118.711613, elevation=552, depth=1,
                            sample_rate=100, start_date=UTCDateTime(2022, 1, 19),
                            response=Response.from_paz([], [], 1)) for code in SEISMIC.CHANNELS]
        station = Station("OD2", 47.387351, -118.711613, 552,
                          channels=channels, site=Site(name="Synthetic"))
        inventory = Inventory([Network("UW", stations=[station])], source="Synthetic")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.xml"
            inventory.write(str(path), format="STATIONXML")
            SEISMIC.validate_file(path, "stationxml", "UW", "OD2", self.start, self.end)
            channels[0].response = None
            inventory.write(str(path), format="STATIONXML")
            with self.assertRaisesRegex(ValueError, "response"):
                SEISMIC.validate_file(path, "stationxml", "UW", "OD2", self.start, self.end)


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

    def test_list_only_does_not_fetch_or_write(self):
        """List seismic and soil requests without fetching data or creating output directories."""
        for module in (SEISMIC, SOIL):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "absent"
                with patch.object(module, "OUTPUT_DIR", output), \
                        patch.object(module, "copy_response") as fetch, \
                        patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(module.main(["--list-only"]), 0)
                    fetch.assert_not_called()
                    self.assertFalse(output.exists())


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

    def test_all_scripts_report_downloads_and_zero_transfer_on_rerun(self):
        """Exercise each entry point and ensure cached files do not inflate network totals."""
        soil_body = (HEADER + b"2026-06-23 16:45,0.1,0.2,0.3,0.4\r\n"
                     b"2026-06-23 17:00,0.1,0.2,0.3,0.4\r\n")
        for module, body in ((SEISMIC, b"waveform"), (SOIL, soil_body), (NISAR, PAYLOAD)):
            with self.subTest(script=module.__name__), tempfile.TemporaryDirectory() as directory, \
                    ExitStack() as stack:
                output_dir = Path(directory)
                stack.enter_context(patch.object(module, "OUTPUT_DIR", output_dir))
                if module is NISAR:
                    granule = NISAR.parse_granule(catalog_item(), (143, 65))
                    stack.enter_context(patch.object(NISAR, "search_granules", return_value=[granule]))
                    opener = Mock()
                    opener.open.return_value = download_response(body)
                    stack.enter_context(patch.object(NISAR, "earthdata_opener", return_value=opener))
                    fetch = opener.open
                else:
                    fetch = stack.enter_context(patch.object(UTILS, "urlopen",
                                                             return_value=download_response(body)))
                    if module is SEISMIC:
                        plan = [(output_dir / "test.mseed", "https://example.org/waveform",
                                 "miniseed", "UW", "OD2", *SEISMIC.date_bounds())]
                        stack.enter_context(patch.object(SEISMIC, "requests", return_value=plan))
                        stack.enter_context(patch.object(SEISMIC, "validate_file"))
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(module.main([]), 0)
                self.assertIn(f"({len(body):,} bytes of response bodies)", output.getvalue())
                if module is SOIL:
                    self.assertLess(next(output_dir.iterdir()).stat().st_size, len(body))
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(module.main([]), 0)
                fetch.assert_called_once()
                self.assertIn("(0 bytes of response bodies)", output.getvalue())
                self.assertIn("N/A (no bytes transferred)", output.getvalue())

    def test_all_scripts_report_stats_on_failure_without_retaining_partial_files(self):
        """Print summaries on unsuccessful transfers while preserving atomic cleanup."""
        for module in (SEISMIC, SOIL, NISAR):
            with self.subTest(script=module.__name__), tempfile.TemporaryDirectory() as directory, \
                    ExitStack() as stack:
                output_dir = Path(directory)
                stack.enter_context(patch.object(module, "OUTPUT_DIR", output_dir))
                response = download_response(b"truncated")
                response.headers["Content-Length"] = "100"
                if module is NISAR:
                    granule = NISAR.parse_granule(catalog_item(), (143, 65))
                    stack.enter_context(patch.object(NISAR, "search_granules", return_value=[granule]))
                    opener = Mock()
                    opener.open.return_value = response
                    stack.enter_context(patch.object(NISAR, "earthdata_opener", return_value=opener))
                else:
                    stack.enter_context(patch.object(UTILS, "urlopen", return_value=response))
                    if module is SEISMIC:
                        plan = [(output_dir / "test.mseed", "https://example.org/waveform",
                                 "miniseed", "UW", "OD2", *SEISMIC.date_bounds())]
                        stack.enter_context(patch.object(SEISMIC, "requests", return_value=plan))
                with patch("sys.stdout", new_callable=io.StringIO) as output, \
                        patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(module.main([]), 1)
                self.assertIn("(9 bytes of response bodies)", output.getvalue())
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
