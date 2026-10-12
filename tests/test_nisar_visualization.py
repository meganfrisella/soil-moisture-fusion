"""Offline checks for general SME2 discovery, caching, native coverage and rendering."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar import catalog, quality
from nisar import geometry
from nisar import observations as sme2
from nisar import discovery
from nisar_visualization import gif as drawing
from nisar_visualization import topographic as topo
import visualize_nisar_sme2 as workflow
from nisar_visualization import workflow as frames_workflow
from test_data_downloads import catalog_item


def write_granule(path, columns=(0, 1, 2), timestamp='2026-01-01T01:00:00'):
    """Write a small, partially overlapping native grid with PMI only."""
    with h5py.File(path, 'w') as f:
        g = f.create_group(sme2.GRID)
        for key, value in dict(xCoordinates=-400 + np.array(columns) * 200,
                               yCoordinates=[200, 0, -200],
                               EASEGridRowIndex=[0, 1, 2], EASEGridColumnIndex=columns,
                               xCoordinateSpacing=200, yCoordinateSpacing=-200,
                               projection=6933).items():
            g.create_dataset(key, data=value)
        shape = (3, len(columns))
        for key, value in dict(soilMoisture=np.full(shape, .2),
                               soilMoistureUncertainty=np.full(shape, -9999.),
                               retrievalQualityFlag=np.ones(shape)).items():
            d = g.create_dataset('algorithmCandidates/PMI/' + key, data=value)
            d.attrs['_FillValue'] = -9999
        identity = f.create_group('science/LSAR/identification')
        for key, value in dict(zeroDopplerStartTime=timestamp, zeroDopplerEndTime=timestamp,
                               trackNumber=1, frameNumber=2, absoluteOrbitNumber=3,
                               orbitPassDirection='Ascending', productVersion='test',
                               productSpecificationVersion='test', compositeReleaseId='test',
                               processingDateTime=timestamp).items():
            identity.create_dataset(key, data=value)


def small_box():
    """Define a 5-column, 3-row window that avoids exact cell-edge rounding ties."""
    west, south = geometry.INVERSE.transform(-490, -290)
    east, north = geometry.INVERSE.transform(490, 290)
    return geometry.BoundingBox(west, south, east, north)


def fake_basemap(address):
    """Serve a deterministic synthetic basemap and ArcGIS metadata without network."""
    if '/export?' in address:
        q = parse_qs(urlparse(address).query)
        bounds = list(map(float, q['bbox'][0].split(',')))
        size = tuple(map(int, q['size'][0].split(',')))
        return json.dumps({'extent': dict(zip(('xmin', 'ymin', 'xmax', 'ymax'), bounds),
                                         spatialReference={'wkid': 6933}),
                           'href': f'https://example.org/image/{size[0]}/{size[1]}'}).encode()
    if '/image/' in address:
        width, height = map(int, address.rsplit('/', 2)[-2:])
        pixels = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
        image = Image.fromarray(pixels).convert('RGB')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue()
    return json.dumps({'copyrightText': 'Synthetic test basemap'}).encode()


class CatalogTests(unittest.TestCase):
    def test_complete_search_after_and_optional_dates(self):
        """Follow every page, send WGS84 bounds and leave the default date range unbounded."""
        a, b = catalog_item(), catalog_item(name='NISAR_SECOND')
        get_page = Mock(side_effect=[({'hits': 2, 'items': [a]}, 'next'),
                                     ({'hits': 2, 'items': [b]}, None)])
        granules, provenance = discovery.search_box((-10, -20, 10, 20), get_page=get_page)
        self.assertEqual(len(granules), 2)
        query = parse_qs(urlparse(provenance['catalog_query']).query)
        self.assertNotIn('temporal', query)
        self.assertEqual(query['bounding_box'], ['-10,-20,10,20'])
        self.assertEqual(query['short_name'], ['NISAR_L3_SME2_PROVISIONAL_*'])
        self.assertEqual(get_page.call_args_list[1].args[1], 'next')
        for side_effect in [
            [({'hits': 2, 'items': [a]}, None)],
            [({'hits': 2, 'items': [a]}, 'x'), ({'hits': 2, 'items': [a]}, 'x')],
            [({'hits': 2, 'items': [a]}, 'x'), ({'hits': 3, 'items': [b]}, None)],
        ]:
            with self.assertRaises(RuntimeError):
                discovery.search_box((-10, -20, 10, 20), get_page=Mock(side_effect=side_effect))
        low, high = discovery.date_limits('2026-01-01', '2026-01-01')
        self.assertEqual((high - low).days, 1)
        self.assertEqual(discovery.date_limits(), (None, None))

    def test_cached_products_need_no_auth_and_corruption_is_preserved(self):
        """Verify cache before authentication; reject corrupt existing data without overwriting."""
        with tempfile.TemporaryDirectory() as folder:
            item = catalog_item()
            granule = catalog.parse_granule(item)
            path = Path(folder) / granule.name
            from test_data_downloads import PAYLOAD
            path.write_bytes(PAYLOAD)
            with patch.object(catalog, 'earthdata_opener') as auth:
                self.assertEqual(list(discovery.ensure_granules([granule], folder)), [path])
                auth.assert_not_called()
                path.write_bytes(b'corrupt')
                with self.assertRaises(ValueError):
                    list(discovery.ensure_granules([granule], folder))
                auth.assert_not_called()
                self.assertEqual(path.read_bytes(), b'corrupt')

    def test_missing_product_downloads_once_to_passed_directory(self):
        """A missing granule is downloaded and verified once, then reused without authentication."""
        from test_data_downloads import download_response, PAYLOAD
        granule = catalog.parse_granule(catalog_item())
        with tempfile.TemporaryDirectory() as folder:
            opener = Mock()
            opener.open.return_value = download_response()
            with patch.object(catalog, 'earthdata_opener', return_value=opener) as auth:
                paths = list(discovery.ensure_granules([granule], folder))
                self.assertEqual(paths[0].parent, Path(folder))
                self.assertEqual(paths[0].read_bytes(), PAYLOAD)
                self.assertEqual(list(discovery.ensure_granules([granule], folder)), paths)
                auth.assert_called_once()
                opener.open.assert_called_once()


class WindowTests(unittest.TestCase):
    def test_corners_and_hemispheres_are_not_region_specific(self):
        """Validate corner order and support east longitudes and southern latitudes."""
        view = geometry.BoundingBox.from_corners((-30, 140), (-31, 141))
        self.assertEqual(view.bounds, (140, -31, 141, -30))
        self.assertIn('S', workflow.bounds_label(view))
        self.assertIn('E', workflow.bounds_label(view))
        for corners in [((1, 2), (2, 3)), ((1, 170), (-1, -170)), ((87, 0), (80, 1))]:
            with self.assertRaises(ValueError):
                geometry.BoundingBox.from_corners(*corners)

    def test_full_box_denominator_and_every_same_day_granule(self):
        """Partial native grids are padded, missing algorithms stay missing, and dates are not collapsed."""
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'{i}.h5' for i in range(3)]
            write_granule(paths[0], columns=(0, 1, 2))
            write_granule(paths[1], columns=(2, 3, 4))
            write_granule(paths[2], columns=(0, 1))
            with h5py.File(paths[0]) as f:
                layout = sme2.window_layout(small_box(), f)
            self.assertEqual(layout['mask'].sum(), 15)
            frames = frames_workflow.GranuleFrames(paths, layout, combine_window=0)
            self.assertEqual(len(frames), 2)
            first = next(iter(frames))
            self.assertEqual(quality.coverage_counts(first, layout['mask']), {'PMI': 9, 'TSR': 0, 'DSG': 0})
            self.assertTrue(np.isnan(first['data']['PMI']['soilMoisture'][:, 3:]).all())
            self.assertTrue(np.isnan(first['data']['PMI']['soilMoistureUncertainty']).all())
            self.assertFalse(first['data']['TSR']['available'])
            self.assertFalse(quality.qualifies({'PMI': 5, 'TSR': 5}, 10))
            self.assertTrue(quality.qualifies({'PMI': 6, 'TSR': 0}, 10))
            self.assertFalse(quality.qualifies({'PMI': 9}, 10, 90))
            self.assertTrue(quality.qualifies({'PMI': 10}, 10, 90))
            self.assertFalse(quality.qualifies({'PMI': 0}, 10, 0))

    def test_endpoint_overflow_and_streamed_gif_metadata(self):
        """Both overflow colors match endpoints and every granule remains a separate GIF frame."""
        for cmap in drawing.color_maps():
            np.testing.assert_allclose(cmap(-1.), cmap(0.))
            np.testing.assert_allclose(cmap(2.), cmap(1.))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.h5'
            write_granule(path)
            with h5py.File(path) as f:
                layout = sme2.window_layout(small_box(), f)
            frame = sme2.read_frame(path, layout)
            output = Path(folder) / 'result.gif'
            drawing.render_gif(layout, [frame, frame], output, 'Synthetic', duration_ms=120)
            with Image.open(output) as image:
                self.assertEqual(image.n_frames, 2)
                info = json.loads(image.info['comment'])
                self.assertEqual(len(info['frames']), 2)
                self.assertEqual(info['moisture_limits'], [0, .6])
                for i in range(2):
                    image.seek(i)
                    self.assertEqual(image.info['duration'], 120)
            self.assertFalse(output.with_suffix('.gif.tmp').exists())

    def test_cli_uses_cache_and_writes_only_requested_visuals(self):
        """Redirect both visuals with --output-dir while retaining the prefix filename and input cache."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'NISAR_SYNTHETIC.h5'
            write_granule(source)
            item = catalog_item(name=source.stem, start='2026-01-01T01:00:00Z')
            item['umm']['DataGranule']['ArchiveAndDistributionInformation'][0]['Checksum'] = {
                'Algorithm': 'SHA-256', 'Value': hashlib.sha256(source.read_bytes()).hexdigest()}
            granule = catalog.parse_granule(item)
            view = small_box()
            output_dir = root / 'visuals'
            args = ['--top-left', str(view.north), str(view.west), '--bottom-right', str(view.south),
                    str(view.east), '--data-dir', str(root), '--output-prefix', str(root / 'visual'),
                    '--output-dir', str(output_dir),
                    '--basemap-size', '100']
            with patch.object(workflow, 'search_box', return_value=([granule], {})), \
                    patch.object(catalog, 'earthdata_opener') as auth, \
                    patch.object(topo, 'fetch', side_effect=fake_basemap):
                self.assertEqual(workflow.main(args), 0)
                auth.assert_not_called()
            self.assertEqual({p.name for p in root.iterdir()},
                             {source.name, 'visuals'})
            self.assertEqual({p.name for p in output_dir.iterdir()}, {'visual.gif', 'visual_topographic.png'})
            with Image.open(output_dir / 'visual_topographic.png') as image:
                info = json.loads(image.info['Description'])
                np.testing.assert_allclose(info['bounds_wgs84'], view.bounds, atol=1e-8)
            defaults = workflow.parser().parse_args(args[:6])
            self.assertEqual(defaults.min_numeric_percent, 50)
            self.assertEqual(defaults.nisar_combine_window, 10)
            self.assertEqual(defaults.data_dir, ROOT / 'data/nisar')


class CombineTests(unittest.TestCase):
    def test_window_is_inclusive_bounded_and_track_specific(self):
        """Group by track across midnight, include the endpoint, and reject gap chaining."""
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'{i}.h5' for i in range(6)]
            times = ['2026-01-01T23:55:00', '2026-01-02T00:03:00',
                     '2026-01-02T00:05:00', '2026-01-02T00:11:00',
                     '2026-01-01T23:56:00', '2026-01-01T23:55:00Z']
            for path, time in zip(paths, times):
                write_granule(path, timestamp=time)
            with h5py.File(paths[4], 'a') as f:
                f['science/LSAR/identification/trackNumber'][...] = 2
            groups = frames_workflow.group_paths(reversed(paths), 10)
            self.assertEqual(groups, [[paths[0], paths[5], paths[1], paths[2]], [paths[4]], [paths[3]]])
            self.assertEqual(len(frames_workflow.group_paths(paths, 0)), 6)
            self.assertEqual(len(frames_workflow.group_paths(paths, .5)), 5)

    def test_combination_precedes_coverage_and_keeps_originals(self):
        """Two 60%-covered native granules together exceed 80% without creating a new HDF5."""
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'{i}.h5' for i in range(2)]
            write_granule(paths[0], columns=(0, 1, 2))
            write_granule(paths[1], columns=(2, 3, 4), timestamp='2026-01-01T01:05:00')
            originals = [path.read_bytes() for path in paths]
            with h5py.File(paths[0]) as f:
                layout = sme2.window_layout(small_box(), f)
            self.assertEqual(len(frames_workflow.GranuleFrames(paths, layout, 80, 0)), 0)
            frames = frames_workflow.GranuleFrames(paths, layout, 80)
            self.assertEqual(len(frames), 1)
            combined = next(iter(frames))
            self.assertEqual(quality.coverage_counts(combined, layout['mask'])['PMI'], 15)
            self.assertEqual([s['source_pixels'] for s in combined['metadata']['source_granules']], [9, 6])
            self.assertEqual(combined['metadata']['zeroDopplerEndTime'], '2026-01-01T01:05:00')
            self.assertEqual([path.read_bytes() for path in paths], originals)
            self.assertEqual(set(Path(folder).iterdir()), set(paths))

    def test_overlap_preserves_measurement_tuples_per_algorithm(self):
        """Earliest finite moisture keeps missing uncertainty/odd flags; later data fills gaps only."""
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'{i}.h5' for i in range(2)]
            for i, path in enumerate(paths):
                write_granule(path, timestamp=f'2026-01-01T01:0{i}:00')
                with h5py.File(path, 'a') as f:
                    g = f[sme2.GRID]
                    g['algorithmCandidates/PMI/soilMoisture'][...] = .2 + i * .1
                    if i == 0:
                        g['algorithmCandidates/PMI/soilMoisture'][0, 0] = -9999
                        g['algorithmCandidates/PMI/soilMoisture'][0, 1] = -9999
                    else:
                        g['algorithmCandidates/PMI/soilMoistureUncertainty'][...] = .04
                        g['algorithmCandidates/PMI/retrievalQualityFlag'][...] = 4
                        g['algorithmCandidates/PMI/soilMoisture'][0, 1] = -9999
                        g.copy('algorithmCandidates/PMI', 'algorithmCandidates/TSR')
            with h5py.File(paths[0]) as f:
                layout = sme2.window_layout(small_box(), f)
            frame = next(iter(frames_workflow.GranuleFrames(reversed(paths), layout)))
            pmi, tsr = frame['data']['PMI'], frame['data']['TSR']
            self.assertAlmostEqual(pmi['soilMoisture'][0, 0], .3)
            self.assertEqual(pmi['soilMoistureUncertainty'][0, 0], .04)
            self.assertEqual(pmi['retrievalQualityFlag'][0, 0], 4)
            self.assertAlmostEqual(pmi['soilMoisture'][1, 1], .2)
            self.assertTrue(np.isnan(pmi['soilMoistureUncertainty'][1, 1]))
            self.assertEqual(pmi['retrievalQualityFlag'][1, 1], 1)
            self.assertTrue(np.isnan(pmi['soilMoisture'][0, 1]))
            self.assertEqual(pmi['retrievalQualityFlag'][0, 1], 1)
            self.assertTrue(tsr['available'])
            self.assertAlmostEqual(tsr['soilMoisture'][1, 1], .3)
            self.assertTrue(np.isnan(frame['data']['DSG']['soilMoisture']).all())
            self.assertTrue(np.isnan(pmi['soilMoisture'][:, 3:]).all())

    def test_incompatible_native_grids_are_rejected(self):
        """Combining never silently resamples a misaligned native grid."""
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f'{i}.h5' for i in range(2)]
            for path in paths:
                write_granule(path)
            with h5py.File(paths[0]) as f:
                layout = sme2.window_layout(small_box(), f)
            with h5py.File(paths[1], 'a') as f:
                f[sme2.GRID + '/xCoordinates'][...] += 1
            with self.assertRaisesRegex(ValueError, 'alignment differs'):
                frames_workflow.GranuleFrames(paths, layout)

    def test_invalid_window_rejected_before_network(self):
        """Negative and nonfinite combine windows fail CLI validation before catalog access."""
        for value in ('-1', 'nan', 'inf', '-inf'):
            with self.subTest(value=value), patch.object(workflow, 'search_box') as search, \
                    patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    workflow.main(['--top-left', '47', '-120', '--bottom-right', '46', '-119',
                                   '--nisar-combine-window=' + value])
                self.assertEqual(error.exception.code, 2)
                search.assert_not_called()


if __name__ == '__main__':
    unittest.main()
