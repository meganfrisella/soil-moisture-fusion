"""Offline integrity checks for the R01 frozen-input audit."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import h5py

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from regridding.inputs import VERSION_FIELDS, audit_inputs, sha256_file
from nisar_fixtures import write_valid_product


class FrozenInputTests(unittest.TestCase):
    def setUp(self):
        """Create a tiny original and checksum-bearing selection without private data."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.product = self.cache / "NISAR_TEST.h5"
        write_valid_product(self.product)
        self.selection = self.cache / "selection.json"
        self.record = {
            "selected_granule_names": [self.product.name], "selected_granules": 1,
            "provenance": {"source_checksums": {}}, "bounds_wgs84": {},
            "selected_frames": 1, "distinct_utc_dates": 1,
            "acquisition_start_utc": "2026-06-17T02:53:18+00:00",
            "acquisition_end_utc": "2026-06-17T02:53:18+00:00",
        }
        self.save_selection()

    def save_selection(self):
        """Refresh synthetic fixture checksums after an intentional test mutation."""
        self.record["provenance"]["source_checksums"] = {
            self.product.name: {"algorithm": "md5", "value": hashlib.md5(
                self.product.read_bytes()).hexdigest()}}
        self.selection.write_text(json.dumps(self.record))
        self.digest = sha256_file(self.selection)

    def audit(self):
        """Call the same public function and explicit arguments as the logged workflow."""
        return audit_inputs(self.selection, self.cache, self.digest)

    def test_valid_inputs_return_reproducible_identity_without_writes(self):
        """Verify all originals, record actual versions, and leave inputs untouched."""
        before = {p.name: p.read_bytes() for p in self.cache.iterdir()}
        result = self.audit()
        self.assertEqual(result["verified_granules"], 1)
        self.assertEqual(result["excluded_granules"], 0)
        self.assertEqual(result["verified_bytes"], self.product.stat().st_size)
        self.assertEqual(result["versions"], VERSION_FIELDS)
        self.assertEqual(result["selection_sha256"], self.digest)
        self.assertIn("src/regridding/inputs.py", result["code"]["sha256"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.cache.iterdir()})

    def test_missing_original_stops_without_recovery(self):
        """A missing required original raises and is never recreated or skipped."""
        self.product.unlink()
        with self.assertRaises(FileNotFoundError):
            self.audit()
        self.assertFalse(self.product.exists())

    def test_corrupt_original_stops_without_overwrite(self):
        """A checksum mismatch leaves the corrupt file intact and raises."""
        with self.product.open("ab") as destination:
            destination.write(b"corrupt")
        original = self.product.read_bytes()
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.audit()
        self.assertEqual(self.product.read_bytes(), original)

    def test_changed_selection_and_duplicate_names_are_rejected(self):
        """Pin the selection bytes and reject duplicate entries even with a matching hash."""
        self.selection.write_text(self.selection.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "selection SHA-256 mismatch"):
            self.audit()
        self.record["selected_granule_names"] *= 2
        self.record["selected_granules"] = 2
        self.save_selection()
        with self.assertRaisesRegex(ValueError, "names/count/checksums disagree"):
            self.audit()

    def test_checksum_valid_wrong_version_is_rejected(self):
        """Matching archive bytes cannot silently introduce another processing release."""
        key = "science/LSAR/identification/productVersion"
        with h5py.File(self.product, "r+") as product:
            product[key][()] = "unexpected"
        self.save_selection()
        with self.assertRaisesRegex(ValueError, "Unexpected product versions"):
            self.audit()


if __name__ == "__main__":
    unittest.main()
