"""Audit six-station nearest ±30 minute UTC matches across acquisition time bounds.

Run through ``scripts/validate_eastern_washington.py`` after preparing
observations.

Optional --tolerance-minutes 10 produces a sensitivity audit; default remains 30.
Requires the existing native Zarr metadata and checksum-verified prepared CSV.
For each station/acquisition, test both outer source-time bounds. If both map to
the same unique sensor label within tolerance, every intervening source time does
too; no start/midpoint/weighted-time convention is needed. Otherwise leave the
pair unresolved. This conservative metadata audit does not establish spatial
coverage, validate field bytes, select contributing pixels or calculate errors.
Spokane's hourly values average the preceding hour; NRCS labels retain provider
support without inventing an averaging interval. Missing nearest values are never
replaced by farther valid observations. Moisture m3/m3; times UTC; no interpolation.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import pandas as pd
from insitu.coverage import digest
from insitu.matching import nearest_label
from insitu.validation import STATIONS


def run(run_dir, preparation_dir, output_dir, tolerance_minutes=30):
    """Write candidate temporal pairs, preserving unresolved and missing cases."""
    preparation = Path(preparation_dir)
    prep_path = preparation/'preparation.json'
    prep = json.loads(prep_path.read_text())
    csv_path = preparation/'prepared.csv'
    if digest(csv_path) != prep['outputs']['prepared.csv']:
        raise ValueError('Prepared CSV checksum mismatch')
    metadata_path = Path(run_dir)/'native.zarr/zarr.json'
    metadata = json.loads(metadata_path.read_text())['attributes']
    table = pd.read_csv(csv_path)
    records = []
    for station in STATIONS:
        rows = table[table.station_id == station].copy()
        if rows.empty:
            raise ValueError(f'Missing station: {station}')
        rows['time'] = pd.to_datetime(rows.time_utc, utc=True)
        rows = rows.sort_values('time').reset_index(drop=True)
        times = pd.DatetimeIndex(rows.time)
        for index, acquisition in enumerate(metadata['acquisitions']):
            sources = [metadata['sources'][i] for i in acquisition['source_indices']]
            start = min(pd.Timestamp(s['start_utc'], tz='UTC') for s in sources)
            end = max(pd.Timestamp(s['end_utc'], tz='UTC') for s in sources)
            first, first_status = nearest_label(times, start, tolerance=pd.Timedelta(minutes=tolerance_minutes))
            last, last_status = nearest_label(times, end, tolerance=pd.Timedelta(minutes=tolerance_minutes))
            record = dict(station_id=station, acquisition_index=index,
                          acquisition_id=acquisition['id'], source_start_utc=start.isoformat(),
                          source_end_utc=end.isoformat(), source_indices=acquisition['source_indices'],
                          bound_match_status=[first_status, last_status],
                          status='source_time_choice_requires_decision')
            if (first_status == last_status == 'outside_tolerance'
                    and (end < times[0] or start > times[-1])):
                record['status'] = 'outside_prepared_time_coverage'
            if (first_status == last_status == 'outside_tolerance'
                    and not ((times >= start) & (times <= end)).any()):
                record['status'] = 'outside_tolerance'
            if first is not None and first == last:
                row = rows.iloc[first]
                label = times[first]
                finite = pd.notna(row.soil_moisture_m3_m3)
                record.update(status='matched_finite' if finite else 'nearest_observation_missing',
                              sensor_time_utc=label.isoformat(),
                              soil_moisture_m3_m3=float(row.soil_moisture_m3_m3) if finite else None,
                              offset_from_source_start_seconds=(label-start).total_seconds(),
                              offset_from_source_end_seconds=(label-end).total_seconds(),
                              provider_flags=json.loads(row.provider_flags), reasons=json.loads(row.reasons),
                              sensor_time_support='preceding_60_minute_average' if station == 'USCRN.04136' else 'provider_hourly_label')
            records.append(record)
    summary = dict(status='temporal_candidates_only_spatial_extraction_pending',
                   tolerance_minutes=tolerance_minutes, tolerance_inclusive=True,
                   counts=dict(Counter(r['status'] for r in records)),
                   per_station={s:dict(Counter(r['status'] for r in records if r['station_id']==s)) for s in STATIONS},
                   excluded_stations=['ODSW', '824:WA:SNTL'], moisture_units='m3/m3', time_zone='UTC',
                   study_window_utc=prep['study_window_utc'],
                   identity=dict(preparation_sha256=digest(prep_path), prepared_csv_sha256=digest(csv_path),
                                 native_metadata_sha256=digest(metadata_path), code_sha256=digest(__file__),
                                 matching_code_sha256=digest(ROOT/'src/insitu/matching.py'),
                                 station_scope_code_sha256=digest(ROOT/'src/insitu/validation.py'),
                                 pixi_lock_sha256=digest(ROOT/'pixi.lock')))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output/'matches.json').write_text(json.dumps(records, indent=2, allow_nan=False)+'\n')
    summary['matches_sha256'] = digest(output/'matches.json')
    (output/'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(json.dumps(summary, indent=2))
