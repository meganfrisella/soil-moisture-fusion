#!/usr/bin/env python3
"""Report native SME2 coverage for qualifying PROVISIONAL granules in a box.

Run from the repository root after pixi install:
    pixi run python scripts/summarize_nisar_sme2.py \
        --top-left 47.271621 -122.137915 --bottom-right 47.170149 -121.951705 \
        --min-numeric-percent 50 --nisar-combine-window 10 --output-dir outputs/enumclaw

Corners are WGS84 LATITUDE LONGITUDE in degrees; area is measured in the
equal-area EPSG:6933 projection, in km². Acquisition times and dates are UTC.
All available overlapping PROVISIONAL granules are queried without date limits.
The cache defaults to data/nisar; existing products are checksum-verified and
missing files are downloaded there. The public CMR catalog needs internet;
downloads additionally need exported EARTHDATA_USER and EARTHDATA_PASS.

All native cells intersecting the box enter the denominator equally, including
partial edge cells and cells outside an individual granule (which remain missing).
Selection requires strictly more than --min-numeric-percent (default 50) finite
soil-moisture cells in at least one candidate. Recommended coverage requires
finite moisture and a valid even candidate flag; combined flags are unused.
--nisar-combine-window defaults to 10 minutes (0 disables combining). Same-track
acquisition starts within an inclusive first-to-last window are combined before
filtering, without chaining. Earliest finite moisture wins with its uncertainty
and candidate flag; filename breaks equal-start ties. Each selected combined
frame has equal weight in coverage means; missing algorithms count as zero coverage.
Counts distinguish original source granules from selected frames. UTC dates and
span include all selected source acquisitions. No resampling, averaging of
measurements, accuracy assessment or uncertainty calibration is performed.
Temporal intervals use any finite moisture, separately for PMI/TSR/DSG. Count
each source acquisition-start UTC date once per cell. Average successive date
gaps within each cell, then average equally over cells with at least two dates;
report days and the number of eligible cells. No interval spans beyond a cell's
first/last observation; quality flags do not filter this metric.
The script prints its report and saves sme2_statistics.json in --output-dir.
Mandatory R36 validation precedes decoding and R37 combined-native diagnostics
precede reporting, writing sme2_field_audit.json and sme2_diagnostics.json in
--output-dir. Existing matching QA is verified; changed inputs/configuration/code
require a new output directory. The approved version policy is VERSION_FIELDS
in regridding.inputs. Warnings preserve values and eligibility. This command
queries the catalog; prepare_nisar_sme2.py is the offline frozen-input path.
"""

import argparse
from http.client import HTTPException
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar.discovery import search_box
from nisar.geometry import BoundingBox
from nisar.selection import (DEFAULT_COMBINE_WINDOW, DEFAULT_MIN_NUMERIC_PERCENT,
                             acquisition_time, prepare_frames, validate_combine_window)
from nisar.quality import ALGORITHMS, cell_counts, qualifies
from ingest.download_stats import DownloadStats
from regridding.inputs import VERSION_FIELDS


def summarize_intervals(days, total):
    """Average per-cell UTC date gaps equally, excluding fewer than two observed dates."""
    first = np.zeros(total, dtype=np.int32)
    last = np.zeros(total, dtype=np.int32)
    count = np.zeros(total, dtype=np.int32)
    for day, packed in sorted(days.items()):
        observed = np.unpackbits(packed, count=total).astype(bool)
        first[observed & (count == 0)] = day
        last[observed] = day
        count[observed] += 1
    eligible = count >= 2
    means = (last[eligible] - first[eligible]) / (count[eligible] - 1)
    return {
        'mean_temporal_interval_days': float(means.mean()) if len(means) else None,
        'temporal_interval_cells': int(eligible.sum()),
    }


def summarize_frames(frames, mask):
    """Average already-selected frames equally and count their original UTC acquisitions."""
    total = int(np.count_nonzero(mask))
    if not total:
        raise ValueError('The region contains no selected native cells')
    sums = {a: {'numeric': 0, 'recommended': 0} for a in ALGORITHMS}
    observed_dates = {a: {} for a in ALGORITHMS}
    selected, dates, starts, ends = set(), set(), [], []
    selected_frames = 0
    for frame in frames:
        counts = {a: cell_counts(frame['data'][a]['soilMoisture'],
                                 frame['data'][a]['retrievalQualityFlag'], mask)
                  for a in ALGORITHMS}
        selected_frames += 1
        for source in frame['metadata']['source_granules']:
            start = acquisition_time(source['zeroDopplerStartTime'])
            end = acquisition_time(source['zeroDopplerEndTime'])
            selected.add(source['granule'])
            starts.append(start)
            ends.append(end)
            dates.add(start.date())
        for algorithm, count in counts.items():
            sums[algorithm]['numeric'] += count['numeric_cells']
            sums[algorithm]['recommended'] += count['recommended_cells']
            arrays = frame['data'][algorithm]
            numeric = np.isfinite(arrays['soilMoisture'][mask])
            source_days = arrays['source_day'][mask]
            for day in np.unique(source_days[numeric]):
                packed = np.packbits(numeric & (source_days == day))
                if day in observed_dates[algorithm]:
                    observed_dates[algorithm][day] |= packed
                else:
                    observed_dates[algorithm][day] = packed
    denominator = total * selected_frames
    means = {
        algorithm: {
            'mean_numeric_percent': 100 * count['numeric'] / denominator if denominator else None,
            'mean_recommended_percent': 100 * count['recommended'] / denominator if denominator else None,
            **summarize_intervals(observed_dates[algorithm], total),
        } for algorithm, count in sums.items()
    }
    return {
        'coverage_denominator_cells': total,
        'selected_granules': len(selected),
        'selected_frames': selected_frames,
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
    p.add_argument('--min-numeric-percent', type=float, default=DEFAULT_MIN_NUMERIC_PERCENT,
                   help='Strict > cutoff in at least one candidate; valid range [0, 100)')
    p.add_argument('--nisar-combine-window', type=float, default=DEFAULT_COMBINE_WINDOW,
                   metavar='MINUTES', help='Maximum same-track acquisition-start span in minutes '
                   '(default: 10; 0 disables combining); applied before coverage filtering')
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
        validate_combine_window(args.nisar_combine_window)
    except ValueError as error:
        p.error(str(error))
    stats = DownloadStats()
    try:
        granules, provenance = search_box(view.bounds)
        layout, frames, provenance = prepare_frames(
            view, granules, args.data_dir, args.min_numeric_percent, provenance, stats,
            combine_window=args.nisar_combine_window, qa_output_dir=args.output_dir,
            expected_versions=VERSION_FIELDS,
            command=['pixi', 'run', 'python', 'scripts/summarize_nisar_sme2.py',
                     *(sys.argv[1:] if argv is None else argv)])
        summary = summarize_frames(frames, layout['mask'])
        report = dict(
            schema='nisar-sme2-statistics-v2',
            bounds_wgs84=dict(zip(('west', 'south', 'east', 'north'), view.bounds)),
            area_km2=view.projected.area / 1e6, area_crs='EPSG:6933',
            min_numeric_percent=args.min_numeric_percent,
            nisar_combine_window_minutes=args.nisar_combine_window,
            coverage_rule=provenance['coverage_rule'],
            recommended_rule='Finite soil moisture and valid even candidate retrievalQualityFlag',
            mean_weighting='Equal weight per selected acquisition frame; missing candidates count as zero coverage',
            temporal_interval_rule='Any finite moisture, regardless of flags; unique source acquisition-start UTC dates per cell; '
                                   'mean consecutive date gap per cell, then equal-weight mean over cells with at least two dates; '
                                   'no extrapolation beyond each cell\'s first/last observation',
            **summary,
            provenance=provenance,
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
              f'{report["coverage_denominator_cells"]:,} intersecting native cells')
        print(f'Selected: {summary["selected_granules"]}/{len(granules)} source granules in {summary["selected_frames"]} frames; '
              f'{summary["distinct_utc_dates"]} distinct UTC dates')
        print(f'Acquisition span (UTC): {summary["acquisition_start_utc"]} — {summary["acquisition_end_utc"]}')
        for algorithm, coverage in summary['algorithms'].items():
            numeric = coverage['mean_numeric_percent']
            recommended = coverage['mean_recommended_percent']
            description = (f'{numeric:.2f}% numeric; {recommended:.2f}% recommended'
                           if numeric is not None else 'n/a (no selected granules)')
            print(f'{algorithm}: {description}')
            interval = coverage['mean_temporal_interval_days']
            interval_text = f'{interval:.2f} days' if interval is not None else 'n/a'
            print(f'  Mean per-cell interval: {interval_text}; '
                  f'{coverage["temporal_interval_cells"]:,} cells with at least two UTC dates')
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
