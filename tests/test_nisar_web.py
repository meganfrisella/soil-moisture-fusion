"""Offline tests for individual-granule HTML controls, native pixels and footprint reuse."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar_visualization import web
from nisar import geometry
from nisar import observations as sme2
from nisar import catalog
import visualize_nisar_sme2 as workflow
from nisar_visualization import workflow as frames_workflow
from nisar_visualization.workflow import frame_tracks
from maps.nisar_footprints import contributing_tracks
from maps.common import read_manifest, BASEMAP_TILES
from nisar_visualization.style import color_maps
from test_nisar_visualization import write_granule, small_box
from test_data_downloads import catalog_item


def fixture(folder, combine=False):
    """Two same-day native granules with different candidates and catalog footprints."""
    view = small_box()
    paths, granules = [], []
    for i in range(2):
        path = folder / f'NISAR_WEB_{i}.h5'
        timestamp = f'2026-01-01T01:0{i * 5}:00' if combine else f'2026-01-01T0{i+1}:00:00'
        columns = (2, 3, 4) if combine and i else (0, 1, 2)
        frame_number = i + 2 if combine else 2
        write_granule(path, columns=columns, timestamp=timestamp)
        with h5py.File(path, 'a') as f:
            f['science/LSAR/identification/frameNumber'][...] = frame_number
            g = f[sme2.GRID]
            for algorithm, flag in [('TSR', 4), ('DSG', 3)]:
                g.copy('algorithmCandidates/PMI', 'algorithmCandidates/' + algorithm)
                g[f'algorithmCandidates/{algorithm}/retrievalQualityFlag'][...] = flag
            g.create_dataset('retrievalQualityFlag', data=np.full((3, 3), 2))
        item = catalog_item(name=path.stem, start=timestamp + 'Z')
        item['meta'] = {'concept-id': f'G{i}-ASF'}
        item['umm']['TemporalExtent']['RangeDateTime']['EndingDateTime'] = timestamp[:-2] + '35Z'
        item['umm']['AdditionalAttributes'] = [dict(Name=k, Values=[v]) for k,v in (
            ('PRODUCT_TYPE','SME2'), ('TRACK_NUMBER','1'), ('FRAME_NUMBER',str(frame_number)),
            ('ASCENDING_DESCENDING','ASCENDING'))]
        item['umm']['SpatialExtent'] = {'HorizontalSpatialDomain': {'Geometry': {'GPolygons': [
            {'Boundary': {'Points': [dict(Longitude=x, Latitude=y) for x,y in view.geometry['coordinates'][0]]}}]}}}
        import hashlib
        item['umm']['DataGranule']['ArchiveAndDistributionInformation'][0]['Checksum'] = {
            'Algorithm': 'SHA-256', 'Value': hashlib.sha256(path.read_bytes()).hexdigest()}
        paths.append(path)
        granules.append(catalog.parse_granule(item))
    with h5py.File(paths[0]) as f:
        layout = sme2.window_layout(view, f)
    frames = frames_workflow.GranuleFrames(paths, layout)
    return view, layout, frames, granules


class NisarWebTests(unittest.TestCase):
    def test_combined_cli_preserves_sources_checksums_and_track_footprints(self):
        """CLI window reaches both renderers; combined coverage and all source provenance survive."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            view, _, _, granules = fixture(root, combine=True)
            args = ['--top-left', str(view.north), str(view.west), '--bottom-right', str(view.south),
                    str(view.east), '--data-dir', str(root), '--output-dir', str(root / 'visuals'),
                    '--output-prefix', 'combined', '--formats', 'html', 'gif',
                    '--min-numeric-percent', '80', '--nisar-combine-window', '5']
            with patch.object(workflow, 'search_box', return_value=(granules, {})), \
                    patch.object(workflow, 'render_topographic') as png, \
                    patch.object(catalog, 'earthdata_opener') as auth:
                self.assertEqual(workflow.main(args), 0)
                png.assert_not_called()
                auth.assert_not_called()
            output = root / 'visuals' / 'combined.html'
            manifest = read_manifest(output)
            self.assertEqual(len(manifest['frames']), 1)
            self.assertEqual(manifest['granule_count'], 2)
            self.assertEqual(manifest['provenance']['nisar_combine_window_minutes'], 5)
            self.assertEqual(manifest['provenance']['qualifying_frames'], 1)
            self.assertEqual(manifest['provenance']['qualifying_granules'], 2)
            self.assertEqual(set(manifest['provenance']['source_checksums']), {g.name for g in granules})
            frame = manifest['frames'][0]
            self.assertEqual(frame['layers']['PMI.soilMoisture']['numeric_cells'], 15)
            self.assertEqual([s['source_pixels'] for s in frame['granules']], [9, 6])
            self.assertEqual(frame['end'], '2026-01-01T01:05:00')
            tracks, _ = contributing_tracks(output)
            self.assertEqual({t['frame'] for t in tracks}, {2, 3})
            with Image.open(output.with_suffix('.gif')) as image:
                comment = json.loads(image.info['comment'])
                self.assertEqual(len(comment['frames']), 1)
                self.assertEqual(comment['frames'][0]['frameNumber'], [2, 3])
                self.assertEqual(len(comment['frames'][0]['source_granules']), 2)
            with patch.object(workflow, 'search_box', return_value=(granules, {})), \
                    patch.object(workflow, 'render_html') as html, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(workflow.main(args[:-1] + ['0']), 1)
                html.assert_not_called()

    def test_candidate_outlines_and_endpoint_colors(self):
        """Even/missing flags stay transparent, odd borders have clear interiors, and scales clip."""
        flags = np.array([[0, 1, 4, 5, np.nan, -9999]], float)
        with Image.open(io.BytesIO(web.encode_outlines(flags, np.ones(flags.shape, bool)))) as im:
            pixels = np.array(im)
        np.testing.assert_array_equal(pixels[0, ::4, 3], [0, 255, 0, 255, 0, 0])
        self.assertTrue((pixels[1, 1::4, 3] == 0).all())
        mask = np.ones((1, 5), bool)
        values = np.array([[-1, 0, .6, 1, np.nan]])
        with Image.open(io.BytesIO(web.encode_values(values, mask, 0, ((0,.6),(0,.08))))) as im:
            rgba = np.array(im)
        np.testing.assert_array_equal(rgba[0,0], rgba[0,1])
        np.testing.assert_array_equal(rgba[0,2], rgba[0,3])
        np.testing.assert_array_equal(rgba[0,4], [199,199,199,255])
        expected = color_maps()[0](np.array([0.,1.]), bytes=True)
        np.testing.assert_array_equal(rgba[0,[1,2]], expected)

    def test_html_layers_assets_and_station_map_metadata(self):
        """Keep every granule, six candidate layers and self-contained contributing-track metadata."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            view, layout, frames, granules = fixture(root)
            output = root / 'test.html'
            tracks = frame_tracks(granules, frames)
            title = '</script><script>bad()</script>__MAP_APP__'
            web.render_html(view, layout, frames, output, title,
                            provenance={'min_numeric_percent':50}, tracks=tracks)
            manifest = read_manifest(output)
            self.assertEqual(len(manifest['frames']), 2)
            self.assertEqual(len(manifest['views']), 6)
            self.assertNotIn(title, output.read_text())
            self.assertEqual(manifest['title'], title)
            self.assertIn('id="flag-toggle"', output.read_text())
            self.assertNotIn('combinedRetrievalQualityFlag', output.read_text())
            self.assertEqual(manifest['map_config']['basemap_url'], BASEMAP_TILES)
            for frame in manifest['frames']:
                self.assertEqual(len(frame['granules']),1)
                self.assertEqual(len(frame['layers']),6)
                self.assertIsNone(frame['layers']['TSR.soilMoisture']['outlines'])
                self.assertIsNotNone(frame['layers']['PMI.soilMoisture']['outlines'])
                self.assertEqual(frame['layers']['PMI.soilMoisture']['outlines'],
                                 frame['layers']['PMI.soilMoistureUncertainty']['outlines'])
                self.assertEqual(frame['layers']['TSR.soilMoisture']['recommended_cells'],9)
                self.assertEqual(frame['layers']['PMI.soilMoistureUncertainty']['numeric_cells'],0)
            for asset in manifest['assets'].values():
                self.assertFalse(asset.startswith('data:'))
                with Image.open(root / asset) as image:
                    image.verify()
            self.assertEqual(contributing_tracks(output)[0], tracks)
            self.assertEqual(manifest['domain']['geometry'], view.geometry)
            edges = web.geographic_edges(layout)
            x,y = np.meshgrid(edges['longitude_edges'],edges['latitude_edges'])
            xx,yy = geometry.FORWARD.transform(x,y)
            np.testing.assert_allclose(np.diff(xx,axis=1),200,atol=.001)
            np.testing.assert_allclose(np.diff(yy,axis=0),-200,atol=.001)

    def test_html_only_cli_does_not_render_other_formats(self):
        """--output-dir keeps HTML and its relative assets together without rendering other formats."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            view, _, _, granules = fixture(root)
            args = ['--top-left',str(view.north),str(view.west),'--bottom-right',str(view.south),
                    str(view.east),'--data-dir',str(root),'--output-prefix',str(root/'html_only'),
                    '--output-dir',str(root/'visuals'),
                    '--formats','html']
            with patch.object(workflow,'search_box',return_value=(granules,{})), \
                    patch.object(workflow,'render_gif') as gif, \
                    patch.object(workflow,'render_topographic') as png:
                self.assertEqual(workflow.main(args),0)
                gif.assert_not_called()
                png.assert_not_called()
            self.assertEqual({p.name for p in root.iterdir()},
                             {'NISAR_WEB_0.h5','NISAR_WEB_1.h5','visuals'})
            output = root/'visuals'/'html_only.html'
            self.assertTrue(output.is_file())
            self.assertTrue(all((output.parent / asset).is_file()
                                for asset in read_manifest(output)['assets'].values()))


if __name__ == '__main__':
    unittest.main()
