"""Prepare shallow station observations from the frozen coverage cache, offline.

Run from the repository root via scripts/validate_eastern_washington.py.
prepare_series(records, start, end, factor, constant_hours=12) keeps the first
source-order duplicate, sorts UTC timestamps, and retains irregular observations.
The audited requests are hourly: absent top-of-hour labels are explicit NaN rows;
no interpolation or resampling of existing values occurs. Gaps/missing values
break exact constant runs. All members of runs spanning >=12 elapsed hours are
masked as NaN by the standard preparation rule, including the first member.

Known volumetric percent is divided by 100; m3/m3 is unchanged. The user selected
Odessa raw values as percentages (factor 0.01), following its catalog percent
label; unit conversion does not establish sensor calibration. Exact zero
values become NaN with a zero_value reason, independently of constant-run masking. Provider flags are kept;
complete error-flag mapping remains a gate before validation. Values outside [0,1]
are flagged only. All consecutive observed changes/rates are logged for choosing
an abrupt-jump threshold, not silently classified with a statistical cutoff.

Outputs: prepared.csv (NaN is the missing-value token), changes.jsonl (JSON null
for unavailable numeric values), and preparation.json (identity, station summaries,
constant runs and remaining decisions). Source observations/caches are unchanged.
Coordinates are inventoried EPSG:4326; depth 5/5.08 cm; timestamps UTC within the
frozen June–October 2026 study window, with provider averaging conventions retained.
The constant-period mask is a fixed rule, not a diagnosis of sensor failure.
Other source-calibration limits remain. No uncertainties, scores, new fits or
figures are produced.
"""

from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path

from insitu.coverage import (ROOT, digest, hours, awdb_records, uscrn_records,
                             agrimet_records, requests_for)
from maps.station_catalogs import Catalog


def finite(value):
    """Recognize decoded missing/nonfinite values without mistaking zero for missing."""
    return value is not None and math.isfinite(value)


def prepare_series(records, start, end, factor, constant_hours=12):
    """Return reversible prepared rows, change events and exact constant-period diagnostics."""
    if constant_hours != 12:
        raise ValueError('Current approved constant threshold is 12 elapsed hours')
    if factor not in (None, 1., .01):
        raise ValueError('Require verified fraction/percent conversion or unresolved units')
    if start.utcoffset() is None or end.utcoffset() is None or start > end:
        raise ValueError('Require ordered timezone-aware study bounds')
    first, events = {}, []
    for index, (time, value, flags) in enumerate(records):
        if time.utcoffset() is None:
            raise ValueError('Timezone-aware observations required')
        time = time.astimezone(timezone.utc)
        if not start <= time <= end:
            continue
        if time in first:
            events.append(dict(action='duplicate_discarded', time_utc=time.isoformat(),
                               retained_source_index=first[time][0], discarded_source_index=index,
                               discarded_value=value if finite(value) else None, discarded_flags=flags))
        else:
            first[time] = (index, value, flags)
    rows = []
    for time in sorted(set(hours(start, end)) | set(first)):
        supplied = time in first
        index, raw, flags = first.get(time, (None, None, {}))
        reasons = []
        if not supplied:
            reasons.append('missing_scheduled_observation')
        elif not finite(raw):
            reasons.append('reported_missing_or_nonfinite')
        if finite(raw) and factor is None:
            reasons.append('unit_unresolved')
        normalized = raw*factor if finite(raw) and factor is not None else math.nan
        rows.append(dict(time_utc=time.isoformat(), source_index=index, observed=supplied,
                         raw_value=raw if finite(raw) else None, provider_flags=flags,
                         soil_moisture_m3_m3=normalized, reasons=reasons,
                         outside_physical_fraction_bounds=finite(normalized) and not 0 <= normalized <= 1))
    periods, run = [], []

    def finish():
        if len(run) < 2:
            return
        duration = (datetime.fromisoformat(rows[run[-1]]['time_utc'])-
                    datetime.fromisoformat(rows[run[0]]['time_utc'])).total_seconds()/3600
        if duration < constant_hours:
            return
        number = len(periods)
        periods.append(dict(period_index=number, start_utc=rows[run[0]]['time_utc'],
                            end_utc=rows[run[-1]]['time_utc'], elapsed_hours=duration,
                            observation_count=len(run), raw_value=rows[run[0]]['raw_value'],
                            mask_status='masked_by_policy'))
        for i in run:
            rows[i]['soil_moisture_m3_m3'] = math.nan
            rows[i]['reasons'].append('constant_period_12h_mask')
            rows[i]['constant_period_index'] = number

    for i, row in enumerate(rows):
        if not finite(row['raw_value']):
            finish(); run = []
        elif run and row['raw_value'] != rows[run[-1]]['raw_value']:
            finish(); run = [i]
        else:
            run.append(i)
    finish()
    changes = []
    previous = None
    for row in rows:
        # Missing scheduled labels break comparisons; do not call a gap an abrupt jump.
        if finite(row['raw_value']):
            if previous is not None:
                dt = (datetime.fromisoformat(row['time_utc'])-
                      datetime.fromisoformat(previous['time_utc'])).total_seconds()/3600
                delta = row['raw_value']-previous['raw_value']
                if delta:
                    changes.append(dict(start_utc=previous['time_utc'], end_utc=row['time_utc'],
                                        elapsed_hours=dt, raw_delta=delta,
                                        delta_m3_m3=delta*factor if factor is not None else None,
                                        rate_m3_m3_per_hour=delta*factor/dt if factor is not None else None,
                                        classification='unclassified_pending_jump_threshold'))
            previous = row
        else:
            previous = None
        if row['raw_value'] == 0:
            row['soil_moisture_m3_m3'] = math.nan
            row['reasons'].append('zero_value')
        if row['reasons'] or row['outside_physical_fraction_bounds']:
            events.append({**row, 'action':'prepared_row',
                           'soil_moisture_m3_m3':row['soil_moisture_m3_m3'] if finite(row['soil_moisture_m3_m3']) else None})
    return rows, events, periods, changes


def run(inventory_path, coverage_path, cache_dir, output_dir):
    """Verify cached inputs, prepare every inventoried station and retain provenance/logs."""
    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError('Use a new output directory; prepared outputs are never overwritten')
    inventory = json.loads(Path(inventory_path).read_text())
    audit = json.loads(Path(coverage_path).read_text())
    if digest(inventory_path) != audit['inventory_sha256']:
        raise ValueError('Inventory differs from frozen coverage audit')
    start, end = (datetime.fromisoformat(inventory['study_window_utc'][k]) for k in ('start', 'end'))
    cache = Catalog(cache_dir)
    reports, all_rows, all_events = [], [], []
    for station, audited in zip(inventory['stations'], audit['stations'], strict=True):
        if station['id'] != audited['id'] or station['preferred_depth_cm'] not in (5., 5.08):
            raise ValueError('Station order/approved depth mismatch')
        records, metadata = [], []
        for address in audited['requests']:
            # cross-check requests may include metadata/daily endpoints; parse only
            # the original hourly requests recomputed by the existing selector.
            if address not in requests_for(station, start, end):
                continue
            cached = Path(cache_dir)/(hashlib.sha256(address.encode()).hexdigest()+'.json')
            if not cached.exists():
                raise FileNotFoundError(f'Frozen response absent; no implicit refresh: {address}')
            body = cache.get(address)
            if cache.responses[address]['sha256'] != audit['responses'][address]['sha256']:
                raise ValueError('Cached source differs from frozen coverage audit')
            if station['network'] in ('SCAN', 'SNTL'):
                if json.loads(body) == []:
                    continue
                parsed, detail = awdb_records(body, station)
            elif station['network'] == 'USCRN':
                parsed, detail = uscrn_records(body)
            else:
                parsed, detail = agrimet_records(body)
            records.extend(parsed); metadata.append(detail)
        factor = 1. if station['network'] == 'USCRN' else .01
        rows, events, periods, changes = prepare_series(records, start, end, factor)
        for row in rows:
            all_rows.append(dict(station_id=station['id'], **row))
        all_events.extend(dict(station_id=station['id'], **event) for event in events)
        all_events.extend(dict(station_id=station['id'], action='observed_change', **change) for change in changes)
        reports.append(dict(station_id=station['id'], name=station['name'],
                            latitude=station['latitude'], longitude=station['longitude'],
                            depth_cm=station['preferred_depth_cm'], channel_metadata=metadata,
                            conversion_factor=factor, normalized_units='m3/m3',
                            conversion_basis='user_selected_catalog_percent' if station['network']=='AgriMet' else 'provider_documented_units',
                            rows=len(rows), finite_prepared=sum(finite(r['soil_moisture_m3_m3']) for r in rows),
                            reason_counts=dict(Counter(reason for r in rows for reason in r['reasons'])),
                            duplicate_count=sum(e['action']=='duplicate_discarded' for e in events),
                            constant_periods=periods, changes_recorded=len(changes),
                            largest_changes=sorted(changes, key=lambda c:abs(c['raw_delta']), reverse=True)[:10],
                            physical_range_flag_count=sum(r['outside_physical_fraction_bounds'] for r in rows),
                            source_warnings=audited.get('warnings', [])))
    root.mkdir(parents=True, exist_ok=True)
    with (root/'prepared.csv').open('w', newline='') as f:
        columns = ['station_id', 'time_utc', 'source_index', 'observed', 'raw_value', 'provider_flags',
                   'soil_moisture_m3_m3', 'reasons', 'outside_physical_fraction_bounds', 'constant_period_index']
        writer = csv.DictWriter(f, fieldnames=columns); writer.writeheader()
        for row in all_rows:
            writer.writerow({**row, 'soil_moisture_m3_m3':row['soil_moisture_m3_m3'] if finite(row['soil_moisture_m3_m3']) else 'NaN',
                             'provider_flags':json.dumps(row['provider_flags']), 'reasons':json.dumps(row['reasons'])})
    with (root/'changes.jsonl').open('w') as f:
        for event in all_events:
            f.write(json.dumps(event, allow_nan=False)+'\n')
    review = ['# Station preparation review', '',
              f"UTC window: {start.isoformat()} through {end.isoformat()}.",
              'Shallow 5/5.08 cm observations; station coordinates EPSG:4326. Normalized moisture is m³/m³.',
              'Exact constant periods spanning at least 12 elapsed hours are masked by the standard preparation rule; individual review is not required.',
              'All members of exact runs spanning at least 12 elapsed hours are NaN; originals remain in prepared.csv and the source cache.',
              'Missing hourly labels are NaN, duplicates retain the first source entry, and irregular timestamps are retained.',
              'Odessa raw percentages are divided by 100 to obtain m³/m³, per user instruction. Exact zeros are NaN.', '',
              '| Station | Missing scheduled | Reported missing | Constant periods | Constant observations | Finite prepared |',
              '|---|---:|---:|---:|---:|---:|']
    for station in reports:
        counts = station['reason_counts']
        review.append(f"| {station['name']} | {counts.get('missing_scheduled_observation',0)} | {counts.get('reported_missing_or_nonfinite',0)} | {len(station['constant_periods'])} | {counts.get('constant_period_12h_mask',0)} | {station['finite_prepared']} |")
    review += ['', '## Constant periods masked by the rule', '',
               'Raw values below retain provider units: NRCS percent, NOAA m³/m³, Odessa percent.',
               'IDs are station ID / zero-based period index. Duration is last minus first timestamp, not observation count.', '',
               '| Station / period | Start UTC | End UTC | Elapsed hours | Observations | Raw value |',
               '|---|---|---|---:|---:|---:|']
    for station in reports:
        for period in station['constant_periods']:
            review.append(f"| {station['station_id']} / {period['period_index']} | {period['start_utc']} | {period['end_utc']} | {period['elapsed_hours']:g} | {period['observation_count']} | {period['raw_value']} |")
    review += ['', '## Other source limitations', '',
               '- Odessa percent interpretation is user-selected; provider calibration and Spokane coordinates remain unresolved. Historical source warnings are retained.',
               '- Complete provider QC/QA error mapping; raw suspect/edited/no-profile flags are retained, not all certified good.',
               '- All nonzero adjacent changes and elapsed-time rates are in changes.jsonl. An abrupt-jump cutoff is not selected.',
               '- Values outside [0,1] m³/m³ are flagged, never clipped. Local unusual-range thresholds are not selected.',
               '- Freeze/snow screening, calibration independence, matching and minimum validation coverage remain unresolved.', '']
    (root/'review.md').write_text('\n'.join(review))
    report = dict(state='prepared', study_window_utc=inventory['study_window_utc'],
                  coordinate_crs='EPSG:4326', moisture_units='m3/m3', missing_value='NaN in CSV; null in JSON',
                  constant_threshold_elapsed_hours=12, constant_equality='exact raw reported values',
                  constant_period_policy='mask_all_members',
                  duplicate_policy='first entry in source request/response order',
                  zero_policy='exact zero becomes NaN', sharp_jump_policy='retained',
                  odessa_scale_policy='user-selected raw percent; factor 0.01; catalog percent followed',
                  gap_policy='absent requested hourly labels added as NaN; irregular observations retained',
                  pending=['Odessa provider calibration (percent interpretation selected by user)',
                           'Complete provider erroneous-flag mapping', 'Unusual-range warning criteria; sharp jumps retained by user instruction',
                           'Coordinates, freeze screening, collocation and validation eligibility'],
                  identity=dict(inventory_sha256=digest(inventory_path), coverage_sha256=digest(coverage_path),
                                sources=cache.responses, code={p:digest(ROOT/p) for p in
                                ('src/insitu/preparation.py', 'scripts/validate_eastern_washington.py', 'src/insitu/coverage.py',
                                 'src/maps/station_catalogs.py', 'src/ingest/downloads.py', 'pixi.lock')}),
                  stations=reports, outputs={p:digest(root/p) for p in ('prepared.csv', 'changes.jsonl', 'review.md')})
    (root/'preparation.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report
