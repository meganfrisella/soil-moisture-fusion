"""Shared station-map validation and rendering.

Run through scripts/build_station_map.py.
Features are WGS84 degrees; GAIA grid bounds are EPSG:5070 metres.
Both entry points share catalog discovery, track hulls and one HTML template.
"""

import math
from pathlib import Path

from maps.common import render_template
from maps.nisar_footprints import track_hulls
from maps.station_catalogs import inside

def validate(data):
    if data.get('schema_version') != 2:
        raise ValueError('Expected station map snapshot schema 2')
    identifiers = set()
    for st in data['stations']:
        if st['id'] in identifiers:
            raise ValueError('Duplicate station ID: ' + st['id'])
        identifiers.add(st['id'])
        if not (-90 <= st['latitude'] <= 90 and -180 <= st['longitude'] <= 180) or not inside(st, data['domain']):
            raise ValueError('Invalid or out-of-domain station: ' + st['id'])
    soil_ids = {st['id'] for st in data['stations'] if st['kind'] == 'soil'}
    if not set(data.get('accent_soil_ids', [])) <= soil_ids:
        raise ValueError('Highlighted soil IDs must identify soil stations in the map')
    ring = data['domain']['geometry']['coordinates'][0]
    if len(ring) < 4 or ring[0] != ring[-1] or not all(math.isfinite(v) for xy in ring for v in xy):
        raise ValueError('Invalid boundary ring')
    for track in data.get('tracks', []):
        if track['geometry']['type'] != 'MultiPolygon' or not track['granules']:
            raise ValueError('Invalid NISAR track footprint')
        for polygon in track['geometry']['coordinates']:
            for ring in polygon:
                if (len(ring) < 4 or ring[0] != ring[-1]
                        or not all(math.isfinite(lon) and math.isfinite(lat)
                                   and -180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in ring)):
                    raise ValueError('Invalid NISAR footprint ring')


def render(data):
    validate(data)
    manifest = dict(data)
    manifest['title'] = data['domain'].get('label', 'GAIA domain') + ' · Source stations and NISAR footprints'
    manifest.pop('track_boxes', None)
    manifest.pop('pilots', None)
    manifest['notes'] = [note for note in manifest.get('notes', [])
                         if not note.startswith('Pilot outlines are')]
    manifest['track_hulls'] = track_hulls(data.get('tracks', []), data['domain']['geometry'])
    return render_template(MAP_HTML, manifest)


MAP_HTML = (Path(__file__).resolve().parent / 'station_map.html').read_text()
