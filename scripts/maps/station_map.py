"""Shared station-map validation, rendering and command-line workflow.

Run through scripts/build_gaia_map.py or scripts/build_station_map.py.
Features are WGS84 degrees; GAIA grid bounds are EPSG:5070 metres.
Both entry points share catalog discovery, track hulls and one HTML template.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import subprocess

from maps.common import read_manifest, render_template
from maps.nisar_footprints import track_hulls
from maps.preview import render_preview
from maps.station_catalogs import inside, refresh
from nisar.geometry import BoundingBox

ROOT = Path(__file__).resolve().parents[2]


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


def main(argv=None, box_required=False):
    """Build from a saved manifest or discover sensors for a projected grid/WGS84 box."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--refresh', action='store_true', help='Rebuild sensor inventory using verified cached responses or missing downloads')
    parser.add_argument('--gaia-root', type=Path, default=ROOT.parent / 'gwl-space-time-smooth')
    parser.add_argument('--input-map', type=Path, help='Existing station-map metadata; defaults to the output for box maps')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/station_map.html')
    parser.add_argument('--output-dir', type=Path, help='HTML/PNG destination; overrides the directory in --output')
    parser.add_argument('--top-left', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--bottom-right', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--title', default='Station map', help='Name of the bounding-box region')
    parser.add_argument('--cache-dir', type=Path,
                        default=ROOT / 'data/station_catalogs' / datetime.now(timezone.utc).date().isoformat(),
                        help='Verified catalog response cache; choose a new directory for a fresh snapshot')
    parser.add_argument('--browser', type=Path, help='Chrome/Chromium executable for the PNG preview')
    parser.add_argument('--sme2-map', type=Path, help='Sync contributing NISAR footprints from this SME2 HTML')
    args = parser.parse_args(argv)
    if args.output_dir is not None:
        args.output = args.output_dir / args.output.name
    if bool(args.top_left) != bool(args.bottom_right):
        parser.error('Provide both --top-left and --bottom-right')
    if box_required and not args.top_left and not args.input_map:
        parser.error('Provide bounding-box corners or --input-map for an offline rebuild')
    region = None
    if args.top_left:
        try:
            view = BoundingBox.from_corners(args.top_left, args.bottom_right)
        except ValueError as error:
            parser.error(str(error))
        region = dict(name=args.title, label=args.title, crs='EPSG:4326',
                      bounds_wgs84=list(view.bounds), geometry=view.geometry)
    input_map = args.input_map or (args.output if box_required else ROOT / 'outputs/station_map.html')
    if args.input_map and not input_map.is_file():
        parser.error(f'Input map does not exist: {input_map}')
    previous = read_manifest(input_map) if input_map.exists() else None
    if box_required and previous and 'bounds_wgs84' not in previous['domain']:
        parser.error('Use build_gaia_map.py to rebuild a projected GAIA domain snapshot')
    if previous and region and previous['domain']['geometry'] != region['geometry']:
        parser.error('Saved map has a different domain; use a new --output-dir or matching --input-map')
    needs_refresh = args.refresh or previous is None
    if needs_refresh:
        if box_required and region is None:
            region = previous['domain']
        data = refresh(args.gaia_root, args.cache_dir, region)
        if previous:
            for key in ('tracks', 'nisar_source'):
                if key in previous:
                    data[key] = previous[key]
    else:
        data = previous
    if region:
        data['domain'] = region
    if args.sme2_map:
        from maps.nisar_footprints import contributing_tracks
        data['tracks'], data['nisar_source'] = contributing_tracks(args.sme2_map)
        data['nisar_source'].update(html=str(args.sme2_map))
    document = render(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document)
    preview = args.output.with_suffix('.png')
    try:
        render_preview(args.output, preview, args.browser)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'HTML saved to {args.output}; PNG preview failed: {error}\n')
    print(f"Built {args.output} and {preview}: {dict(Counter(s['kind'] for s in data['stations']))}")
    return 0

MAP_HTML = (ROOT / 'scripts/maps/station_map.html').read_text()
