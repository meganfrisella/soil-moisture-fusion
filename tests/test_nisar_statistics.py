"""Offline SME2 coverage statistics with synthetic native cells and candidate flags."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
import summarize_nisar_sme2 as statistics
from nisar.quality import ALGORITHMS
from nisar import catalog, geometry, observations
from test_data_downloads import catalog_item
from test_nisar_visualization import write_granule


def frame(name, start, values, flags):
    """Make a four-cell frame, leaving unspecified candidates entirely missing."""
    return {
        'metadata': {'granule': name, 'zeroDopplerStartTime': start,
                     'zeroDopplerEndTime': start},
        'data': {a: {'soilMoisture': np.array(values.get(a, [np.nan] * 4)),
                     'retrievalQualityFlag': np.array(flags.get(a, [np.nan] * 4))}
                 for a in ALGORITHMS},
    }


class StatisticsTests(unittest.TestCase):
    def test_center_denominator_and_cached_cli_report(self):
        """Exclude edge-intersecting centers outside the box and retain missing centers beyond a granule."""
        west, south = geometry.INVERSE.transform(-450, -250)
        east, north = geometry.INVERSE.transform(250, 150)
        view = geometry.BoundingBox(west, south, east, north)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'NISAR_SYNTHETIC.h5'
            write_granule(source)
            with h5py.File(source, 'a') as f:
                g = f[observations.GRID]
                g['algorithmCandidates/PMI/retrievalQualityFlag'][...] = 4
                g.create_dataset('retrievalQualityFlag', data=np.ones((3, 3)))
                intersection = observations.window_layout(view, f)
                centers = statistics.center_layout(view, f)
            self.assertEqual(intersection['mask'].sum(), 12)
            self.assertEqual(centers['mask'].sum(), 8)
            item = catalog_item(name=source.stem, start='2026-01-01T01:00:00Z')
            item['umm']['DataGranule']['ArchiveAndDistributionInformation'][0]['Checksum'] = {
                'Algorithm': 'SHA-256', 'Value': hashlib.sha256(source.read_bytes()).hexdigest()}
            granule = catalog.parse_granule(item)
            output = root / 'reports'
            args = ['--top-left', str(north), str(west), '--bottom-right', str(south), str(east),
                    '--data-dir', str(root), '--output-dir', str(output)]
            with patch.object(statistics, 'search_box', return_value=([granule], {'catalog_granules': 1})), \
                    patch.object(catalog, 'earthdata_opener') as auth:
                self.assertEqual(statistics.main(args), 0)
                auth.assert_not_called()
            report = json.loads((output / 'sme2_statistics.json').read_text())
            self.assertEqual(report['native_pixel_centers'], 8)
            self.assertEqual(report['selected_granules'], 1)
            self.assertAlmostEqual(report['area_km2'], .28)
            self.assertEqual(report['algorithms']['PMI']['mean_numeric_percent'], 75)
            self.assertEqual(report['algorithms']['PMI']['mean_recommended_percent'], 75)
            self.assertEqual(report['algorithms']['DSG']['mean_numeric_percent'], 0)
            self.assertEqual({p.name for p in output.iterdir()}, {'sme2_statistics.json'})

    def test_strict_selection_candidate_flags_and_per_granule_means(self):
        """Select by any candidate, reject exactly 50%, and give same-day granules equal weight."""
        frames = [
            frame('a.h5', '2026-01-01T01:00:00Z', {'PMI': [.2, .2, .2, np.nan]},
                  {'PMI': [0, 4, 1, 0]}),
            frame('b.h5', '2026-01-01T02:00:00', {'TSR': [.2] * 4, 'PMI': [.2] * 4},
                  {'TSR': [4, 5, np.nan, -9999], 'PMI': [0] * 4}),
            frame('c.h5', '2026-01-02T01:00:00Z', {'DSG': [.2] * 4}, {'DSG': [0] * 4}),
            frame('excluded.h5', '2026-01-03T01:00:00Z', {'PMI': [.2, .2, np.nan, np.nan]},
                  {'PMI': [0] * 4}),
        ]
        result = statistics.summarize_frames(iter(frames), np.ones(4, bool), 50)
        self.assertEqual(result['selected_granules'], 3)
        self.assertEqual(result['distinct_utc_dates'], 2)
        self.assertEqual(result['acquisition_end_utc'], '2026-01-02T01:00:00+00:00')
        for algorithm, numeric, recommended in [('PMI', 175 / 3, 50),
                                                 ('TSR', 100 / 3, 25 / 3),
                                                 ('DSG', 100 / 3, 100 / 3)]:
            self.assertAlmostEqual(result['algorithms'][algorithm]['mean_numeric_percent'], numeric)
            self.assertAlmostEqual(result['algorithms'][algorithm]['mean_recommended_percent'], recommended)

    def test_mask_and_empty_selection(self):
        """Cells outside the supplied region do not qualify a granule; empty means remain null."""
        data = frame('a.h5', '2026-01-01T00:00:00Z', {'PMI': [np.nan, np.nan, .2, .2]},
                     {'PMI': [0] * 4})
        result = statistics.summarize_frames([data], np.array([True, True, False, False]), 0)
        self.assertEqual(result['selected_granules'], 0)
        self.assertEqual(result['distinct_utc_dates'], 0)
        self.assertIsNone(result['acquisition_start_utc'])
        self.assertIsNone(result['algorithms']['PMI']['mean_numeric_percent'])
        with self.assertRaisesRegex(ValueError, 'no selected native cells'):
            statistics.summarize_frames([], np.zeros(4, bool), 50)


if __name__ == '__main__':
    unittest.main()
