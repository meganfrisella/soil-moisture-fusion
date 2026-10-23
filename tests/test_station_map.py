"""Offline checks for geographic station maps, activity notes, and verified catalog reuse."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from maps import station_map as maps
from maps import station_catalogs as catalogs
from maps.common import read_manifest
from nisar.geometry import BoundingBox
import build_station_map as workflow


def snapshot():
    """Make a small WGS84 region containing one closed seismic station."""
    view = BoundingBox.from_corners((48, -121), (46, -117))
    station = catalogs.point('UW.TEST', 'Test station', 'seismic', 47, -119, 'https://example.org')
    station['activity'] = dict(status='inactive', basis='Station closed in 2020', as_of_utc='2026-10-15')
    return dict(schema_version=2, retrieved_at='2026-10-15T00:00:00+00:00',
                domain=dict(name='Test region', label='Test region', crs='EPSG:4326',
                            bounds_wgs84=list(view.bounds), geometry=view.geometry),
                stations=[station], tracks=[], selection={}, sources=[], notes=[])


class StationMapTests(unittest.TestCase):
    def test_geographic_boundary_and_region_labels(self):
        """Geographic boxes include boundary stations without inventing a GAIA grid or resolution."""
        data = snapshot()
        maps.validate(data)
        for lat, lon, expected in [(48, -121, True), (46, -117, True), (45.999, -119, False)]:
            self.assertEqual(maps.inside(dict(latitude=lat, longitude=lon), data['domain']), expected)
        data['domain']['label'] = '</title><script>unsafe()</script>'
        html = maps.render(data)
        self.assertNotIn(data['domain']['label'], html)
        self.assertIn('Inactive sensor in the source catalog.', html)
        self.assertNotIn('__MAP_TITLE__', html)
        self.assertNotIn('resolution_m', data['domain'])

    def test_historical_seismic_epochs_and_activity(self):
        """Retain closed stations and prefer an open epoch when a station code was reused."""
        def station(code, end):
            attr = f' endDate="{end}"' if end else ''
            return f'<Station code="{code}"{attr}><Latitude>47</Latitude><Longitude>-119</Longitude></Station>'
        xml = ('<FDSNStationXML xmlns="http://www.fdsn.org/xml/station/1"><Network code="UW">' +
               station('OLD', '2020-01-01') + station('NEW', None) + station('NEW', '2021-01-01') +
               '</Network></FDSNStationXML>')
        result = {s['id']: s for s in catalogs.seismic_stations(xml, '', None, '2026-10-15')}
        self.assertEqual(set(result), {'UW.OLD', 'UW.NEW'})
        self.assertEqual(result['UW.OLD']['activity']['status'], 'inactive')
        self.assertEqual(result['UW.NEW']['activity']['status'], 'active')
        self.assertEqual(catalogs.epoch_activity(None, '2026-10-15')['status'], 'unknown')

    def test_closed_awdb_station_keeps_both_roles(self):
        """Closed SNOTEL sites still show colocated moisture and SWE without duplicate markers."""
        item = dict(networkCode='SNTL', stationTriplet='1:WA:SNTL', name='Closed', latitude=47,
                    longitude=-119, endDate='2020-01-01',
                    stationElements=[{'elementCode': code} for code in ('SMS', 'WTEQ')])
        found = catalogs.awdb_stations([item], ('SNTL',), '', '2026-10-15')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['activity']['status'], 'inactive')
        self.assertIn('WTEQ', dict(found[0]['rows'])['Measurements'])

    def test_response_cache_validation_and_no_repeat_download(self):
        """Reuse verified public responses and fail on corruption before contacting the provider."""
        with tempfile.TemporaryDirectory() as folder:
            catalog = catalogs.Catalog(folder)
            address = 'https://example.org/catalog'
            response = subprocess.CompletedProcess([], 0, stdout=b'{"stations": []}')
            with patch.object(catalogs.subprocess, 'run', return_value=response) as run:
                self.assertEqual(catalog.get(address), response.stdout)
                self.assertEqual(catalog.get(address), response.stdout)
                self.assertEqual(run.call_count, 1)
                path = next(Path(folder).glob('*.json'))
                doc = json.loads(path.read_text())
                doc['body'] = 'corrupted'
                path.write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError, 'Invalid cached'):
                    catalog.get(address)
                self.assertEqual(run.call_count, 1)
            self.assertEqual(catalog.bytes, len(response.stdout))

    def test_box_cli_build_reuse_and_wrong_region_rejection(self):
        """Box builds use shared rendering, then reuse their own metadata and reject mismatched bounds."""
        with tempfile.TemporaryDirectory() as folder:
            data = snapshot()
            args = ['--top-left', '48', '-121', '--bottom-right', '46', '-117',
                    '--title', 'Test region', '--output-dir', folder]
            with patch.object(workflow, 'refresh', return_value=copy.deepcopy(data)) as refresh, \
                    patch.object(workflow, 'render_preview') as preview:
                self.assertEqual(workflow.main(args), 0)
                self.assertEqual(workflow.main(args), 0)
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(preview.call_count, 2)
                saved = read_manifest(Path(folder) / 'station_map.html')
                self.assertEqual(saved['domain']['bounds_wgs84'], [-121, 46, -117, 48])
                self.assertEqual(saved['stations'], data['stations'])
                wrong = args.copy()
                wrong[1] = '49'
                with self.assertRaises(SystemExit):
                    workflow.main(wrong)
                self.assertEqual(refresh.call_count, 1)

    def test_generic_discharge_parser_and_empty_catalog(self):
        """Generic boxes use provider names; configured GAIA gauge lists remain enforced."""
        rdb = '# comment\nsite_no\tstation_nm\tdec_lat_va\tdec_long_va\n15s\t50s\t16s\t16s\n1\tRiver one\t47\t-119\n'
        self.assertEqual(catalogs.rdb_stations(rdb)[0]['name'], 'River one')
        self.assertEqual(catalogs.rdb_stations(rdb, {'1': 'Configured'})[0]['name'], 'Configured')
        with self.assertRaisesRegex(ValueError, 'every configured'):
            catalogs.rdb_stations(rdb, {'2': 'Missing'})
        self.assertEqual(catalogs.rdb_stations('# No sites found'), [])


if __name__ == '__main__':
    unittest.main()
