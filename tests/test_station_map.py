"""Offline scientific metadata checks; run with python -m unittest discover -s tests."""

import copy
import subprocess
import sys
import tempfile
import importlib.util
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("station_map", ROOT / "scripts/build_station_map.py")
MAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAP)


class StationMapTests(unittest.TestCase):
    def test_seismic_components_exclude_housekeeping_and_closed_epochs(self):
        """Keep active seismic channels; exclude housekeeping, past, and future epochs."""
        stations = "".join(f'''<Station code="{code}"><Latitude>47.3</Latitude>
            <Longitude>-118.7</Longitude><Elevation>500</Elevation>
            <Channel code="HHZ"><SampleRate>100</SampleRate></Channel>
            <Channel code="GNS"/><Channel code="SNI"/>
            <Channel code="EHZ" endDate="2020-01-01"/>
            <Channel code="HHN" startDate="2027-01-01"/></Station>'''
            for code in MAP.STATIONS)
        xml = f'<FDSNStationXML xmlns="{MAP.NS["s"]}"><Network>{stations}</Network></FDSNStationXML>'
        result = MAP.seismic_stations(xml, "2026-10-06")
        self.assertEqual(len(result), len(MAP.STATIONS))
        self.assertTrue(all([c["code"] for c in s["channels"]] == ["HHZ"] for s in result))

    def test_awdb_excludes_expired_and_temperature_only_sensors(self):
        """Report active moisture depths in inches and centimeters without spatial filtering."""
        station = {"latitude": 47.33, "longitude": -118.69, "stationTriplet": "1:WA:SCAN",
                   "stationId": "1", "networkCode": "SCAN", "name": "Synthetic", "elevation": 100,
                   "stationElements": [
                       {"elementCode": "SMS", "heightDepth": -8, "beginDate": "2000-01-01",
                        "endDate": "2100-01-01", "durationName": "HOURLY"},
                       {"elementCode": "SMS", "heightDepth": -40, "beginDate": "2000-01-01",
                        "endDate": "2020-01-01", "durationName": "HOURLY"},
                       {"elementCode": "STO", "heightDepth": -20}]}
        result = MAP.awdb_stations([station], "2026-10-06")
        self.assertEqual(dict(result[0]["rows"])["Sensor depths"], "8 in (20.32 cm)")
        station["latitude"] = 49
        self.assertEqual(len(MAP.awdb_stations([station], "2026-10-06")), 1)

    def test_awn_requires_moisture_evidence_and_corrects_degrees_west(self):
        """Require public moisture-equipped sites and convert degrees west to negative longitude."""
        catalog = {"data": [{"UNIT_ID": "1", "STATION_NAME": "Synthetic", "NETWORK_TYPE": "Legacy",
                             "STATION_LATDEG": "47.33", "STATION_LNGDEG": "118.69",
                             "STATION_VISIBILITY": "public"}]}
        inventory = {"data": [{"unit_id": 1, "soil_temp_8_in": "Y", "soil_mois_8_in": "N"}]}
        self.assertEqual(MAP.awn_stations(inventory, catalog), [])
        inventory["data"][0]["soil_mois_8_in"] = "Y"
        self.assertEqual(MAP.awn_stations(inventory, catalog)[0]["longitude"], -118.69)
        catalog["data"][0]["STATION_VISIBILITY"] = "private"
        self.assertEqual(MAP.awn_stations(inventory, catalog), [])

    def test_cmr_pagination_and_zero_results(self):
        """Query Odessa across all result pages, allow no matches, and reject truncated results."""
        calls = []
        def fake_get(address):
            query = parse_qs(urlparse(address).query)
            calls.append(query)
            return {"hits": 2, "items": [{"page": int(query["page_num"][0])}]}
        items, _ = MAP.search_nisar(fake_get)
        self.assertEqual(items, [{"page": 1}, {"page": 2}])
        self.assertEqual(calls[0]["point"], ["-118.6882,47.3332"])
        self.assertEqual(MAP.search_nisar(lambda _: {"hits": 0, "items": []})[0], [])
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            MAP.search_nisar(lambda _: {"hits": 1, "items": []})

    def test_agrimet_station_specific_moisture_depths(self):
        """Extract only ODSW moisture depths and reject inventories lacking moisture sensors."""
        site = 'Latitude: 47.30888 N<br>Longitude: -118.87861 W<br>Installation Date: 4/24/1984'
        inventory = '''<pre><A NAME=ODSW></A>ODSW ODESSA
        SL SOIL TEMP AT 1 INCH DEPTH (DEG F)
        XSM2 SOIL MOISTURE - 2 INCH DEPTH (PERCENT)
        XSM8 SOIL MOISTURE - 8 INCH DEPTH (PERCENT)
        XSM20 SOIL MOISTURE - 20 INCH DEPTH (PERCENT)
        XSM40 SOIL MOISTURE - 40 INCH DEPTH (PERCENT)
        <A NAME=OTHER></A>OTHER
        XSM60 SOIL MOISTURE - 60 INCH DEPTH (PERCENT)</pre>'''
        station = MAP.agrimet_odessa(site, inventory)
        self.assertEqual(station['longitude'], -118.87861)
        self.assertEqual(dict(station['rows'])['Sensor depths'],
                         '2 in (5.08 cm), 8 in (20.32 cm), 20 in (50.8 cm), 40 in (101.6 cm)')
        with self.assertRaisesRegex(ValueError, 'no verified soil-moisture'):
            MAP.agrimet_odessa(site, '<pre><A NAME=ODSW></A>SL SOIL TEMP AT 1 INCH</pre>')

    def test_polygon_order_closure_and_holes(self):
        """Use GeoJSON longitude-latitude order, close polygon rings, and retain holes."""
        ring = {"Points": [{"Latitude": 47, "Longitude": -119},
                           {"Latitude": 48, "Longitude": -119},
                           {"Latitude": 47, "Longitude": -118}]}
        record = {"SpatialExtent": {"HorizontalSpatialDomain": {"Geometry": {
            "GPolygons": [{"Boundary": ring, "ExclusiveZone": {"Boundaries": [ring]}}]}}}}
        geometry = MAP.cmr_geometry(record)
        self.assertEqual(len(geometry["coordinates"][0]), 2)
        self.assertEqual(geometry["coordinates"][0][0][0], [-119, 47])
        self.assertEqual(geometry["coordinates"][0][0][-1], [-119, 47])

    def test_snapshot_integrity_and_html_escaping(self):
        """Validate station coordinates and escape metadata that could inject script tags."""
        data = json.loads((ROOT / "data/station_map.json").read_text())
        MAP.validate(data)
        self.assertEqual(len(data["seismic"]), len(MAP.STATIONS))
        bad = copy.deepcopy(data)
        bad["soil"][0]["latitude"] = 91
        with self.assertRaisesRegex(ValueError, "Invalid coordinates"):
            MAP.validate(bad)
        data["soil"][0]["name"] = "</script><script>alert(1)</script>"
        rendered = MAP.render(data)
        self.assertNotIn(data["soil"][0]["name"], rendered)
        self.assertIn("\\u003c/script>", rendered)

    def test_cli_build_from_clean_directory_without_templates_or_distance_fields(self):
        """Build HTML from a snapshot without existing outputs, templates, or distance fields."""
        data = json.loads((ROOT / "data/station_map.json").read_text())
        data.pop("radius_km", None)
        for station in data["seismic"] + data["soil"]:
            station.pop("distance_km", None)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "scripts/build_station_map.py"
            script.parent.mkdir()
            script.write_text((ROOT / "scripts/build_station_map.py").read_text())
            snapshot = root / "data/station_map.json"
            snapshot.parent.mkdir()
            snapshot.write_text(json.dumps(data))
            result = subprocess.run([sys.executable, str(script)], cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            document = (root / "outputs/station_map.html").read_text()
            self.assertIn('id="map-data"', document)
            self.assertIn("UW.DAVN", document)
            self.assertNotIn("__MAP_DATA__", document)
            self.assertNotIn("distance_km", document)
            self.assertNotIn("radius_km", document)


if __name__ == "__main__":
    unittest.main()
