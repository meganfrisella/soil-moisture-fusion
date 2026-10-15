"""Offline soil-site selection, provenance, activity and coordinate-conversion checks."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pyproj import CRS
import shapefile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from maps import soil_stations as soil
from maps.station_catalogs import Catalog, inside


class SoilStationTests(unittest.TestCase):
    def test_agrimet_requires_station_specific_moisture_channels(self):
        """A neighboring station's moisture channel must not qualify a temperature-only station."""
        coordinates = {'features': [dict(properties=dict(siteid=identifier, description=identifier,
                      url=identifier.lower() + '.html'), geometry={'coordinates': [-119, 47]})
                      for identifier in ('SOIL', 'TEMP')]}
        parameters = ('<pre><A NAME=SOIL></A>XSM8 SOIL MOISTURE - 8 INCH DEPTH (PERCENT)\n'
                      '<A NAME=TEMP></A>SL SOIL TEMP AT 1 INCH DEPTH (DEG F)</pre>')
        stations = soil.agrimet_stations(coordinates, parameters, '2026-10-15')
        self.assertEqual([s['id'] for s in stations], ['AgriMet.SOIL'])
        self.assertEqual(stations[0]['activity']['status'], 'unknown')
        self.assertIn('20.32 cm', dict(stations[0]['rows'])['Channels / depths'])

    def test_awn_requires_public_coordinates_and_moisture_probe(self):
        """Exclude private, temperature-only and unmatched sites; convert positive-west longitude."""
        inventory = {'data': [dict(unit_id=i, soil_mois_8_in='Y' if i != 3 else 'N', soil_temp_8_in='Y')
                              for i in range(1, 5)]}
        metadata = {'data': [dict(UNIT_ID=i, STATION_NAME=str(i), STATION_LATDEG=47,
                                 STATION_LNGDEG=119, STATION_VISIBILITY='private' if i == 2 else 'public')
                             for i in range(1, 4)]}
        stations = soil.awn_stations(inventory, metadata, '2026-10-15')
        self.assertEqual([s['id'] for s in stations], ['AWN.1'])
        self.assertEqual(stations[0]['longitude'], -119)
        self.assertTrue(inside(stations[0], {'bounds_wgs84': [-120, 46, -118, 48]}))
        self.assertFalse(inside(stations[0], {'bounds_wgs84': [-118, 46, -117, 48]}))

    def test_ameriflux_swc_evidence_and_closed_tower(self):
        """Require the SWC token or published sensor evidence, without treating old data as closure."""
        records = [dict(site_id=identifier, site_name=identifier, latitude=47, longitude=-119,
                        base_vars=[1, variable], base_data=[2018], tower_start='2017', tower_end=end)
                   for identifier, variable, end in [('US-ONE', 'TS, SWC', '2024'),
                                                    ('US-TWO', 'SWC', ''),
                                                    ('US-THREE', 'TS, SWC_F', ''),
                                                    ('US-Hn1', 'TS', '')]]
        html = 'const jsonSites = ' + repr(json.dumps(records)) + ';'
        stations = {s['id']: s for s in soil.ameriflux_stations(html, '2026-10-15')}
        self.assertEqual(set(stations), {'AmeriFlux.US-ONE', 'AmeriFlux.US-TWO', 'AmeriFlux.US-Hn1'})
        self.assertEqual(stations['AmeriFlux.US-ONE']['activity']['status'], 'inactive')
        self.assertEqual(stations['AmeriFlux.US-TWO']['activity']['status'], 'unknown')
        self.assertIn('Published sensor evidence', dict(stations['AmeriFlux.US-Hn1']['links']))

    def test_cook_points_use_archive_crs_and_preserve_profile_ids(self):
        """Transform a synthetic NAD83 UTM point and keep historical coverage separate from closure."""
        shp, dbf = io.BytesIO(), io.BytesIO()
        with shapefile.Writer(shp=shp, dbf=dbf, shapeType=shapefile.POINT) as writer:
            writer.field('Location', 'C')
            writer.point(493384.052, 5180585.318)
            writer.record('CAF_TEST')
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as files:
            files.writestr('CAF_sensors.shp', shp.getvalue())
            files.writestr('CAF_sensors.dbf', dbf.getvalue())
            files.writestr('CAF_sensors.prj', CRS.from_epsg(26911).to_wkt())
        station, = soil.cook_probe_stations(archive, '2026-10-15')
        self.assertEqual(station['id'], 'CookFarm.CAF_TEST')
        self.assertAlmostEqual(station['latitude'], 46.778784, places=5)
        self.assertAlmostEqual(station['longitude'], -117.086666, places=5)
        self.assertEqual(station['activity']['status'], 'unknown')

    def test_token_loader_runs_only_for_missing_inventory(self):
        """Cache the public response under a stable URL without retaining temporary nonce URLs."""
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(folder)
            with patch.object(catalog, 'fetch', side_effect=AssertionError('Unexpected request')):
                calls = []
                def loader():
                    calls.append(1)
                    return b'{"data": []}'
                self.assertEqual(catalog.get(soil.AWN_SENSORS, loader), b'{"data": []}')
                self.assertEqual(catalog.get(soil.AWN_SENSORS, loader), b'{"data": []}')
                self.assertEqual(len(calls), 1)
                self.assertEqual(set(catalog.responses), {soil.AWN_SENSORS})

    def test_archive_reuse_verifies_published_checksum(self):
        """Fail on a corrupted cached archive and never silently redownload or accept it."""
        import hashlib
        payload = b'archive fixture'
        metadata = dict(name='fixture.zip', download_url='https://example.org/archive',
                        size=len(payload), computed_md5=hashlib.md5(payload).hexdigest())
        with tempfile.TemporaryDirectory() as folder:
            catalog = Catalog(folder)
            with patch.object(catalog, 'fetch', return_value=payload) as fetch:
                path = catalog.archive(metadata)
                self.assertEqual(catalog.archive(metadata), path)
                self.assertEqual(fetch.call_count, 1)
                path.write_bytes(b'corrupted bytes')
                with self.assertRaisesRegex(ValueError, 'Invalid cached archive'):
                    catalog.archive(metadata)
                self.assertEqual(fetch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
