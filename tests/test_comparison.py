"""Synthetic aggregation, matching, agreement and prepared-store integrity checks."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comparison.aggregation import CANDIDATES, MIN_VALID, SCREENS, aggregate
from comparison.metrics import MIN_PAIRS_PER_CELL, compare, match_smap, pearson
from comparison.provenance import publish_zarr, verify_store, write_json
from smap.ease2 import CELL_M, NISAR_SUBCELLS, X_ORIGIN, Y_ORIGIN


POSTING = CELL_M / NISAR_SUBCELLS


ROWS, COLS = np.array([207, 208]), np.array([630])


PMI, TSR, DSG, MEAN = range(4)


ALL, REC = range(2)


def native(n_rows=90, n_cols=45, row0=207, col0=630):
    """One-acquisition native dataset (PMI/TSR/DSG) covering a 2x1 block of 9 km cells."""
    y = Y_ORIGIN - row0 * CELL_M - (np.arange(n_rows) + 0.5) * POSTING
    x = X_ORIGIN + col0 * CELL_M + (np.arange(n_cols) + 0.5) * POSTING
    shape = (1, 3, n_rows, n_cols)
    moisture = np.empty(shape)
    moisture[0, :] = np.array([0.30, 0.10, 0.20])[:, None, None]
    moisture[0, 0, :45] = np.linspace(0.2, 0.4, 45 * n_cols).reshape(45, n_cols)
    valid = np.ones(shape, np.uint8)
    valid[0, 1, 45:45 + 23] = 0          # TSR: 990 valid in cell 1, below the threshold
    flag = np.zeros(shape, np.int64)
    flag[0, 2, :20] = 1                  # DSG: 900 not recommended in cell 0
    sigma = np.full(shape, 0.03)
    sigma[0, 0, :45, :15] = 0.04         # PMI: one third of cell 0 has larger sigma
    sigma_ok = np.ones(shape, np.uint8)
    sigma_ok[0, 1, :, :] = 0             # TSR: no valid sigma anywhere
    dims = ("acquisition", "candidate", "y", "x")
    t = np.array(["2026-06-17T02:53:18"], dtype="datetime64[ns]")
    return xr.Dataset({"soil_moisture": (dims, moisture), "moisture_valid": (dims, valid),
                       "retrieval_quality_flag": (dims, flag), "flag_valid": (dims, valid.copy()),
                       "reported_uncertainty": (dims, sigma), "uncertainty_valid": (dims, sigma_ok)},
                      coords={"y": y, "x": x, "candidate": ["PMI", "TSR", "DSG"],
                              "acquisition_start": ("acquisition", t)})


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.count, self.mean, self.sd, self.sigma, _ = aggregate(native(), ROWS, COLS)

    def test_layout(self):
        """Outputs are (acquisition, candidate[PMI,TSR,DSG,MEAN], screen[all,recommended], row, col)."""
        self.assertEqual(CANDIDATES, ("PMI", "TSR", "DSG", "MEAN"))
        self.assertEqual(SCREENS, ("all", "recommended"))
        self.assertEqual(self.mean.shape, (1, 4, 2, 2, 1))

    def test_block_mean_count_and_sd(self):
        """Each cell averages its own 2025 subcells; SD reflects within-cell spread."""
        self.assertEqual(self.count[0, PMI, ALL, 0, 0], 2025)
        self.assertAlmostEqual(self.mean[0, PMI, ALL, 0, 0], 0.3)
        self.assertGreater(self.sd[0, PMI, ALL, 0, 0], 0)
        self.assertAlmostEqual(self.sd[0, DSG, ALL, 1, 0], 0)

    def test_recommended_screen_drops_flagged_pixels(self):
        """Odd candidate flags are removed only in the recommended screen."""
        self.assertEqual(self.count[0, DSG, ALL, 0, 0], 2025)
        self.assertEqual(self.count[0, DSG, REC, 0, 0], 2025 - 20 * 45)

    def test_combined_requires_all_three(self):
        """MEAN uses only pixels valid for all three candidates and averages them equally."""
        self.assertEqual(self.count[0, MEAN, ALL, 1, 0], 2025 - 23 * 45)
        self.assertAlmostEqual(self.mean[0, MEAN, ALL, 1, 0], 0.2)
        self.assertEqual(self.count[0, MEAN, REC, 0, 0], 2025 - 20 * 45)

    def test_sigma_linear_mean(self):
        """sigma is the linear mean of valid pixel sigma (fully correlated); absent without valid sigma."""
        self.assertAlmostEqual(self.sigma[0, PMI, ALL, 0, 0], (675 * 0.04 + 1350 * 0.03) / 2025)
        self.assertTrue(np.isnan(self.sigma[0, TSR]).all())

    def test_mean_sigma_requires_all_three(self):
        """MEAN pixel sigma is the mean of the three candidates' sigma, only where all are valid."""
        self.assertTrue(np.isnan(self.sigma[0, MEAN]).all())  # TSR has no valid sigma
        values = native()
        values["uncertainty_valid"][:] = 1
        _, _, _, sigma, _ = aggregate(values, ROWS, COLS)
        pmi = (675 * 0.04 + 1350 * 0.03) / 2025
        self.assertAlmostEqual(sigma[0, MEAN, ALL, 0, 0], (pmi + 0.03 + 0.03) / 3)

    def test_coverage_threshold_inputs(self):
        """Invalid subcells reduce the count used for the >= 50% threshold."""
        self.assertEqual(self.count[0, TSR, ALL, 1, 0], 2025 - 23 * 45)
        self.assertLess(self.count[0, TSR, ALL, 1, 0], MIN_VALID)
        self.assertEqual(MIN_VALID, 1013)

    def test_partial_native_coverage_rejected(self):
        """A 9 km cell not fully covered by the native grid stops aggregation."""
        with self.assertRaises(ValueError):
            aggregate(native(n_rows=80), ROWS, COLS)


def ts(text):
    return np.datetime64(text, "ms")


class MatchTests(unittest.TestCase):
    def setUp(self):
        # Three granules over a 1x2 grid; NISAR at 03:00.
        self.times = np.array([[[ts("2026-07-01T01:00"), ts("2026-07-01T01:00")]],
                               [[ts("2026-07-01T05:00"), ts("2026-07-01T05:00")]],
                               [[ts("2026-07-01T06:30"), "NaT"]]], dtype="datetime64[ms]")
        self.swath = np.array([[[1, 1]], [[1, 1]], [[1, 0]]], np.uint8)
        self.moisture = np.array([[[0.10, 0.11]], [[0.20, np.nan]], [[0.30, np.nan]]])

    def test_tie_prefers_earlier_pass(self):
        """Passes 2 h before and after are equidistant; the earlier is chosen."""
        m, dt, g = match_smap(np.array([ts("2026-07-01T03:00")]), self.times, self.swath,
                              self.moisture)
        self.assertEqual(g[0, 0, 0], 0)
        self.assertAlmostEqual(dt[0, 0, 0], -2.0)
        self.assertAlmostEqual(m[0, 0, 0], 0.10)

    def test_nearest_missing_is_not_replaced(self):
        """A nearer pass with missing moisture leaves the pair missing; no farther fallback."""
        m, dt, g = match_smap(np.array([ts("2026-07-01T04:30")]), self.times, self.swath,
                              self.moisture)
        self.assertEqual(g[0, 0, 1], 1)
        self.assertTrue(np.isnan(m[0, 0, 1]))
        self.assertAlmostEqual(m[0, 0, 0], 0.20)

    def test_beyond_three_hours_and_outside_swath(self):
        """Exactly 3 h matches; 3 h + 1 s and cells outside every swath stay unmatched."""
        m, dt, g = match_smap(np.array([ts("2026-07-01T09:30")]), self.times, self.swath,
                              self.moisture)
        self.assertEqual(g[0, 0, 0], 2)
        self.assertEqual(g[0, 0, 1], -1)
        self.assertTrue(np.isnan(dt[0, 0, 1]))
        m, _, g = match_smap(np.array([ts("2026-07-01T09:30:01")]), self.times, self.swath,
                             self.moisture)
        self.assertTrue((g == -1).all() and np.isnan(m).all())


class MetricTests(unittest.TestCase):
    def test_offset_field_has_bias_but_perfect_correlation(self):
        """A constant +0.05 offset gives bias=RMSD=0.05, zero temporal ubRMSD and r=1."""
        rng = np.random.default_rng(0)
        b = rng.uniform(0.05, 0.3, (MIN_PAIRS_PER_CELL + 2, 3, 4))
        per_cell, summary = compare(b + 0.05, b)
        self.assertAlmostEqual(summary["bias"], 0.05)
        self.assertAlmostEqual(summary["rmsd"], 0.05)
        self.assertAlmostEqual(summary["median_r_temporal"], 1)
        np.testing.assert_allclose(per_cell["bias"], 0.05)
        np.testing.assert_allclose(per_cell["ubrmsd_temporal"], 0, atol=1e-12)

    def test_sparse_cells_hidden(self):
        """Cells with fewer than 5 matched pairs are not reported."""
        a = np.linspace(0.1, 0.3, MIN_PAIRS_PER_CELL)[:, None, None] * np.ones((1, 1, 2))
        b = a * 0.9
        b[0, 0, 0] = np.nan
        per_cell, summary = compare(a, b)
        self.assertEqual(per_cell["n_pairs"][0, 0], MIN_PAIRS_PER_CELL - 1)
        self.assertTrue(np.isnan(per_cell["bias"][0, 0]) and np.isnan(per_cell["r_temporal"][0, 0]))
        self.assertTrue(np.isfinite(per_cell["bias"][0, 1]))
        self.assertEqual(summary["matched_pairs"], a.size - 1)

    def test_one_sigma_coverage(self):
        """Coverage is the share of pairs with valid sigma where |a - b| <= sigma (boundary inside)."""
        d = np.array([0.01, -0.02, 0.03, -0.05, 0.02, 0.0])[:, None, None]
        b = np.full_like(d, 0.2)
        sigma = np.full_like(d, 0.02)
        sigma[-1] = np.nan                    # excluded from coverage only
        per_cell, summary = compare(b + d, b, sigma)
        self.assertEqual(per_cell["n_sigma_pairs"][0, 0], 5)
        self.assertAlmostEqual(per_cell["coverage"][0, 0], 3 / 5)
        self.assertAlmostEqual(summary["coverage_1sigma"], 3 / 5)
        self.assertAlmostEqual(per_cell["sigma_mean"][0, 0], 0.02)
        self.assertEqual(summary["matched_pairs"], 6)

    def test_coverage_needs_five_sigma_pairs(self):
        """A cell with fewer than 5 pairs carrying valid sigma has no coverage value."""
        d = np.zeros((6, 1, 1))
        sigma = np.full_like(d, 0.02)
        sigma[:2] = np.nan
        per_cell, _ = compare(d + 0.2, d + 0.2, sigma)
        self.assertTrue(np.isnan(per_cell["coverage"][0, 0]))

    def test_constant_series_correlation_is_nan(self):
        """Correlation with a constant series is undefined, not zero."""
        self.assertTrue(np.isnan(pearson(np.array([1.0, 2.0, 3.0]), np.ones(3), 0)))


class StoreVerificationTests(unittest.TestCase):
    def setUp(self):
        """Write a synthetic prepared store with provenance and a complete file inventory."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'box.zarr'
        self.record_path = self.root / 'box.json'
        self.record = {'product': 'synthetic', 'units': 'm3 m-3'}
        self.dataset = xr.Dataset({'moisture': ('cell', np.array([0.2, np.nan]))},
                                  attrs={'provenance': json.dumps(self.record)})
        self.record['store_sha256'] = publish_zarr(self.dataset, self.store)
        write_json(self.record_path, self.record)

    def test_verifies_complete_store_and_record(self):
        """All file hashes and embedded provenance must agree with the preparation record."""
        self.assertEqual(verify_store(self.store, self.record_path), self.record)

    def test_changed_chunk_is_rejected(self):
        """A single altered data byte cannot pass validation using unchanged sidecars."""
        chunk = next(p for p in self.store.rglob('*') if p.is_file() and p.name != 'zarr.json')
        chunk.write_bytes(chunk.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            verify_store(self.store, self.record_path)

    def test_added_or_missing_file_is_rejected(self):
        """The inventory binds the complete file set, not only the data files that remain."""
        (self.store / 'unexpected').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            verify_store(self.store, self.record_path)
        (self.store / 'unexpected').unlink()
        (self.store / 'zarr.json').unlink()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            verify_store(self.store, self.record_path)

    def test_sidecar_provenance_mismatch_is_rejected(self):
        """Changing a source identity in the sidecar cannot silently relabel a store."""
        self.record['product'] = 'different'
        self.record_path.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            verify_store(self.store, self.record_path)

    def test_legacy_store_requires_preparation(self):
        """A record without store hashes must be regenerated rather than trusted on first read."""
        self.record.pop('store_sha256')
        self.record_path.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, 'regenerate preparation'):
            verify_store(self.store, self.record_path)

    def test_existing_outputs_are_preserved(self):
        """Neither publication nor record writing may replace an existing artifact."""
        before = self.record_path.read_bytes()
        with self.assertRaises(FileExistsError):
            publish_zarr(self.dataset, self.store)
        with self.assertRaises(FileExistsError):
            write_json(self.record_path, {'changed': True})
        self.assertEqual(self.record_path.read_bytes(), before)
        verify_store(self.store, self.record_path)


if __name__ == "__main__":
    unittest.main()
