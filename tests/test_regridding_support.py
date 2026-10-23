"""Synthetic R04 strict native boundaries and aligned target-mask checks."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import h5py
import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from nisar.observations import GRID, window_layout, read_window
from regridding.support import METHODS, comparison_masks, coverage


class SupportTests(unittest.TestCase):
    def test_strict_native_window_retains_partial_cells_without_halo(self):
        """Exclude edge-only neighbors, but keep native cells with any positive overlap."""
        with tempfile.TemporaryDirectory() as directory:
            with h5py.File(Path(directory) / "grid.h5", "w") as product:
                grid = product.create_group(GRID)
                for name, value in {"xCoordinateSpacing": 200., "yCoordinateSpacing": -200.,
                                    "projection": 6933, "EASEGridRowIndex": [0, 1, 2],
                                    "EASEGridColumnIndex": [0, 1, 2],
                                    "xCoordinates": [0., 200., 400.],
                                    "yCoordinates": [400., 200., 0.]}.items():
                    grid[name] = value
                grid["field"] = np.arange(9, dtype=float).reshape(3, 3)
                exact = window_layout(SimpleNamespace(projected=box(100, 100, 300, 300),
                                                      bounds=(0, 0, 1, 1)), product)
                self.assertEqual((exact["r0"], exact["r1"], exact["c0"], exact["c1"]),
                                 (1, 2, 1, 2))
                np.testing.assert_array_equal(read_window(grid, "field", exact,
                                                         np.arange(3), np.arange(3)), [[4]])
                partial = window_layout(SimpleNamespace(projected=box(110, 110, 310, 310),
                                                        bounds=(0, 0, 1, 1)), product)
                self.assertEqual((partial["r0"], partial["r1"], partial["c0"], partial["c1"]),
                                 (0, 2, 1, 3))
                self.assertTrue(partial["mask"].all())

    def test_separate_common_masks_keep_moisture_without_uncertainty(self):
        """Missing uncertainty shrinks only its cohort; preserve full method masks and zeros."""
        moisture = {name: np.array([.2, .3, .4, .5]) for name in METHODS}
        uncertainty = {name: np.array([0., .1, .1, .1]) for name in METHODS}
        moisture["nearest"][2] = np.nan
        uncertainty["bilinear"][1] = np.nan
        result = comparison_masks(moisture, uncertainty, [True, True, True, False])
        np.testing.assert_array_equal(result["moisture_common"], [True, True, False, False])
        np.testing.assert_array_equal(result["uncertainty_common"], [True, False, False, False])
        np.testing.assert_array_equal(result["per_method"]["bilinear"]["moisture"],
                                      [True, True, True, False])
        self.assertEqual(moisture["nearest"][3], .5)

    def test_empty_common_support_does_not_fall_back_to_pairwise(self):
        """Disjoint valid outputs stay empty, without affecting another independent call."""
        moisture = {name: np.eye(4)[index].copy() for index, name in enumerate(METHODS)}
        moisture = {name: np.where(values == 1, .2, np.nan) for name, values in moisture.items()}
        uncertainty = {name: np.full(4, .1) for name in METHODS}
        self.assertFalse(comparison_masks(moisture, uncertainty, [True] * 4)["moisture_common"].any())
        independent = {name: np.full(4, .2) for name in METHODS}
        self.assertTrue(comparison_masks(independent, uncertainty, [True] * 4)["moisture_common"].all())

    def test_count_and_area_denominators_include_center_exclusions(self):
        """Unequal clipped areas give distinct coverage, with outside centers in denominator."""
        result = coverage([True, False, True, True], [8100, 4050, 2700, 0],
                          [True, True, False, False])
        self.assertEqual(result["intersecting_cells"], 3)
        self.assertEqual(result["valid_cells"], 1)
        self.assertEqual(result["center_excluded_cells"], 1)
        self.assertAlmostEqual(result["count_coverage_percent"], 100 / 3)
        self.assertAlmostEqual(result["area_coverage_percent"], 100 * 8100 / 14850)
        self.assertEqual(result["valid_area_m2"], 8100)

    def test_empty_domain_has_undefined_coverage_not_zero_percent(self):
        """Distinguish no denominator from zero valid coverage of an existing domain."""
        empty = coverage([False], [0], [False])
        self.assertIsNone(empty["count_coverage_percent"])
        self.assertIsNone(empty["area_coverage_percent"])
        missing = coverage([False], [8100], [True])
        self.assertEqual(missing["count_coverage_percent"], 0)
        self.assertEqual(missing["area_coverage_percent"], 0)

    def test_incomplete_methods_and_misalignment_are_rejected(self):
        """Prevent incomplete comparisons, silent broadcasting, and invalid area masks."""
        data = {name: [.2] for name in METHODS}
        with self.assertRaisesRegex(ValueError, "exactly"):
            comparison_masks({"nearest": [.2]}, data, [True])
        with self.assertRaisesRegex(ValueError, "shape"):
            comparison_masks(data, data, [True, True])
        with self.assertRaisesRegex(ValueError, "boolean"):
            comparison_masks(data, data, [1])
        for areas, centers in (([-1], [True]), ([np.nan], [True]), ([1], [1]),
                               ([1, 2], [True])):
            with self.subTest(areas=areas, centers=centers), self.assertRaises(ValueError):
                coverage([True], areas, centers)


if __name__ == "__main__":
    unittest.main()
