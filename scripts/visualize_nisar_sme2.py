#!/usr/bin/env python3
r"""Download and visualize all qualifying NISAR SME2 PROVISIONAL granules in a box.

Run from the repository root after pixi install:
    pixi run python scripts/visualize_nisar_sme2.py \
        --top-left 47.271621 -122.137915 --bottom-right 47.170149 -121.951705 \
        --output-dir outputs/enumclaw --output-prefix enumclaw --title Enumclaw

Corners are LATITUDE LONGITUDE in WGS84 degrees. Non-antimeridian-crossing boxes
within 86° S–86° N are supported by the native EPSG:6933 grid. All available
PROVISIONAL collections/dates are queried unless inclusive UTC --start-date or
--end-date limits are given.

--nisar-combine-window defaults to 10 minutes. Same-track granules are mosaicked
before coverage filtering when their UTC acquisition starts fall within this
inclusive window from the group's earliest start (no successive-gap chaining).
Use 0 to keep every granule separate. Each qualifying group becomes one frame.
Per algorithm/cell, earliest finite moisture wins with its uncertainty and flag;
filename breaks equal-start ties. If all moisture is missing, the earliest tuple
with a finite uncertainty or flag is retained. No averaging occurs. Original
HDF5 files remain separate; all source identities are retained in provenance.

--data-dir defaults to data/nisar. Existing HDF5 files are checksum-verified;
missing originals are downloaded there atomically. Downloads need exported
EARTHDATA_USER and EARTHDATA_PASS. Credentials are never written or printed.
The public CMR catalog and Esri basemap need internet, including on cached runs.
--list-only reports matching catalog granules without downloading or rendering.

--min-numeric-percent defaults to 50: at least one candidate algorithm must have
STRICTLY MORE than this percentage of finite soil-moisture cells across the
whole viewing box. Partial edge cells count equally, outside-granule cells count
as missing, and flags/uncertainties do not affect eligibility. 0 retains any
nonempty group; the valid threshold range is [0,100). Full native HDF5 products
must be downloaded to evaluate numeric coverage; excluded files remain cached.

--formats selects gif, png and/or html (default: gif png). Examples:
    --formats html             # HTML + sibling <prefix>_assets/ PNG directory
    --formats gif png html     # All three outputs from the same qualifying set

Outputs: <prefix>.gif, <prefix>_topographic.png, <prefix>.html as selected.
--output-dir overrides the directory of --output-prefix, preserving its name;
without it, --output-prefix defaults to outputs/nisar_sme2.
HTML always uses an assets folder; keep it beside the HTML when moving files.
Its dashed box, six-field dropdown, acquisition-group time control and candidate
flag checkbox match the GIF semantics. No seventh combined-flag layer exists.
The HTML carries contributing-track metadata for build_gaia_map.py --sme2-map.
All outputs carry provenance; HTML viewing needs internet for Leaflet and tiles.
The GIF has PMI/TSR/DSG moisture and reported uncertainty in m³/m³. Subtitles show
numeric and recommended percentages; recommended means finite displayed value
and valid even candidate flag. Odd candidate flags receive red borders, including
on missing-valued cells; combined flags are not visualized. Fixed scales default
to 0–0.60 moisture and 0–0.08 uncertainty; configurable triangular overflow ends
match endpoint colors. There is no interpolation or uncertainty recalibration.

The topographic PNG uses the same box with Esri source attribution; basemap dates
vary. Workflow and rendering helpers reside in scripts/nisar_visualization/;
shared ingestion, geometry and quality primitives reside in src/.
"""

import argparse
from http.client import HTTPException
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar.discovery import date_limits, search_box
from nisar.geometry import BoundingBox
from maps.common import bounds_label
from nisar_visualization.gif import render_gif
from nisar_visualization.topographic import render_topographic
from nisar_visualization.web import render_html
from nisar_visualization.workflow import (DEFAULT_COMBINE_WINDOW, prepare_frames, frame_tracks,
                                          validate_combine_window)
from nisar.quality import qualifies
from nisar_visualization.style import DEFAULT_LIMITS
from utils.download_stats import DownloadStats


def parser():
    """Define geographic inputs and explicit workflow/display configuration."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--top-left', type=float, nargs=2, required=True, metavar=('LAT', 'LON'))
    p.add_argument('--bottom-right', type=float, nargs=2, required=True, metavar=('LAT', 'LON'))
    p.add_argument('--data-dir', type=Path, default=ROOT / 'data/nisar')
    p.add_argument('--output-prefix', type=Path, default=ROOT / 'outputs/nisar_sme2')
    p.add_argument('--output-dir', type=Path,
                   help='Directory for all visuals and HTML assets; overrides the prefix directory')
    p.add_argument('--formats', nargs='+', choices=('gif', 'png', 'html'), default=['gif', 'png'],
                   help='Outputs to generate; HTML always uses a sibling assets folder')
    p.add_argument('--title', default='NISAR SME2 viewing box')
    p.add_argument('--min-numeric-percent', type=float, default=50)
    p.add_argument('--nisar-combine-window', type=float, default=DEFAULT_COMBINE_WINDOW,
                   metavar='MINUTES', help='Maximum same-track acquisition-start span in minutes '
                   '(default: 10; 0 disables combining); applied before coverage filtering')
    p.add_argument('--start-date', help='First acquisition date, inclusive UTC (YYYY-MM-DD)')
    p.add_argument('--end-date', help='Last acquisition date, inclusive UTC (YYYY-MM-DD)')
    p.add_argument('--moisture-limits', type=float, nargs=2, default=DEFAULT_LIMITS[0], metavar=('MIN', 'MAX'))
    p.add_argument('--uncertainty-limits', type=float, nargs=2, default=DEFAULT_LIMITS[1], metavar=('MIN', 'MAX'))
    p.add_argument('--frame-seconds', type=float, default=1)
    p.add_argument('--basemap-size', type=int, default=1600, help='Longest source basemap image side in pixels')
    p.add_argument('--list-only', action='store_true')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if args.output_dir is not None:
        args.output_prefix = args.output_dir / args.output_prefix.name
    try:
        view = BoundingBox.from_corners(args.top_left, args.bottom_right)
        date_limits(args.start_date, args.end_date)
        qualifies({}, 1, args.min_numeric_percent)
        validate_combine_window(args.nisar_combine_window)
        for limits in (args.moisture_limits, args.uncertainty_limits):
            if not np.isfinite(limits).all() or limits[0] >= limits[1]:
                raise ValueError('Color limits must be finite and increasing')
        if not np.isfinite(args.frame_seconds) or not .01 <= args.frame_seconds <= 655.35:
            raise ValueError('GIF frame seconds must be between 0.01 and 655.35')
        if not 1 <= args.basemap_size <= 4096:
            raise ValueError('Basemap size must be between 1 and 4096 pixels')
    except ValueError as error:
        p.error(str(error))
    stats = DownloadStats()
    try:
        granules, provenance = search_box(view.bounds, args.start_date, args.end_date)
        print(f'Catalog: {len(granules)} overlapping PROVISIONAL granules; WGS84 {bounds_label(view)}', flush=True)
        if args.list_only:
            for granule in granules:
                print(f'{granule.start.isoformat()}  {granule.name}')
            return 0
        layout, frames, provenance = prepare_frames(
            view, granules, args.data_dir, args.min_numeric_percent, provenance, stats,
            combine_window=args.nisar_combine_window)
        limits = (args.moisture_limits, args.uncertainty_limits)
        if 'gif' in args.formats:
            render_gif(layout, frames, Path(str(args.output_prefix) + '.gif'), args.title,
                       limits, round(args.frame_seconds * 100) * 10, provenance)
        if 'png' in args.formats:
            png = Path(str(args.output_prefix) + '_topographic.png')
            basemap = render_topographic(view, png, args.title, args.basemap_size)
            print(f'Saved {png}; basemap retrieved {basemap["retrieved_at_utc"]}; '
                  f'{basemap["download_bytes"]:,} response bytes in {basemap["download_seconds"]:.2f} s', flush=True)
        if 'html' in args.formats:
            render_html(view, layout, frames, Path(str(args.output_prefix) + '.html'), args.title,
                        limits, provenance, frame_tracks(granules, frames))
        return 0
    except HTTPError as error:
        print(f'HTTP {error.code}: catalog, product or basemap request failed; '
              'for 401/403 check Earthdata credentials and authorization.', file=sys.stderr)
    except (URLError, TimeoutError, ConnectionError, HTTPException):
        print('Network request failed; rerun to reuse verified downloads.', file=sys.stderr)
    except (ValueError, KeyError, RuntimeError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
    finally:
        if not args.list_only:
            stats.report()
    return 1


if __name__ == '__main__':
    sys.exit(main())
