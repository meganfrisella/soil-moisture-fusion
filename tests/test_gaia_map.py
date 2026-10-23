"""Offline checks: pixi run python -m unittest discover -s tests -p 'test_gaia_map.py'."""

import copy
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from maps import station_map as MAP
from maps.common import read_manifest
from maps import station_catalogs as catalogs
from maps.preview import render_preview


class GaiaMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = read_manifest(ROOT / 'site/index.html')

    def test_projected_domain_and_clip(self):
        """Every densified boundary vertex lies on the projected grid, excluding bbox corners."""
        d = self.data['domain']
        tf = Transformer.from_crs('EPSG:4326', d['crs'], always_xy=True)
        x0, y0, x1, y1 = d['bounds_m']
        ring = d['geometry']['coordinates'][0]
        for lon, lat in ring:
            x, y = tf.transform(lon, lat)
            self.assertLess(min(abs(x-x0), abs(x-x1), abs(y-y0), abs(y-y1)), .01)
        self.assertTrue(MAP.inside({'latitude':47.2, 'longitude':-122.02}, d))
        bbox_corner = {'latitude':min(p[1] for p in ring), 'longitude':min(p[0] for p in ring)}
        self.assertFalse(MAP.inside(bbox_corner, d))

    def test_awdb_roles_and_temperature_only(self):
        """Colocated SWE and moisture produce one marker; temperature alone is excluded."""
        st = dict(networkCode='SNTL', stationTriplet='1:WA:SNTL', name='Test', latitude=47.2,
                  longitude=-122, stationElements=[{'elementCode':code} for code in ('SMS','WTEQ')])
        found = catalogs.awdb_stations([st], ('SNTL','SCAN','SNLT'), 'https://example.org')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], 'soil')
        self.assertIn('WTEQ', dict(found[0]['rows'])['Measurements'])
        st['stationElements'] = [{'elementCode':'WTEQ'}]
        self.assertEqual(catalogs.awdb_stations([st], ('SNTL',), '')[0]['kind'], 'snow')
        st['stationElements'] = [{'elementCode':'STO'}]
        self.assertEqual(catalogs.awdb_stations([st], ('SNTL',), ''), [])

    def test_groundwater_qc_and_record_period(self):
        """Historical wells are retained once; disturbed, missing and negative depths are excluded."""
        def feature(site, value, date, qualifier=()):
            return {'geometry':{'coordinates':[-122,47.2]}, 'properties':{
                'monitoring_location_id':site, 'value':value, 'time':date, 'qualifier':qualifier}}
        features = [feature('USGS-1',3,'2020-01-01'), feature('USGS-1',2,'2001-01-01'),
                    feature('USGS-2',4,'2020-01-01',['Pumping']), feature('USGS-3',None,'2020-01-01'),
                    feature('USGS-4',-1,'2020-01-01')]
        result = catalogs.groundwater_stations(features, {'pumping'})
        self.assertEqual(len(result),1)
        self.assertEqual(dict(result[0]['rows'])['Accepted records (UTC)'], '2001-01-01 — 2020-01-01')

    def test_pagination_and_loop_detection(self):
        """USGS next-page links are followed and repeated links fail instead of silently truncating."""
        class Catalog:
            def get(self, address):
                if address.endswith('/first'):
                    return json.dumps({'features':[1], 'links':[{'rel':'next','href':'next'}]})
                return json.dumps({'features':[2], 'links':[]})
        self.assertEqual(catalogs.paged_features(Catalog(), 'https://example.org/first'), [1,2])
        class Loop:
            def get(self, address):
                return json.dumps({'features':[], 'links':[{'rel':'next','href':address}]})
        with self.assertRaisesRegex(ValueError,'pagination'):
            catalogs.paged_features(Loop(), 'https://example.org/first')

    def test_snapshot_integrity_and_html_safety(self):
        """Snapshot stations are unique and in-domain; HTML escapes catalog text and drops pilot regions."""
        MAP.validate(self.data)
        data = copy.deepcopy(self.data)
        data['pilots'] = [{'id': 'enumclaw', 'name': 'Removed candidate region'}]
        data['stations'][0]['name'] = '</script><script>alert(1)</script>'
        self.assertNotIn(data['stations'][0]['name'], MAP.render(data))
        self.assertNotIn('Removed candidate region', MAP.render(data))
        data['stations'][0]['latitude'] = 91
        with self.assertRaisesRegex(ValueError,'Invalid'):
            MAP.validate(data)

    def test_offline_build_without_gaia_checkout(self):
        """Saved metadata and a browser that stays alive after capture produce both outputs offline."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'scripts').mkdir()
            (root / 'outputs').mkdir()
            shutil.copytree(ROOT / 'src', root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
            script = root / 'scripts/build_station_map.py'
            script.write_text((ROOT / 'scripts/build_station_map.py').read_text())
            (root / 'outputs/station_map.html').write_text(MAP.render(self.data))
            fixture = root / 'fixture.png'
            Image.new('RGB', (1600, 1050), 'white').save(fixture)
            browser = root / 'fake-chrome'
            browser.write_text(f'#!{sys.executable}\nimport sys, shutil, time\n'
                               'target = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--screenshot="))\n'
                               f'shutil.copyfile({str(fixture)!r}, target)\n'
                               'print(\'<html data-map-ready="true"></html>\', flush=True)\ntime.sleep(30)\n')
            browser.chmod(0o700)
            result = subprocess.run([sys.executable, str(script), '--gaia-domain', '--browser', str(browser)],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            html = (root / 'outputs/station_map.html').read_text()
            self.assertIn('GAIA domain',html)
            self.assertNotIn('__MAP_DATA__',html)
            manifest = read_manifest(root / 'outputs/station_map.html')
            self.assertNotIn('track_boxes', manifest)
            self.assertNotIn('pilots', manifest)
            hulls = manifest['track_hulls']
            self.assertEqual(len(hulls), len({hull['track'] for hull in hulls}))
            self.assertTrue(all(len(hull['geometry']['coordinates']) == 1 for hull in hulls))
            output_dir = root / 'custom'
            result = subprocess.run(
                [sys.executable, str(script), '--gaia-domain', '--browser', str(browser), '--output-dir', str(output_dir),
                 '--output', str(root / 'unused' / 'custom_map.html')],
                cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output_dir / 'custom_map.html').read_text(), html)
            for output in [root / 'outputs/station_map.png', output_dir / 'custom_map.png']:
                with Image.open(output) as image:
                    self.assertEqual(image.size, (1600, 1050))
                    metadata = json.loads(image.info['Description'])
                    self.assertEqual(metadata['source_html'], output.with_suffix('.html').name)
                    self.assertEqual(metadata['feature_crs'], 'EPSG:4326')
            self.assertFalse((root / 'unused').exists())

    def test_failed_browser_does_not_replace_preview(self):
        """An incomplete map render fails visibly and preserves the previously published PNG."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html, png = root / 'map.html', root / 'map.png'
            html.write_text('<html></html>')
            png.write_bytes(b'previous PNG')
            browser = root / 'fake-chrome'
            browser.write_text(f'#!{sys.executable}\nprint(\'<html data-map-ready="false"></html>\')\n')
            browser.chmod(0o700)
            with self.assertRaisesRegex(RuntimeError, 'Existing PNG preserved'):
                render_preview(html, png, browser=browser)
            self.assertEqual(png.read_bytes(), b'previous PNG')
            self.assertFalse(list(root.glob('.map-preview-*')))


if __name__ == '__main__':
    unittest.main()
