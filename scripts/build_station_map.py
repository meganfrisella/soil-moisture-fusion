#!/usr/bin/env python3
"""Build a GAIA-domain or WGS84-box station map and PNG preview.

Run from the repository root after pixi install:
    pixi run python scripts/build_station_map.py --gaia-domain --output-dir outputs
    pixi run python scripts/build_station_map.py \
        --top-left 48.076483 -121.241744 --bottom-right 46.057370 -117.072163 \
        --title "Eastern Washington" --output-dir outputs/eastern_washington \
        --sme2-map outputs/eastern_washington/eastern_washington.html

Catalog responses are verified in --cache-dir; cached metadata is reused unless
--refresh is supplied. The GAIA checkout is needed for fresh discovery. Box
corners are WGS84 latitude and longitude. Chrome/Chromium and map tiles are
needed to capture the PNG; --browser selects its executable. The resulting
HTML carries EPSG:4326 stations and NISAR footprints, while GAIA grid bounds
are EPSG:5070 metres. --output-dir selects the HTML/PNG destination.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from maps.common import read_manifest
from maps.nisar_footprints import contributing_tracks
from maps.preview import render_preview
from maps.station_catalogs import refresh
from maps.station_map import render
from nisar.geometry import BoundingBox


def main(argv=None):
    """Discover or reuse a station inventory, render HTML, and capture its PNG."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gaia-domain', action='store_true',
                        help='Use the projected GAIA domain rather than a WGS84 bounding box')
    parser.add_argument('--refresh', action='store_true',
                        help='Rebuild the sensor inventory using cached or missing responses')
    parser.add_argument('--gaia-root', type=Path, default=ROOT.parent / 'gwl-space-time-smooth')
    parser.add_argument('--input-map', type=Path, help='Existing station-map metadata')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/station_map.html')
    parser.add_argument('--output-dir', type=Path, help='HTML/PNG destination; overrides --output directory')
    parser.add_argument('--top-left', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--bottom-right', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--title', default='Station map')
    parser.add_argument('--cache-dir', type=Path,
                        default=ROOT / 'data/station_catalogs' / datetime.now(timezone.utc).date().isoformat())
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--sme2-map', type=Path, help='Sync NISAR footprints from this SME2 HTML')
    parser.add_argument('--accent-soil-ids', nargs='+', metavar='ID')
    args = parser.parse_args(argv)
    if args.output_dir is not None:
        args.output = args.output_dir / args.output.name
    if bool(args.top_left) != bool(args.bottom_right):
        parser.error('Provide both --top-left and --bottom-right')
    if args.gaia_domain and args.top_left:
        parser.error('--gaia-domain cannot be combined with box corners')
    if not args.gaia_domain and not args.top_left and not args.input_map:
        parser.error('Provide bounding-box corners, --input-map, or --gaia-domain')
    region = None
    if args.top_left:
        try:
            view = BoundingBox.from_corners(args.top_left, args.bottom_right)
        except ValueError as error:
            parser.error(str(error))
        region = dict(name=args.title, label=args.title, crs='EPSG:4326',
                      bounds_wgs84=list(view.bounds), geometry=view.geometry)
    input_map = args.input_map or (ROOT / 'outputs/station_map.html' if args.gaia_domain else args.output)
    if args.input_map and not input_map.is_file():
        parser.error(f'Input map does not exist: {input_map}')
    previous = read_manifest(input_map) if input_map.exists() else None
    if not args.gaia_domain and previous and 'bounds_wgs84' not in previous['domain']:
        parser.error('Use --gaia-domain to rebuild a projected GAIA domain snapshot')
    if previous and region and previous['domain']['geometry'] != region['geometry']:
        parser.error('Saved map has a different domain; use a new --output-dir or matching --input-map')
    if args.refresh or previous is None:
        if not args.gaia_domain and region is None:
            region = previous['domain']
        data = refresh(args.gaia_root, args.cache_dir, region)
        if previous:
            for key in ('tracks', 'nisar_source', 'accent_soil_ids'):
                if key in previous:
                    data[key] = previous[key]
    else:
        data = previous
    if region:
        data['domain'] = region
    if args.accent_soil_ids is not None:
        data['accent_soil_ids'] = args.accent_soil_ids
    if args.sme2_map:
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


if __name__ == "__main__":
    main()
