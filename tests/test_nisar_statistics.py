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
from test_nisar_visualization import write_granule, small_box
import visualize_nisar_sme2 as visualization
from nisar.selection import GranuleFrames


def frame(name, start, values, flags):
    """Make a four-cell frame, leaving unspecified candidates entirely missing."""
    return {
        'metadata': {'source_granules': [{'granule': name, 'zeroDopplerStartTime': start,
                                         'zeroDopplerEndTime': start}]},
        'data': {a: {'soilMoisture': np.array(values.get(a, [np.nan] * 4)),
                     'retrievalQualityFlag': np.array(flags.get(a, [np.nan] * 4))}
                 for a in ALGORITHMS},
    }


class StatisticsTests(unittest.TestCase):
    def test_intersecting_denominator_and_cached_cli_report(self):
        """Count partial edge cells and missing cells beyond a granule exactly as the viewer does."""
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
            self.assertEqual(intersection['mask'].sum(), 12)
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
            self.assertEqual(report['coverage_denominator_cells'], 12)
            self.assertEqual(report['selected_granules'], 1)
            self.assertAlmostEqual(report['area_km2'], .28)
            self.assertEqual(report['algorithms']['PMI']['mean_numeric_percent'], 75)
            self.assertEqual(report['algorithms']['PMI']['mean_recommended_percent'], 75)
            self.assertEqual(report['algorithms']['DSG']['mean_numeric_percent'], 0)
            self.assertEqual({p.name for p in output.iterdir()}, {'sme2_statistics.json'})

    def test_candidate_flags_and_per_frame_means(self):
        """Weight frames equally despite different source counts; accept even flags and reject missing flags."""
        frames = [
            frame('a.h5', '2026-01-01T01:00:00Z', {'PMI': [.2, .2, .2, np.nan]},
                  {'PMI': [0, 4, 1, 0]}),
            frame('b.h5', '2026-01-01T02:00:00', {'TSR': [.2] * 4, 'PMI': [.2] * 4},
                  {'TSR': [4, 5, np.nan, -9999], 'PMI': [0] * 4}),
            frame('c.h5', '2026-01-02T01:00:00Z', {'DSG': [.2] * 4}, {'DSG': [0] * 4}),
        ]
        frames[0]['metadata']['source_granules'].append(
            {'granule': 'a2.h5', 'zeroDopplerStartTime': '2026-01-01T01:05:00Z',
             'zeroDopplerEndTime': '2026-01-01T01:05:00Z'})
        result = statistics.summarize_frames(iter(frames), np.ones(4, bool))
        self.assertEqual(result['selected_granules'], 4)
        self.assertEqual(result['selected_frames'], 3)
        self.assertEqual(result['distinct_utc_dates'], 2)
        self.assertEqual(result['acquisition_end_utc'], '2026-01-02T01:00:00+00:00')
        for algorithm, numeric, recommended in [('PMI', 175 / 3, 50),
                                                 ('TSR', 100 / 3, 25 / 3),
                                                 ('DSG', 100 / 3, 100 / 3)]:
            self.assertAlmostEqual(result['algorithms'][algorithm]['mean_numeric_percent'], numeric)
            self.assertAlmostEqual(result['algorithms'][algorithm]['mean_recommended_percent'], recommended)

    def test_mask_and_empty_selection(self):
        """Ignore values outside the supplied mask and keep empty-selection means null."""
        data = frame('a.h5', '2026-01-01T00:00:00Z', {'PMI': [np.nan, np.nan, .2, .2]},
                     {'PMI': [0] * 4})
        masked = statistics.summarize_frames([data], np.array([True, True, False, False]))
        self.assertEqual(masked['algorithms']['PMI']['mean_numeric_percent'], 0)
        result = statistics.summarize_frames([], np.ones(4, bool))
        self.assertEqual(result['selected_granules'], 0)
        self.assertEqual(result['distinct_utc_dates'], 0)
        self.assertIsNone(result['acquisition_start_utc'])
        self.assertIsNone(result['algorithms']['PMI']['mean_numeric_percent'])
        with self.assertRaisesRegex(ValueError, 'no selected native cells'):
            statistics.summarize_frames([], np.zeros(4, bool))

    def test_combined_cli_matches_visualization_selection(self):
        """Both CLIs select identical combined tuples, reject equality, and support disabling combining."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            view = small_box()
            granules = []
            for i, columns in enumerate(((0, 1, 2), (2, 3, 4))):
                source = root / f'NISAR_TEST_{i}.h5'
                write_granule(source, columns, timestamp='2026-01-01T23:55:00' if i == 0 else '2026-01-02T00:05:00')
                with h5py.File(source, 'a') as f:
                    f[observations.GRID]['algorithmCandidates/PMI/retrievalQualityFlag'][...] = 1 if i == 0 else 4
                item = catalog_item(name=source.stem)
                item['umm']['DataGranule']['ArchiveAndDistributionInformation'][0]['Checksum'] = {
                    'Algorithm': 'SHA-256', 'Value': hashlib.sha256(source.read_bytes()).hexdigest()}
                granules.append(catalog.parse_granule(item))
            args = ['--top-left', str(view.north), str(view.west), '--bottom-right', str(view.south),
                    str(view.east), '--data-dir', str(root), '--min-numeric-percent', '80',
                    '--nisar-combine-window', '10', '--output-dir', str(root / 'summary')]
            with patch.object(statistics, 'search_box', return_value=(granules, {})):
                self.assertEqual(statistics.main(args), 0)
            with patch.object(visualization, 'search_box', return_value=(granules, {})), \
                    patch.object(visualization, 'render_gif') as render:
                self.assertEqual(visualization.main(args + ['--formats', 'gif']), 0)
            layout, frames = render.call_args.args[:2]
            report = json.loads((root / 'summary/sme2_statistics.json').read_text())
            for key, value in statistics.summarize_frames(frames, layout['mask']).items():
                self.assertEqual(report[key], value)
            self.assertEqual(report['selected_granules'], 2)
            self.assertEqual(report['selected_frames'], 1)
            self.assertEqual(report['distinct_utc_dates'], 2)
            self.assertEqual(report['algorithms']['PMI']['mean_numeric_percent'], 100)
            self.assertEqual(report['algorithms']['PMI']['mean_recommended_percent'], 40)
            paths = [root / g.name for g in granules]
            self.assertEqual(len(GranuleFrames(paths, layout, 60, 0)), 0)
            self.assertEqual(len(GranuleFrames(paths, layout, 59, 0)), 2)
            for module in (statistics, visualization):
                defaults = module.parser().parse_args(args[:6])
                self.assertEqual(defaults.min_numeric_percent, 50)
                self.assertEqual(defaults.nisar_combine_window, 10)


if __name__ == '__main__':
    unittest.main()
