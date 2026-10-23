"""Verify timestamp matching, selected-method pairing and containing-cell lookup."""

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insitu.validation import containing_cell, paired_rmse
from insitu.matching import nearest_label


class SelectedValidationTests(unittest.TestCase):
    """Exercise support alignment without private station or satellite data."""

    def test_containing_cell_uses_station_point_on_each_grid(self):
        """Select half-open cells from projected point coordinates at grid edges."""
        x_bounds = np.asarray([[0., 2.], [2., 4.]])
        y_bounds = np.asarray([[4., 2.], [2., 0.]])
        self.assertEqual(containing_cell(2., 2., x_bounds, y_bounds), (1, 1))
        self.assertEqual(containing_cell(1., 1., x_bounds, y_bounds), (1, 0))
        with self.assertRaises(ValueError):
            containing_cell(4., 1., x_bounds, y_bounds)

    def test_common_pairs_exclude_missing_method_without_regression(self):
        """Only native, nearest and bilinear availability sets the score cohort."""
        rows = [
            dict(sensor_moisture_m3_m3=.2, native_200m=.3, nearest=.25, bilinear=.22),
            dict(sensor_moisture_m3_m3=.4, native_200m=.5, nearest=.45, bilinear=None),
            dict(sensor_moisture_m3_m3=None, native_200m=.5, nearest=.45, bilinear=.42),
        ]
        score = paired_rmse(rows)
        self.assertEqual(score["pairs"], 1)
        self.assertAlmostEqual(score["methods"]["native_200m"]["rmse_m3_m3"], .1)
        self.assertAlmostEqual(score["methods"]["bilinear"]["rmse_m3_m3"], .02)


class MatchingTests(unittest.TestCase):
    def test_ten_minute_sensitivity_preserves_default(self):
        """The requested tighter tolerance accepts ten minutes and leaves default 30 intact."""
        times = pd.to_datetime(['2026-06-17T03:00Z'])
        ten = pd.Timedelta(minutes=10)
        self.assertEqual(nearest_label(times, times[0]+ten, tolerance=ten), (0, 'matched'))
        later = times[0]+ten+pd.Timedelta(microseconds=1)
        self.assertEqual(nearest_label(times, later, tolerance=ten), (None, 'outside_tolerance'))
        self.assertEqual(nearest_label(times, later), (0, 'matched'))

    def test_inclusive_tolerance_and_no_extrapolation(self):
        """Accept exactly 30 minutes, reject even one microsecond beyond."""
        times = pd.to_datetime(['2026-06-17T03:00Z'])
        self.assertEqual(nearest_label(times, times[0]+pd.Timedelta(minutes=30)), (0, 'matched'))
        self.assertEqual(nearest_label(times, times[0]+pd.Timedelta(minutes=30, microseconds=1)), (None, 'outside_tolerance'))

    def test_tie_not_silently_resolved(self):
        """An equally distant pair remains a decision, including irregular labels."""
        times = pd.to_datetime(['2026-06-17T03:00Z', '2026-06-17T03:20Z'])
        self.assertEqual(nearest_label(times, times[0]+pd.Timedelta(minutes=10)), (None, 'tie_requires_decision'))

    def test_nearest_does_not_filter_missing_values(self):
        """Nearest position includes the missing row instead of a farther finite row."""
        table = pd.Series([float('nan'), .2], index=pd.to_datetime(['2026-06-17T03:00Z', '2026-06-17T03:20Z']))
        index, status = nearest_label(table.index, table.index[0]+pd.Timedelta(minutes=5))
        self.assertEqual(status, 'matched')
        self.assertTrue(pd.isna(table.iloc[index]))

    def test_invalid_chronology_rejected(self):
        """Reject duplicate and zone-less inputs instead of guessing chronology."""
        for times in (pd.to_datetime(['2026-06-17', '2026-06-17'], utc=True), pd.to_datetime(['2026-06-17'])):
            with self.assertRaises(ValueError):
                nearest_label(times, pd.Timestamp('2026-06-17', tz='UTC'))


if __name__ == "__main__":
    unittest.main()
