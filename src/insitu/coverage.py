"""Reproducible R28 station availability audit and coverage presentation.

Run through scripts/validate_eastern_washington.py; run(inventory, cache_dir, output_dir)
uses only explicitly inventoried stations and their shallow catalog channels.
Locations are EPSG:4326 degrees. NRCS SMS:-2:1 is percent at 5.08 cm,
USCRN SOIL_MOISTURE_5 is m3/m3 at 5 cm, and ODSW XSM2 is cataloged percent
at 5.08 cm (raw scale/calibration unverified; no conversion is applied).
Hourly timestamps are converted from NRCS fixed catalog offset, NOAA UTC and
AgriMet America/Los_Angeles. Counts cover hourly time labels inside the exact
inventory UTC window; they are NOT satellite-time matches. USCRN values are
hour-ending averages; AgriMet requests top-of-hour samples, not hourly means.

Reports all provider-returned finite moisture, retaining NRCS suspect values
and QC flags. Only documented null/missing encodings are absent. No new QC,
depth eligibility, imputation, minimum sample count, collocation or inference
rule is selected. Raw responses retain all other channels/flags/version info.
Duplicates/conflicts and irregular timestamps fail rather than inflate counts.
Request/parser failures are unverified, never interpreted as zero coverage.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np
from maps.station_catalogs import Catalog, url

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
AWDB = 'https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data'
USCRN_ROOT = 'https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/'
AGRIMET = 'https://www.usbr.gov/pn-bin/instant.pl'
DOCS = {
    'awdb_api': 'https://wcc.sc.egov.usda.gov/awdbRestApi/v3/api-docs',
    'uscrn': USCRN_ROOT + 'README.txt',
    'agrimet_form': 'https://www.usbr.gov/pn/agrimet/webaghrread.html',
    'agrimet_time': 'https://www.usbr.gov/pn/hydromet/using_dfcgi.html',
    'agrimet_channels': 'https://www.usbr.gov/pn/agrimet/aginfo/station_params.html',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hours(start, end):
    """Return expected hourly timestamp labels within inclusive UTC bounds."""
    first = start.replace(minute=0, second=0, microsecond=0)
    if first < start:
        first += timedelta(hours=1)
    return [first + timedelta(hours=i) for i in range(int((end-first).total_seconds()//3600)+1)]


def runs(times, predicate):
    """Return inclusive runs of consecutive hourly labels satisfying predicate."""
    result, current = [], []
    for time in times:
        if predicate(time):
            current.append(time)
        elif current:
            result.append(current)
            current = []
    if current:
        result.append(current)
    return [dict(start_utc=r[0].isoformat(), end_utc=r[-1].isoformat(), hours=len(r)) for r in result]


def summarize(records, start, end):
    """Count unique finite labels, with gaps and flags, without QC selection."""
    expected = hours(start, end)
    values = {}
    for time, value, flags in records:
        if start <= time <= end:
            if time in values:
                raise ValueError('Duplicate timestamp; resolve before reporting coverage')
            if time.minute or time.second or time.microsecond:
                raise ValueError('Nonhourly timestamp in hourly audit')
            values[time] = (value, flags)
    present = {t for t, (v, _) in values.items() if v is not None and math.isfinite(v)}
    flag_counts = Counter(json.dumps(flags, sort_keys=True) for t, (v, flags) in values.items() if t in present)
    day_counts, day_expected = Counter(t.date().isoformat() for t in present), Counter(t.date().isoformat() for t in expected)
    finite = [values[t][0] for t in sorted(present)]
    gaps = runs(expected, lambda t: t not in present)
    flat_max, flat, previous = 0, 0, None
    for t in expected:
        v = values[t][0] if t in present else None
        flat = (flat+1 if v == previous else 1) if v is not None else 0
        flat_max, previous = max(flat_max, flat), v
    return dict(expected_hours=len(expected), returned_hours=len(values), finite_hours=len(present),
                finite_percent=100*len(present)/len(expected),
                first_finite_utc=min(present).isoformat() if present else None,
                last_finite_utc=max(present).isoformat() if present else None,
                days_any=len(day_counts), days_total=len(day_expected),
                daily=[dict(date=d, finite_hours=day_counts[d], expected_hours=n) for d, n in day_expected.items()],
                missing_runs=gaps, longest_missing_hours=max((r['hours'] for r in gaps), default=0),
                minimum_reported=min(finite) if finite else None, maximum_reported=max(finite) if finite else None,
                longest_exact_constant_hours=flat_max, flags_on_finite=dict(flag_counts))


def awdb_records(body, station):
    """Decode only the exact shallow channel; do not drop suspect finite values."""
    data = json.loads(body)
    if len(data) != 1 or data[0]['stationTriplet'] != station['id']:
        raise ValueError('Unexpected AWDB station response')
    matching = [d for d in data[0]['data'] if d['stationElement']['elementCode'] == 'SMS'
                and d['stationElement']['heightDepth'] == -2 and d['stationElement']['ordinal'] == 1]
    if len(matching) != 1 or matching[0].get('error'):
        raise ValueError('Missing or failed AWDB shallow channel')
    channel = matching[0]['stationElement']
    if channel['durationName'] != 'HOURLY' or channel['storedUnitCode'] != 'pct':
        raise ValueError('AWDB channel duration/units changed')
    tz = timezone(timedelta(hours=station['catalog_time_zone_utc_offset_hours']))
    records = [(datetime.fromisoformat(v['date']).replace(tzinfo=tz).astimezone(UTC),
                v.get('value'), {k: v.get(k) for k in ('qcFlag', 'qaFlag')}) for v in matching[0]['values']]
    return records, dict(channel=channel, time_basis='fixed catalog UTC offset; no DST', source_units='percent')


def uscrn_records(body):
    """Decode documented NOAA hourly02 schema and -99.000 shallow missing code."""
    records, coords = [], set()
    for line in body.decode().splitlines():
        row = line.split()
        if len(row) != 38 or row[0] != '04136':
            raise ValueError('Unexpected USCRN hourly schema/station')
        time = datetime.strptime(row[1]+row[2].zfill(4), '%Y%m%d%H%M').replace(tzinfo=UTC)
        value = float(row[28])
        records.append((time, None if value == -99.0 else value, {}))
        coords.add((float(row[6]), float(row[7])))
    return records, dict(channel='SOIL_MOISTURE_5', time_basis='UTC hour-ending 60-minute average',
                         source_units='m3/m3', data_coordinates_lon_lat=sorted(coords))


def agrimet_records(body):
    """Parse top-of-hour XSM2 with provider flags retained and no unit conversion."""
    text = body.decode()
    if 'BEGIN DATA' not in text or 'END DATA' not in text:
        raise ValueError('AgriMet response lacks data boundaries')
    block = text.split('BEGIN DATA', 1)[1].split('END DATA', 1)[0].strip().splitlines()
    if 'ODSW' not in block[0] or 'XSM2' not in block[0]:
        raise ValueError('Unexpected AgriMet station/channel')
    records = []
    for line in block[1:]:
        date, raw = line.strip().split(',', 1)
        time = datetime.strptime(date.strip(), '%m/%d/%Y %H:%M').replace(tzinfo=ZoneInfo('America/Los_Angeles')).astimezone(UTC)
        raw = raw.strip()
        match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([A-Za-z*?]*)', raw)
        if match:
            value, flags = float(match[1]), {'suffix': match[2]}
        elif raw in ('', '---', '----', 'M', 'm'):
            value, flags = None, {'raw_missing': raw}
        else:
            raise ValueError(f'Unknown AgriMet encoding: {raw!r}')
        records.append((time, value, flags))
    return records, dict(channel='XSM2', source_units='catalog percent; scale/calibration unverified',
                         time_basis='America/Los_Angeles top-of-hour samples, not hourly averages')


def requests_for(station, start, end):
    """Build documented provider requests padded by one day around study bounds."""
    begin = (start-timedelta(days=1)).date()
    finish = (end+timedelta(days=1)).date()
    if station['network'] in ('SCAN', 'SNTL'):
        return [url(AWDB, stationTriplets=station['id'], elements='SMS:-2:1', duration='HOURLY',
                    beginDate=str(begin), endDate=str(finish), returnFlags='true',
                    returnSuspectData='true', periodRef='END')]
    if station['network'] == 'USCRN' and station['id'] == 'USCRN.04136':
        return [USCRN_ROOT+f'{year}/CRNH0203-{year}-WA_Spokane_17_SSW.txt' for year in range(start.year, end.year+1)]
    if station['id'] == 'ODSW':
        params = [('station', 'ODSW')]
        for d in (begin, finish):
            params.extend([('year', str(d.year)), ('month', str(d.month)), ('day', str(d.day))])
        params.extend([('pcode', 'XSM2'), ('print_hourly', '1')])
        return [AGRIMET+'?'+urlencode(params)]
    raise ValueError('Unsupported station; no silent network or channel substitution')


def crosscheck_empty_awdb(cache, rows, start, end):
    """Retain metadata and daily-query evidence for empty hourly responses."""
    empty = [r for r in rows if r.get('empty_hourly_response')]
    if not empty:
        return
    ids = ','.join(r['id'] for r in empty)
    metadata_url = url(AWDB.replace('/data', '/stations'), stationTriplets=ids,
                       returnStationElements='true')
    daily_url = url(AWDB, stationTriplets=ids, elements='SMS:-2:1', duration='DAILY',
                    beginDate=str((start-timedelta(days=1)).date()),
                    endDate=str((end+timedelta(days=1)).date()), returnFlags='true',
                    returnSuspectData='true', periodRef='END')
    metadata = {s['stationTriplet']: s for s in json.loads(cache.record('Empty hourly response metadata check', metadata_url))}
    daily = {s['stationTriplet']: s for s in json.loads(cache.record('Empty hourly response daily check', daily_url))}
    for row in empty:
        row['requests'] += [metadata_url, daily_url]
        channels = [c for c in metadata[row['id']]['stationElements']
                    if c['elementCode']=='SMS' and c['heightDepth']==-2 and c['ordinal']==1]
        row['empty_response_crosscheck'] = dict(current_catalog_channels=channels,
                                                daily_response=daily.get(row['id']))
        row['status'] = 'no_records_returned'
        row['warnings'] = [
            'Hourly request returned an empty list; shallow channel remains in current catalog. '
            + ('Daily cross-check also returned no station records. ' if row['id'] not in daily else 'Daily records require separate interpretation. ')
            + 'No study-window shallow records obtained; this does not establish sensor closure or prove the provider has no other records.']


def render(report, path):
    """Plot point locations and daily finite hourly fractions; gray means unverified."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    rows, bounds = report['stations'], report['bounds_wgs84']
    fig, (ax, tx) = plt.subplots(1, 2, figsize=(15, 7), gridspec_kw={'width_ratios': [1, 1.6]})
    colors = {'SCAN': '#00876c', 'SNTL': '#305fa3', 'USCRN': '#d88c00', 'AgriMet': '#bb4861'}
    w,s,e,n = (bounds[k] for k in ('west','south','east','north'))
    ax.add_patch(Rectangle((w,s),e-w,n-s, fill=False, color='black', lw=1))
    for i, row in enumerate(rows):
        ax.scatter(row['longitude'], row['latitude'], edgecolor=colors[row['network']],
                   facecolor=colors[row['network']] if row['status']=='verified_response' else 'none', s=50)
        ax.annotate(str(i+1), (row['longitude'], row['latitude']), xytext=(5,5), textcoords='offset points', fontsize=9)
    ax.set(xlim=(w-.15,e+.2), ylim=(s-.1,n+.15), xlabel='Longitude (WGS84 degrees)', ylabel='Latitude (WGS84 degrees)', title='Cataloged station locations')
    ax.set_aspect(1/math.cos(math.radians((s+n)/2)))
    ax.grid(alpha=.2)
    day_labels = [d['date'] for r in rows if r['status']=='verified_response' for d in r['coverage']['daily']][:report['days_total']]
    matrix = np.full((len(rows), len(day_labels)), np.nan)
    for i, row in enumerate(rows):
        if row['status']=='verified_response':
            matrix[i] = [d['finite_hours']/d['expected_hours'] for d in row['coverage']['daily']]
    cmap = plt.colormaps['viridis'].copy(); cmap.set_bad('#b0b0b0')
    im = tx.imshow(matrix, aspect='auto', vmin=0, vmax=1, cmap=cmap, interpolation='nearest')
    tx.set_yticks(range(len(rows)), [f'{i+1}. '+('Odessa AgriMet' if r['id']=='ODSW' else r['name']) for i,r in enumerate(rows)])
    ticks = sorted(set([0, len(day_labels)-1] + [i for i,d in enumerate(day_labels) if d.endswith('-01')]))
    tx.set_xticks(ticks, [day_labels[i][5:] for i in ticks], rotation=35, ha='right')
    tx.set(xlabel='UTC date, 2026', title='Daily fraction of hourly labels with reported moisture')
    fig.colorbar(im, ax=tx, label='Reported nonmissing fraction', shrink=.7)
    fig.suptitle('Eastern Washington near-surface station availability', fontsize=16)
    fig.text(.5,.035,'June 17–October 10, 2026 UTC • depths 5–5.08 cm • availability before validation QC\nPoint locations do not imply area coverage; no satellite-time matching. Gray / hollow = no records obtained or unverified.',ha='center',fontsize=10)
    fig.tight_layout(rect=(0,.09,1,.94)); fig.savefig(path, dpi=180); plt.close(fig)


def markdown(report):
    """Generate the requested human-readable report from the same audited counts."""
    lines = ['# Eastern Washington in-situ coverage audit', '',
             f'UTC study window: {report["study_window_utc"]["start"]} through {report["study_window_utc"]["end"]}.',
             f'Hourly timestamp denominator: {report["expected_hours"]}; UTC dates touched: {report["days_total"]}.',
             'Coverage means finite provider-returned shallow moisture, before validation QC. NRCS suspect data are retained. All positions below are the frozen EPSG:4326 catalog coordinates.', '',
             '| Station | Network | Lat, lon (degrees) | Depth cm | Finite / expected hours | Coverage | First–last finite UTC | Longest gap (hours) |',
             '|---|---|---|---:|---:|---:|---|---:|']
    for row in report['stations']:
        base=f'| {row["name"]} | {row["network"]} | {row["latitude"]:.5f}, {row["longitude"]:.5f} | {row["preferred_depth_cm"]} |'
        if row['status']!='verified_response':
            label='none returned' if row['status']=='no_records_returned' else 'unverified'
            lines.append(base+f' {label} | — | — | — |'); continue
        c=row['coverage']
        lines.append(base+f' {c["finite_hours"]}/{c["expected_hours"]} | {c["finite_percent"]:.2f}% | {c["first_finite_utc"]} – {c["last_finite_utc"]} | {c["longest_missing_hours"]} |')
    lines += ['', '## Spatial interpretation', '',
              'Counts by the WGS84 bounding-box midpoint quadrants (descriptive only; not the EPSG:6933 transfer folds): '+json.dumps(report['spatial_quadrant_counts'])+'.',
              'Stations with any retrieved finite shallow observation in those quadrants: '+json.dumps(report['spatial_reporting_counts'])+'.',
              'Western-half stations with retrieved observations: '+', '.join(
                  r['name']+' ('+str(r['coverage']['finite_hours'])+' finite hourly labels)'
                  for r in report['stations'] if r['status']=='verified_response'
                  and r['coverage']['finite_hours'] and r['longitude'] < (report['bounds_wgs84']['west']+report['bounds_wgs84']['east'])/2)+'.',
              'The stations are point samples. No station radius, domain percentage represented, or environmental representativeness is inferred.', '',
              '## Data warnings and limits', '']
    for row in report['stations']:
        for note in row.get('warnings',[]): lines.append(f'- {row["name"]}: {note}')
    lines += ['', 'No station has yet been approved under R29–R31. Calibration independence, sensor QC, minimum matched counts and point-to-cell mismatch remain unresolved. First/last dates inside this window do not establish continuous full historical operation. Missing runs, daily fractions, source URLs/checksums, flags and raw ranges are retained in coverage.json and the raw cache.', '',
              '![Spatial and temporal coverage](coverage.png)', '', '## Provider documentation', '']
    lines += [f'- [{key}]({value})' for key,value in DOCS.items()]
    return '\n'.join(lines)+'\n'


def run(inventory, cache_dir, output_dir):
    """Fetch missing raw products, verify cache, audit each station and write reports."""
    output_dir=Path(output_dir); output_dir.mkdir(parents=True,exist_ok=True)
    source=json.loads(Path(inventory).read_text())
    start,end=(datetime.fromisoformat(source['study_window_utc'][k]) for k in ('start','end'))
    expected=hours(start,end)
    cache=Catalog(cache_dir)
    report=dict(schema='insitu-coverage-v1', generated_utc=datetime.now(UTC).isoformat(),
                inventory_path=str(inventory), cache_dir=str(cache_dir),
                inventory_sha256=digest(inventory), study_window_utc=source['study_window_utc'],
                bounds_wgs84=source['bounds_wgs84'], expected_hours=len(expected),
                days_total=len({t.date() for t in expected}), stations=[], documentation=DOCS,
                code_sha256={str(p.relative_to(ROOT)):digest(p) for p in
                             [Path(__file__),ROOT/'scripts/validate_eastern_washington.py',ROOT/'src/maps/station_catalogs.py',ROOT/'src/ingest/downloads.py',ROOT/'pixi.lock']})
    for key,address in DOCS.items():
        cache.record(key,address)
    for station in source['stations']:
        row={k:station[k] for k in ('id','name','network','latitude','longitude','preferred_depth_cm')}
        row.update(status='unverified',warnings=[],requests=requests_for(station,start,end))
        try:
            records=[]; meta=[]
            for address in row['requests']:
                body=cache.record(station['name'],address)
                if station['network'] in ('SCAN','SNTL'):
                    row['empty_hourly_response'] = json.loads(body)==[]
                    parsed,details=awdb_records(body,station)
                elif station['network']=='USCRN': parsed,details=uscrn_records(body)
                else: parsed,details=agrimet_records(body)
                records.extend(parsed);meta.append(details)
            row.update(status='verified_response',channel_metadata=meta,coverage=summarize(records,start,end))
            c=row['coverage']
            if station['network']=='AgriMet':
                row['warnings'].append('XSM2 raw scale/calibration is not established by the percent channel label; no unit conversion or eligibility claim. Preliminary provider data.')
            if station['network']=='USCRN':
                coords=meta[0]['data_coordinates_lon_lat']
                if coords != [(station['longitude'],station['latitude'])]:
                    row['warnings'].append(f'Hourly-file coordinates {coords} differ from frozen inventory; resolve station coordinates before cell collocation.')
            row['warnings'].append(f'Raw range {c["minimum_reported"]}–{c["maximum_reported"]} ({meta[0]["source_units"]}); longest exactly unchanged run {c["longest_exact_constant_hours"]} hourly labels. Diagnostic only; no flatline rejection.')
            print(json.dumps(dict(station=station['name'],finite_hours=c['finite_hours'],expected_hours=c['expected_hours'],percent=c['finite_percent'])),flush=True)
        except (ValueError,KeyError,RuntimeError) as error:
            row['warnings'].append(f'Retrieval/decoding failed: {error}')
            print(json.dumps(dict(station=station['name'],error=str(error))),flush=True)
        report['stations'].append(row)
    crosscheck_empty_awdb(cache,report['stations'],start,end)
    b=source['bounds_wgs84']; midlat=(b['south']+b['north'])/2; midlon=(b['west']+b['east'])/2
    report['spatial_quadrant_counts']={q:0 for q in ('NW','NE','SW','SE')}
    report['spatial_reporting_counts']={q:0 for q in ('NW','NE','SW','SE')}
    for row in report['stations']:
        q=('N' if row['latitude']>=midlat else 'S')+('E' if row['longitude']>=midlon else 'W')
        report['spatial_quadrant_counts'][q]+=1
        if row['status']=='verified_response' and row['coverage']['finite_hours']:
            report['spatial_reporting_counts'][q]+=1
    report['responses']=cache.responses
    report['cached_raw_bytes_by_provider'] = {name: sum(
        len(cache.get(address)) for address in list(cache.responses) if domain in address)
        for name, domain in [('NRCS','wcc.sc.egov.usda.gov'), ('NOAA','ncei.noaa.gov'), ('USBR','usbr.gov')]}
    report['downloaded_bytes_this_run']=cache.bytes
    report['download_seconds_this_run']=cache.seconds
    (output_dir/'coverage.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    (output_dir/'coverage.md').write_text(markdown(report))
    render(report,output_dir/'coverage.png')
    return report
