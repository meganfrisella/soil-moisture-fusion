"""Shared Leaflet/Esri configuration and safe HTML manifest serialization.

Map geometries use WGS84 longitude/latitude degrees. Basemaps are cartographic
context, with varying source dates; they are not observation coverage masks.
Imported by ``pixi run python scripts/build_station_map.py`` and the SME2 viewer.
"""

from html import escape
import json
from pathlib import Path
import re

LEAFLET_VERSION = '1.9.4'
LEAFLET_CSS = f'https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.css'
LEAFLET_JS = f'https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.js'
LEAFLET_CSS_INTEGRITY = 'sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='
LEAFLET_JS_INTEGRITY = 'sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo='
BASEMAP_SERVICE = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer'
BASEMAP_TILES = BASEMAP_SERVICE + '/tile/{z}/{y}/{x}'
BASEMAP_ATTRIBUTION = ('Tiles © Esri — Esri, HERE, Garmin, Intermap, increment P, GEBCO, USGS, FAO, '
                       'NPS, NRCAN, GeoBase, IGN, Kadaster NL, Ordnance Survey, Esri Japan, METI, '
                       'Esri China (Hong Kong), OpenStreetMap contributors, GIS User Community')
BOUNDARY_COLOR = '#344b44'
BOUNDARY_DASH = '7 6'


def browser_config():
    """Expose shared settings to either Leaflet map without duplicate JS constants."""
    return dict(basemap_url=BASEMAP_TILES, basemap_attribution=BASEMAP_ATTRIBUTION,
                boundary_color=BOUNDARY_COLOR, boundary_dash=BOUNDARY_DASH)


def json_for_html(value):
    """Prevent embedded JSON from closing its script element."""
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            .replace('&', '\\u0026').replace('<', '\\u003c'))


def render_template(template, manifest, app=None):
    """Apply shared web dependencies and safe data to a map template."""
    tokens = dict(MAP_TITLE=escape(manifest.get('title', 'Source stations and NISAR footprints')),
                  MAP_DATA=json_for_html(dict(manifest, map_config=browser_config())),
                  LEAFLET_CSS=LEAFLET_CSS, LEAFLET_JS=LEAFLET_JS,
                  LEAFLET_CSS_INTEGRITY=LEAFLET_CSS_INTEGRITY,
                  LEAFLET_JS_INTEGRITY=LEAFLET_JS_INTEGRITY,
                  BOUNDARY_COLOR=BOUNDARY_COLOR)
    if app is not None:
        tokens['MAP_APP'] = app
    # Replace placeholders in the template only: user-supplied titles are data.
    return re.sub(r'__([A-Z_]+)__', lambda m: tokens.get(m[1], m[0]), template)


def read_manifest(path):
    """Read the map-data block shared by the station and SME2 HTML formats."""
    match = re.search(r'<script id="map-data"[^>]*>(.*?)</script>', Path(path).read_text(), re.S)
    if not match:
        raise ValueError('HTML has no map-data manifest')
    return json.loads(match[1])


def latitude(value):
    """Format WGS84 latitude with the correct hemisphere."""
    return f'{abs(value):.6f}° {"N" if value >= 0 else "S"}'


def longitude(value):
    """Format WGS84 longitude with the correct hemisphere."""
    return f'{abs(value):.6f}° {"E" if value >= 0 else "W"}'


def bounds_label(view):
    """Format a WGS84 rectangle for workflow logs and figure labels."""
    return (f'{latitude(view.south)}–{latitude(view.north)}, '
            f'{longitude(view.west)}–{longitude(view.east)}')
