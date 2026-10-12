#!/usr/bin/env python3
"""Report native SME2 coverage for qualifying PROVISIONAL granules in a box.

Run from the repository root after pixi install:
    pixi run python scripts/summarize_nisar_sme2.py \
        --top-left 47.271621 -122.137915 --bottom-right 47.170149 -121.951705 \
        --min-numeric-percent 50 --output-dir outputs/enumclaw

Corners are WGS84 LATITUDE LONGITUDE in degrees; area is measured in the
equal-area EPSG:6933 projection, in km². Acquisition times and dates are UTC.
All available overlapping PROVISIONAL granules are queried without date limits.
The cache defaults to data/nisar; existing products are checksum-verified and
missing files are downloaded there. The public CMR catalog needs internet;
downloads additionally need exported EARTHDATA_USER and EARTHDATA_PASS.

Only native pixel centers inside or on the box boundary enter the denominator,
including centers outside an individual granule (which remain missing).
Selection requires strictly more than --min-numeric-percent (default 50) finite
soil-moisture cells in at least one candidate. Recommended coverage requires
finite moisture and a valid even candidate flag; combined flags are unused.
Each selected granule has equal weight in the means, including multiple products
on the same UTC date. Missing algorithms count as zero coverage. No resampling,
daily mosaicking, accuracy assessment or uncertainty calibration is performed.
The script prints its report and saves sme2_statistics.json in --output-dir.
"""

import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar.discovery import ensure_granules, search_box
from nisar.geometry import BoundingBox
from nisar.observations import read_frame, window_layout
from nisar.quality import ALGORITHMS, cell_counts, qualifies
from utils.download_stats import DownloadStats


def center_layout(view, reference):
    """Mask the shared native window to centers inside or on the requested WGS84 box."""
    layout = window_layout(view, reference)
    west, south, east, north = view.projected.bounds
    layout['mask'] = ((layout['xx'] >= west) & (layout['xx'] <= east)
                      & (layout['yy'] >= south) & (layout['yy'] <= north))
    if not layout['mask'].any():
        raise ValueError('The region contains no native pixel centers')
    return layout


def utc_timestamp(value):
    """Interpret native identification timestamps as UTC, with or without a Z suffix."""
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def summarize_frames(frames, mask, minimum_percent):
    """Select per granule, then average candidate coverage over the same full-region mask."""
    total = int(np.count_nonzero(mask))
    qualifies({}, total, minimum_percent)
    if not total:
        raise ValueError('The region contains no selected native cells')
    sums = {a: {'numeric': 0, 'recommended': 0} for a in ALGORITHMS}
    selected, dates, starts, ends = [], set(), [], []
    for frame in frames:
        counts = {a: cell_counts(frame['data'][a]['soilMoisture'],
                                 frame['data'][a]['retrievalQualityFlag'], mask)
                  for a in ALGORITHMS}
        if not qualifies({a: c['numeric_cells'] for a, c in counts.items()},
                         total, minimum_percent):
            continue
        metadata = frame['metadata']
        start = utc_timestamp(metadata['zeroDopplerStartTime'])
        end = utc_timestamp(metadata['zeroDopplerEndTime'])
        selected.append(metadata['granule'])
        starts.append(start)
        ends.append(end)
        dates.add(start.date())
        for algorithm, count in counts.items():
            sums[algorithm]['numeric'] += count['numeric_cells']
            sums[algorithm]['recommended'] += count['recommended_cells']
    denominator = total * len(selected)
    means = {
        algorithm: {
            'mean_numeric_percent': 100 * count['numeric'] / denominator if denominator else None,
            'mean_recommended_percent': 100 * count['recommended'] / denominator if denominator else None,
        } for algorithm, count in sums.items()
    }
    return {
        'coverage_denominator_cells': total,
        'selected_granules': len(selected),
        'distinct_utc_dates': len(dates),
        'acquisition_start_utc': min(starts).isoformat() if starts else None,
        'acquisition_end_utc': max(ends).isoformat() if ends else None,
        'algorithms': means,
        'selected_granule_names': sorted(selected),
    }


def parser():
    """Define the region, strict coverage cutoff, input cache and report destination."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--top-left', type=float, nargs=2, required=True, metavar=('LAT', 'LON'))
    p.add_argument('--bottom-right', type=float, nargs=2, required=True, metavar=('LAT', 'LON'))
    p.add_argument('--min-numeric-percent', type=float, default=50,
                   help='Strict > cutoff in at least one candidate; valid range [0, 100)')
    p.add_argument('--data-dir', type=Path, default=ROOT / 'data/nisar')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/nisar_statistics')
    return p


def main(argv=None):
    """Verify all overlapping products, summarize native cells, and publish one JSON report."""
    p = parser()
    args = p.parse_args(argv)
    try:
        view = BoundingBox.from_corners(args.top_left, args.bottom_right)
        qualifies({}, 1, args.min_numeric_percent)
    except ValueError as error:
        p.error(str(error))
    stats = DownloadStats()
    try:
        granules, provenance = search_box(view.bounds)
        if not granules:
            raise ValueError('No overlapping PROVISIONAL granules were found')
        paths = ensure_granules(granules, args.data_dir, stats)
        first = next(paths)
        with h5py.File(first) as reference:
            layout = center_layout(view, reference)

        def frames():
            yield read_frame(first, layout)
            for path in paths:
                yield read_frame(path, layout)

        summary = summarize_frames(frames(), layout['mask'], args.min_numeric_percent)
        selected = set(summary['selected_granule_names'])
        report = dict(
            schema='nisar-sme2-statistics-v1',
            bounds_wgs84=dict(zip(('west', 'south', 'east', 'north'), view.bounds)),
            area_km2=view.projected.area / 1e6, area_crs='EPSG:6933',
            native_pixel_centers=summary['coverage_denominator_cells'],
            min_numeric_percent=args.min_numeric_percent,
            coverage_rule='Native centers inside or on the box; strictly greater than threshold in at least one candidate',
            recommended_rule='Finite soil moisture and valid even candidate retrievalQualityFlag',
            mean_weighting='Equal weight per selected granule; missing candidates count as zero coverage',
            **summary,
            provenance=dict(
                provenance, archive_checksums_verified=True,
                selected_checksums={g.name: {'algorithm': g.checksum_algorithm, 'value': g.checksum}
                                    for g in granules if g.name in selected}),
            download=dict(bytes=stats.bytes_received, seconds=stats.transfer_seconds),
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output = args.output_dir / 'sme2_statistics.json'
        temporary = output.with_suffix('.json.tmp')
        try:
            temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'Area: {report["area_km2"]:.3f} km² (EPSG:6933); '
              f'{report["native_pixel_centers"]:,} native pixel centers')
        print(f'Selected: {summary["selected_granules"]}/{len(granules)} granules; '
              f'{summary["distinct_utc_dates"]} distinct UTC dates')
        print(f'Acquisition span (UTC): {summary["acquisition_start_utc"]} — {summary["acquisition_end_utc"]}')
        for algorithm, coverage in summary['algorithms'].items():
            numeric, recommended = coverage.values()
            description = (f'{numeric:.2f}% numeric; {recommended:.2f}% recommended'
                           if numeric is not None else 'n/a (no selected granules)')
            print(f'{algorithm}: {description}')
        print(f'Saved {output}', flush=True)
        return 0
    except HTTPError as error:
        print(f'HTTP {error.code}: catalog or product request failed; '
              'for 401/403 check Earthdata credentials and authorization.', file=sys.stderr)
    except (URLError, TimeoutError, ConnectionError, HTTPException):
        print('Network request failed; rerun to reuse verified downloads.', file=sys.stderr)
    except (ValueError, KeyError, RuntimeError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
    finally:
        stats.report()
    return 1


if __name__ == '__main__':
    sys.exit(main())
