"""Shared catalog, verified cache and coverage selection for SME2 visual formats.

Every output receives the same native EPSG:6933 window and qualifying granules.
Acquisition dates are UTC; coverage ignores flags and uncertainties.
Run through ``pixi run python scripts/visualize_nisar_sme2.py --help``.
"""

from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from nisar.quality import ALGORITHMS, coverage_counts, qualifies
from maps.nisar_footprints import tracks_from_items

from nisar.discovery import ensure_granules
from nisar.observations import native, read_frame, window_layout

DEFAULT_COMBINE_WINDOW = 10


def validate_combine_window(minutes):
    """Require a finite nonnegative window in minutes; zero disables combining."""
    if not np.isfinite(minutes) or minutes < 0:
        raise ValueError('NISAR combine window must be finite and nonnegative (minutes)')


def acquisition_time(value):
    """Normalize SME2 identification times to UTC, including zone-less HDF5 UTC times."""
    timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)


def group_paths(paths, minutes):
    """Group same-track starts within an inclusive first-to-last window, without chaining."""
    validate_combine_window(minutes)
    entries = []
    for path in paths:
        with h5py.File(path) as source:
            identity = source['science/LSAR/identification']
            start = acquisition_time(native(identity['zeroDopplerStartTime'][()]))
            track = int(identity['trackNumber'][()])
        entries.append((start, Path(path), track))
    entries.sort(key=lambda entry: (entry[0], entry[1].name))
    groups, active = [], {}
    for start, path, track in entries:
        previous = active.get(track)
        if minutes == 0 or previous is None or (start - previous[0]).total_seconds() / 60 > minutes:
            group = [path]
            groups.append(group)
            active[track] = (start, group)
        else:
            previous[1].append(path)
    return groups


def read_combined_frame(paths, layout):
    """Mosaic ordered native EPSG:6933 cells, retaining source measurement tuples.

    Per algorithm/cell, the first finite moisture wins along with its reported
    uncertainty (m³/m³) and flag, even if those companions are missing. Where all
    moisture is missing, preserve the earliest tuple with a finite flag or
    uncertainty, so flags over missing moisture remain visible. No averaging,
    resampling, or uncertainty recalibration occurs. Source pixels count the
    union of cells supplying finite moisture across algorithms for each source.
    """
    combined, sources = None, []
    fields = ('soilMoisture', 'soilMoistureUncertainty', 'retrievalQualityFlag')
    for path in paths:
        frame = read_frame(path, layout)
        supplied = np.zeros(layout['mask'].shape, dtype=bool)
        for algorithm in ALGORITHMS:
            incoming = frame['data'][algorithm]
            finite = np.isfinite(incoming['soilMoisture'])
            if combined is None:
                supplied |= finite
                continue
            target = combined['data'][algorithm]
            take_numeric = ~np.isfinite(target['soilMoisture']) & finite
            empty = ~np.logical_or.reduce([np.isfinite(target[field]) for field in fields])
            take = take_numeric | empty
            for field in fields:
                target[field][take] = incoming[field][take]
            target['available'] |= incoming['available']
            supplied |= take_numeric
        sources.append(dict(frame['metadata'], source_pixels=int((supplied & layout['mask']).sum())))
        if combined is None:
            combined = frame
    metadata = dict(combined['metadata'])
    if len(sources) > 1:
        for key in metadata.keys() - {'granule', 'zeroDopplerStartTime', 'zeroDopplerEndTime'}:
            values = list(dict.fromkeys(source[key] for source in sources))
            metadata[key] = values[0] if len(values) == 1 else values
        metadata['granule'] = f'{len(sources)} combined granules'
        metadata['zeroDopplerEndTime'] = max(
            (source['zeroDopplerEndTime'] for source in sources), key=acquisition_time)
    metadata['source_granules'] = sources
    combined['metadata'] = metadata
    combined['numeric_moisture_cells'] = sum(coverage_counts(combined, layout['mask']).values())
    return combined


def prepare_frames(view, granules, data_dir, minimum_percent, provenance, stats=None,
                   combine_window=DEFAULT_COMBINE_WINDOW):
    """Verify/cache inputs once and return a reusable lazy frame sequence."""
    if not granules:
        raise ValueError('No overlapping PROVISIONAL granules were found')
    paths = list(ensure_granules(granules, data_dir, stats))
    with h5py.File(paths[0]) as f:
        layout = window_layout(view, f)
    frames = GranuleFrames(paths, layout, minimum_percent, combine_window)
    print(f'{len(frames)}/{frames.group_count} acquisition groups qualify from {len(paths)} granules; '
          f'{layout["mask"].sum():,} native cells '
          f'in the full box; >{minimum_percent:g}% numeric in at least one algorithm.', flush=True)
    if not frames:
        raise ValueError('No acquisition groups pass the numeric coverage threshold; no visuals written')
    retained = {path.name for record in frames.records for path in record['paths']}
    provenance = dict(provenance, min_numeric_percent=minimum_percent,
                      nisar_combine_window_minutes=combine_window,
                      combine_rule='Same track; inclusive first-to-last UTC acquisition-start window; no chaining; zero disables combining',
                      overlap_rule='Earliest finite moisture per algorithm/cell, with its uncertainty and flag; filename breaks equal-start ties; if all moisture is missing, earliest nonempty tuple',
                      selection_rule='Combine before coverage filtering; qualifying groups ordered by UTC acquisition start and filename',
                      coverage_rule='Strictly greater than threshold in at least one candidate algorithm; all intersecting cells in the full box count equally',
                      qualifying_granules=len(retained), qualifying_frames=len(frames),
                      archive_checksums_verified=True,
                      source_checksums={g.name: {'algorithm': g.checksum_algorithm, 'value': g.checksum}
                                        for g in granules if g.name in retained})
    return layout, frames, provenance


def frame_tracks(granules, frames):
    """Build station-map-compatible metadata using only retained granules."""
    donors = {source['granule'].removesuffix('.h5'): source
              for record in frames.records for source in record['metadata']['source_granules']}
    return tracks_from_items([g.catalog_item for g in granules if g.name.removesuffix('.h5') in donors], donors)


class GranuleFrames:
    """Scan coverage once, then load retained frames lazily to bound array memory."""

    def __init__(self, paths, layout, minimum_percent=50, combine_window=DEFAULT_COMBINE_WINDOW):
        self.layout = layout
        self.records = []
        total = int(layout['mask'].sum())
        qualifies({}, total, minimum_percent)
        groups = group_paths(paths, combine_window)
        self.group_count = len(groups)
        for group in groups:
            frame = read_combined_frame(group, layout)
            counts = coverage_counts(frame, layout['mask'])
            if qualifies(counts, total, minimum_percent):
                self.records.append(dict(paths=group, metadata=frame['metadata'],
                                         numeric_moisture_cells=frame['numeric_moisture_cells']))

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        for record in self.records:
            yield read_combined_frame(record['paths'], self.layout)
