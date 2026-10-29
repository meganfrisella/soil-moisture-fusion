"""Synthetic SMAP catalog, cached-file, extraction and EASE-Grid alignment checks."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import h5py
import numpy as np
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smap.catalog import download, write_manifest
from smap.ease2 import (CELL_M, NISAR_SUBCELLS, X_ORIGIN, Y_ORIGIN,
                       block_statistics, box_cells, cell_centers, nested_labels)
from smap.l2e import (FLOAT_FIELDS, GROUP, parse_granule, parse_utc, read_box,
                      recommended, verify_file)


POSTING = CELL_M / NISAR_SUBCELLS


def native_axes(row0, col0, offset_rows, offset_cols, n_rows, n_cols):
    """Native centres starting offset_* subcells into 9 km cell (row0, col0)."""
    x0 = X_ORIGIN + col0 * CELL_M + (offset_cols + 0.5) * POSTING
    y0 = Y_ORIGIN - row0 * CELL_M - (offset_rows + 0.5) * POSTING
    return y0 - np.arange(n_rows) * POSTING, x0 + np.arange(n_cols) * POSTING


class BoxCellTests(unittest.TestCase):
    def test_cells_lie_inside_and_neighbours_do_not(self):
        """Selected cell edges are inside the box; one more column/row would cross it."""
        rows, cols = box_cells(46.05737, -121.241744, 48.076483, -117.072163)
        to_grid = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
        (xw, xe), (ys, yn) = to_grid.transform([-121.241744, -117.072163], [46.05737, 48.076483])
        left, right = X_ORIGIN + cols[0] * CELL_M, X_ORIGIN + (cols[-1] + 1) * CELL_M
        top, bottom = Y_ORIGIN - rows[0] * CELL_M, Y_ORIGIN - (rows[-1] + 1) * CELL_M
        self.assertTrue(left >= xw and right <= xe and top <= yn and bottom >= ys)
        self.assertTrue(left - CELL_M < xw and right + CELL_M > xe)
        self.assertTrue(top + CELL_M > yn and bottom - CELL_M < ys)

    def test_centres_round_trip(self):
        """Cell centres fall in the middle of their own row/column."""
        y, x = cell_centers(np.array([206]), np.array([629]))
        self.assertAlmostEqual((x[0] - X_ORIGIN) / CELL_M, 629.5)
        self.assertAlmostEqual((Y_ORIGIN - y[0]) / CELL_M, 206.5)


class NestingTests(unittest.TestCase):
    def test_offset_native_grid_maps_whole_blocks(self):
        """A native grid offset 16/34 subcells labels exact 45x45 blocks; margins are -1."""
        rows, cols = np.array([207, 208]), np.array([630, 631, 632])
        y, x = native_axes(206, 629, 34, 16, (45 - 34) + 2 * 45 + 10, (45 - 16) + 3 * 45 + 7)
        labels, offset = nested_labels(y, x, rows, cols)
        self.assertLess(offset, 1e-6)
        counts = np.bincount(labels[labels >= 0], minlength=6)
        np.testing.assert_array_equal(counts, 45 * 45)
        self.assertEqual(labels[0, 0], -1)
        self.assertEqual(labels[11, 29], 0)

    def test_misaligned_or_wrong_posting_rejected(self):
        """A 5 m shift or a 200 m nominal posting is not silently accepted."""
        rows, cols = np.array([207]), np.array([630])
        y, x = native_axes(206, 629, 0, 0, 100, 100)
        with self.assertRaises(ValueError):
            nested_labels(y, x + 5.0, rows, cols)
        with self.assertRaises(ValueError):
            nested_labels(y, x[0] + np.arange(100) * 200.0, rows, cols)


class BlockStatisticsTests(unittest.TestCase):
    def test_missing_values_excluded_and_empty_cells_nan(self):
        """Invalid values do not contribute; a cell with none valid has NaN mean/SD."""
        labels = np.array([[0, 0, 1, 1], [0, 0, 1, 1]])
        values = np.array([[0.1, 0.3, np.nan, 0.5], [0.2, 9.0, np.nan, 0.5]])
        valid = np.isfinite(values) & (values < 1)
        valid[:, 2:] = False
        count, mean, sd = block_statistics(values, valid, labels, 3)
        np.testing.assert_array_equal(count, [3, 0, 0])
        self.assertAlmostEqual(mean[0], 0.2)
        self.assertAlmostEqual(sd[0], np.std([0.1, 0.3, 0.2]))
        self.assertTrue(np.isnan(mean[1:]).all() and np.isnan(sd[1:]).all())


NAME = "SMAP_L2_SM_P_E_60767_D_20260617T134650_R19240_002.h5"


ITEM = {"meta": {"concept-id": "G1-NSIDC_CPRD", "revision-id": 1}, "umm": {
    "GranuleUR": NAME + "_pDJvhVoW",
    "CollectionReference": {"ShortName": "SPL2SMP_E", "Version": "006"},
    "DataGranule": {"Identifiers": [{"Identifier": NAME, "IdentifierType": "ProducerGranuleId"}],
                    "ProductionDateTime": "2026-08-29T06:51:23Z",
                    "ArchiveAndDistributionInformation": [
                        {"Name": NAME, "SizeInBytes": 10,
                         "Checksum": {"Algorithm": "MD5", "Value": "0" * 32}},
                        {"Name": NAME.replace(".h5", ".qa"), "SizeInBytes": 1,
                         "Checksum": {"Algorithm": "MD5", "Value": "1" * 32}}]},
    "TemporalExtent": {"RangeDateTime": {"BeginningDateTime": "2026-06-17T13:46:50Z",
                                         "EndingDateTime": "2026-06-17T14:36:00Z"}},
    "RelatedUrls": [{"Type": "GET DATA", "URL": "https://data.nsidc.earthdatacloud.nasa.gov/x/" + NAME},
                    {"Type": "GET DATA VIA DIRECT ACCESS", "URL": "s3://bucket/" + NAME}]}}


def write_granule(path, rows, cols, moisture, flags, times):
    """Minimal SPL2SMP_E-like HDF5 with 1D swath arrays."""
    n = len(rows)
    with h5py.File(path, "w") as h5:
        g = h5.create_group(GROUP)
        g["EASE_row_index"] = np.asarray(rows, np.uint16)
        g["EASE_column_index"] = np.asarray(cols, np.uint16)
        g["soil_moisture"] = np.asarray(moisture, np.float32)
        for name in FLOAT_FIELDS[1:] + ("latitude", "longitude"):
            g[name] = np.full(n, 1.0, np.float32)
        g["retrieval_qual_flag"] = np.asarray(flags, np.uint16)
        g["surface_flag"] = np.zeros(n, np.uint16)
        g["tb_time_utc"] = np.array(times, dtype="S24")


class CatalogTests(unittest.TestCase):
    def test_parses_primary_file_not_sidecar(self):
        """The producer ID names the .h5; the suffixed GranuleUR and .qa sidecar are ignored."""
        g = parse_granule(ITEM)
        self.assertEqual((g.name, g.direction, g.orbit, g.crid), (NAME, "D", 60767, "R19240"))
        self.assertEqual(g.md5, "0" * 32)

    def test_wrong_version_or_missing_checksum_rejected(self):
        """Another collection version or a non-MD5 checksum stops parsing."""
        item = copy.deepcopy(ITEM)
        item["umm"]["CollectionReference"]["Version"] = "005"
        with self.assertRaises(ValueError):
            parse_granule(item)
        item = copy.deepcopy(ITEM)
        item["umm"]["DataGranule"]["ArchiveAndDistributionInformation"][0]["Checksum"]["Algorithm"] = "SHA-1"
        with self.assertRaises(ValueError):
            parse_granule(item)

    def test_manifest_rerun_and_changed_selection(self):
        """An identical rerun keeps the manifest; a changed selection is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            g = parse_granule(ITEM)
            self.assertEqual(write_manifest(path, [g], {"q": 1}), "written")
            self.assertEqual(write_manifest(path, [g], {"q": 2}), "unchanged")
            changed = parse_granule(ITEM).__class__(**{**g.__dict__, "md5": "f" * 32})
            with self.assertRaises(RuntimeError):
                write_manifest(path, [changed], {"q": 1})
            self.assertEqual(json.loads(path.read_text())["granules"][0]["md5"], "0" * 32)


    def test_frozen_selection_rejects_different_study_scope(self):
        """Another box or date window must fail before downloading or publishing a manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'selection.json'
            write_manifest(path, [parse_granule(ITEM)], {"q": 1})
            original = json.loads(path.read_text())
            for field, value in (("box_wgs84", {}), ("utc_dates", ["2025-01-01", "2025-01-02"])):
                with self.subTest(field=field):
                    changed = {**original, field: value}
                    path.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, 'different study box or UTC window'):
                        download(root / 'cache', root / field, path)
                    self.assertFalse((root / field / 'smap_l2e_manifest.json').exists())


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / NAME
        write_granule(self.path, [10, 10, 11, 50], [20, 21, 20, 20], [0.2, -9999, 0.3, 0.4],
                      [0, 0, 1, 0], ["2026-06-17T13:55:01.250Z", "2026-06-17T13:55:02.000Z",
                                     "-9999", "2026-06-17T13:55:03.000Z"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_verify_detects_corruption_and_truncation(self):
        """Correct MD5/size pass; a flipped byte or wrong size fails."""
        data = self.path.read_bytes()
        verify_file(self.path, hashlib.md5(data).hexdigest(), len(data))
        with self.assertRaises(ValueError):
            verify_file(self.path, hashlib.md5(data).hexdigest(), len(data) + 1)
        self.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(ValueError):
            verify_file(self.path, hashlib.md5(data).hexdigest(), len(data))

    def test_read_box_fill_time_and_extent(self):
        """Fill becomes NaN/NaT, out-of-box swath cells are dropped, absent cells flag -1."""
        out = read_box(self.path, np.array([10, 11]), np.array([20, 21]))
        np.testing.assert_array_equal(out["swath_cell"], [[1, 1], [1, 0]])
        self.assertAlmostEqual(out["soil_moisture"][0, 0], 0.2, places=6)
        self.assertTrue(np.isnan(out["soil_moisture"][0, 1]) and np.isnan(out["soil_moisture"][1, 1]))
        self.assertEqual(out["retrieval_qual_flag"][1, 1], -1)
        self.assertEqual(str(out["tb_time_utc"][0, 0]), "2026-06-17T13:55:01.250")
        self.assertTrue(np.isnat(out["tb_time_utc"][1, 0]))

    def test_flag_fill_is_not_a_bit_pattern(self):
        """Flag fill 65534 becomes -1 rather than a set of quality bits."""
        write_granule(self.path, [10], [20], [0.2], [65534], ["2026-06-17T13:55:01Z"])
        out = read_box(self.path, np.array([10]), np.array([20]))
        self.assertEqual(out["retrieval_qual_flag"][0, 0], -1)

    def test_recommended_screen(self):
        """Only finite moisture with flag bit 0 clear is recommended; absent or fill flags are not."""
        moisture = np.array([0.2, 0.2, np.nan, 0.2])
        flags = np.array([0, 1, 0, -1])
        np.testing.assert_array_equal(recommended(moisture, flags), [True, False, False, False])

    def test_duplicate_cells_rejected(self):
        """Two swath records for one grid cell are an error, not an overwrite."""
        write_granule(self.path, [10, 10], [20, 20], [0.2, 0.3], [0, 0], ["2026-06-17T13:55:01Z"] * 2)
        with self.assertRaises(ValueError):
            read_box(self.path, np.array([10]), np.array([20]))

    def test_masked_milliseconds_keep_whole_seconds(self):
        """A provider string with masked milliseconds keeps its whole-second time."""
        self.assertEqual(str(parse_utc([b"2026-06-20T14:14:04.***Z"])[0]), "2026-06-20T14:14:04.000")

    def test_naive_time_rejected(self):
        """A timestamp without a UTC designator is not assumed to be UTC."""
        with self.assertRaises(ValueError):
            parse_utc([b"2026-06-17T13:55:01"])


if __name__ == "__main__":
    unittest.main()
