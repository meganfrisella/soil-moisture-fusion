"""Compare >=0.05 m3/m3 hourly validation-sensor jumps with station rain gauges.

Run through ``scripts/validate_eastern_washington.py --with-precipitation``
after ``pixi install`` and completed regridded fields.

The frozen prepared moisture table supplies consecutive, finite, exactly hourly
pairs. NRCS hourly PRCP increments are preferred where available; otherwise
hourly PREC accumulation is differenced. NOAA P_CALC gives preceding-hour mm.
Positive rainfall within the local civil day is evidence of a recorded event,
not proof that rainfall caused a sensor jump. All outputs preserve source hashes.
"""

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
from maps.station_catalogs import Catalog

AWDB = 'https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data'
NOAA = ('https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/2026/'
        'CRNH0203-2026-WA_Spokane_17_SSW.txt')
UTC = timezone.utc
PACIFIC = ZoneInfo('America/Los_Angeles')
THRESHOLD = 0.05


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observation_time(station, label):
    if station['network'] in ('SCAN', 'SNTL'):
        fixed = timezone(timedelta(hours=station['catalog_time_zone_utc_offset_hours']))
        return datetime.fromisoformat(label).replace(tzinfo=fixed).astimezone(UTC)
    return datetime.fromisoformat(label).replace(tzinfo=UTC)


def nrcs_precip(station, catalog, begin, end):
    metadata = json.loads(Path(station['cached_catalog']).read_text())
    if hashlib.sha256(metadata['body'].encode()).hexdigest() != metadata['sha256']:
        raise ValueError('NRCS station catalog cache checksum mismatch')
    entries = json.loads(metadata['body'])
    entry = next(s for s in entries if s['stationTriplet'] == station['id'])
    channels = [e for e in entry['stationElements'] if e['durationName'] == 'HOURLY'
                and e['elementCode'] in ('PRCP', 'PREC') and e['ordinal'] == 1]
    selected = next((e for e in channels if e['elementCode'] == 'PRCP'), None)
    if selected is None:
        selected = next((e for e in channels if e['elementCode'] == 'PREC'), None)
    if selected is None:
        return {}, {'status': 'no_hourly_station_gauge', 'catalog_sha256': metadata['sha256']}
    if selected['storedUnitCode'] != 'in':
        raise ValueError('Unexpected NRCS precipitation units')
    address = AWDB + '?' + urlencode(dict(stationTriplets=station['id'], elements=selected['elementCode'],
        duration='HOURLY', beginDate=begin, endDate=end, returnFlags='true',
        returnSuspectData='true', periodRef='END'))
    body = catalog.get(address)
    data = json.loads(body)
    if len(data) != 1 or data[0]['stationTriplet'] != station['id'] or len(data[0]['data']) != 1:
        raise ValueError('Unexpected NRCS precipitation response')
    response = data[0]['data'][0]
    if response.get('error') or response['stationElement']['elementCode'] != selected['elementCode']:
        raise ValueError('NRCS precipitation channel mismatch or error')
    raw = {}
    for item in response.get('values', []):
        time = observation_time(station, item['date'])
        if time in raw:
            raise ValueError('Duplicate precipitation timestamp')
        value = item.get('value')
        raw[time] = float(value) if value is not None and math.isfinite(float(value)) else None
    amount = {}
    if selected['elementCode'] == 'PRCP':
        amount = {t: (v * 25.4 if v is not None and v >= 0 else None) for t, v in raw.items()}
    else:
        previous = None
        for time, value in sorted(raw.items()):
            # PREC is water-year cumulative; only consecutive valid labels are differenced.
            increment = None
            if (previous is not None and value is not None and previous[1] is not None
                    and time - previous[0] == timedelta(hours=1)):
                difference = value - previous[1]
                if difference >= 0:
                    increment = difference * 25.4
            amount[time] = increment
            previous = time, value
    return amount, {'status': 'hourly', 'channel': selected['elementCode'],
        'meaning': 'hourly increment' if selected['elementCode'] == 'PRCP' else 'hourly cumulative difference',
        'catalog_sha256': metadata['sha256'], 'response': catalog.responses[address]}


def noaa_precip(catalog, prior_cache):
    files = [json.loads(p.read_text()) for p in Path(prior_cache).glob('*.json')]
    matches = [f for f in files if f.get('url') == NOAA]
    if len(matches) > 1:
        raise ValueError('Ambiguous NOAA Spokane cache')
    if matches and hashlib.sha256(matches[0]['body'].encode()).hexdigest() != matches[0]['sha256']:
        raise ValueError('NOAA cached source checksum mismatch')
    # Copy a verified existing response into the task cache; fetch only if absent.
    body = catalog.get(NOAA, loader=(lambda: matches[0]['body'].encode()) if matches else None)
    precip = {}
    for line in body.decode().splitlines():
        row = line.split()
        if len(row) != 38 or row[0] != '04136':
            raise ValueError('Unexpected NOAA hourly row')
        time = datetime.strptime(row[1] + row[2], '%Y%m%d%H%M').replace(tzinfo=UTC)
        if time in precip:
            raise ValueError('Duplicate NOAA precipitation timestamp')
        value = float(row[12])  # documented column 13, P_CALC, hourly mm
        precip[time] = value if value >= 0 else None
    return precip, {'status': 'hourly', 'channel': 'P_CALC', 'meaning': 'preceding-hour total',
                    'response': catalog.responses[NOAA]}


def run(args):
    output = args.output_dir
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Use a new output directory')
    inventory = json.loads(args.inventory.read_text())
    metrics = json.loads(args.validation_metrics.read_text())
    ids = set(metrics['stations'])
    ids.discard('ODSW')
    stations = {s['id']: s for s in inventory['stations'] if s['id'] in ids}
    if stations.keys() != ids:
        raise ValueError('Validation stations disagree with inventory')
    window = inventory['study_window_utc']
    start = datetime.fromisoformat(window['start']) - timedelta(days=2)
    stop = datetime.fromisoformat(window['end']) + timedelta(days=2)
    observations = {sid: [] for sid in sorted(ids)}
    with (args.preparation_dir / 'prepared.csv').open() as stream:
        for row in csv.DictReader(stream):
            if row['station_id'] in ids:
                observations[row['station_id']].append(row)
    jumps = []
    for sid, rows in observations.items():
        previous = None
        for row in rows:
            value = float(row['soil_moisture_m3_m3'])
            time = datetime.fromisoformat(row['time_utc'])
            if (previous is not None and math.isfinite(value) and math.isfinite(previous[1])
                    and time - previous[0] == timedelta(hours=1)
                    and abs(value - previous[1]) >= THRESHOLD - 1e-12):
                jumps.append(dict(station_id=sid, start_utc=previous[0].isoformat(),
                                  end_utc=time.isoformat(), delta_m3_m3=round(value - previous[1], 6),
                                  local_date=time.astimezone(PACIFIC).date().isoformat()))
            previous = time, value
    catalog = Catalog(args.cache_dir)
    gauges = {}
    source = {}
    for sid, station in stations.items():
        if station['network'] == 'USCRN':
            gauges[sid], source[sid] = noaa_precip(catalog, args.moisture_cache_dir)
        else:
            gauges[sid], source[sid] = nrcs_precip(station, catalog,
                start.date().isoformat(), stop.date().isoformat())
    study_start = datetime.fromisoformat(window['start'])
    study_end = datetime.fromisoformat(window['end'])
    coverage = {}
    for sid, readings in gauges.items():
        in_window = [v for t, v in readings.items() if study_start <= t <= study_end]
        coverage[sid] = {'hourly_labels': len(in_window),
                         'finite_hourly_amounts': sum(v is not None for v in in_window)}
    for jump in jumps:
        sid = jump['station_id']
        station = stations[sid]
        end = datetime.fromisoformat(jump['end_utc'])
        day = jump['local_date']
        samples = [(t, value) for t, value in gauges[sid].items()
                   if t.astimezone(PACIFIC).date().isoformat() == day]
        positive = [(t, value) for t, value in samples if value is not None and value > 0]
        finite = [value for _, value in samples if value is not None]
        if positive:
            status = ('recorded_by_jump' if any(t <= end for t, _ in positive)
                      else 'recorded_later_same_day')
        elif len(finite) == 24:
            status = 'no_precipitation_recorded'
        else:
            status = 'cannot_corroborate_incomplete_gauge'  # provenance retains gaps
        jump.update(station=station['name'], latitude=metrics['stations'][sid]['latitude_wgs84'],
            longitude=metrics['stations'][sid]['longitude_wgs84'], precip_status=status,
            precip_mm=round(sum(value for _, value in positive), 3) if positive else None,
            precip_hours=len(positive), gauge_hours=len(samples), finite_gauge_hours=len(finite),
            rain_before_or_at_jump_mm=round(sum(v for t, v in positive if t <= end), 3),
            first_precip_utc=min((t for t, _ in positive), default=None).isoformat() if positive else None)
    jumps.sort(key=lambda r: (r['station'], r['end_utc']))
    output.mkdir(parents=True, exist_ok=True)
    fieldnames = list(jumps[0]) if jumps else ['station_id', 'start_utc', 'end_utc', 'delta_m3_m3']
    with (output / 'jumps.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(jumps)
    report = dict(threshold_m3_m3=THRESHOLD, comparison='consecutive valid prepared one-hour observations',
        day='America/Los_Angeles civil date of jump endpoint', coordinates='EPSG:4326 degrees',
        stations=source, gauge_coverage=coverage, station_count=len(stations), jump_count=len(jumps),
        input_sha256={'inventory': digest(args.inventory), 'prepared': digest(args.preparation_dir/'prepared.csv'),
                      'validation_metrics': digest(args.validation_metrics)},
        counts={sid: sum(j['station_id'] == sid for j in jumps) for sid in sorted(ids)})
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = [
        '# Shallow soil-moisture jumps and station precipitation', '',
        'Study window: ' + window['start'] + ' to ' + window['end'] + '.',
        'Jump: absolute change ≥0.05 m³/m³ across two consecutive valid prepared hourly observations.',
        'Precipitation: own-station hourly gauge, accumulated over the America/Los_Angeles date of the jump endpoint.',
        'Coordinates: EPSG:4326 degrees; amounts: mm. Same-day rain is supporting context, not causal proof.', '',
        '| Station (lat, lon) | Jump ending UTC | Change (m³/m³) | Local day rain (mm) | By jump (mm) | Finding |',
        '|---|---|---:|---:|---:|---|',
    ]
    for jump in jumps:
        rain = '—' if jump['precip_mm'] is None else str(jump['precip_mm'])
        lines.append(f"| {jump['station']} ({jump['latitude']}, {jump['longitude']}) | "
            f"{jump['end_utc']} | {jump['delta_m3_m3']:+.3f} | {rain} | "
            f"{jump['rain_before_or_at_jump_mm']:.3f} | {jump['precip_status']} |")
    lines += ['', 'Stations with no qualifying jump: ' + ', '.join(sorted(
        stations[sid]['name'] for sid, count in report['counts'].items() if count == 0)) + '.',
        'NRCS SCAN PRCP is hourly increment; SNOTEL PREC is a cumulative hourly reading differenced across complete hours; NOAA P_CALC is preceding-hour total.',
        'Gauge availability in the study window (finite hourly amounts / returned hourly labels): ' +
            '; '.join(f"{stations[sid]['name']} {coverage[sid]['finite_hourly_amounts']}/{coverage[sid]['hourly_labels']}"
                      for sid in sorted(ids)) + '.',
        'Positive totals on days with incomplete hourly gauge coverage are lower bounds. Rain that begins after a jump cannot corroborate rain at the jump.',
        'NRCS precipitation values used here carry provider flags in the cached source; raw values have not received human review.',
        'Detailed per-event gauge coverage, first rain hour, source identities, and hashes are in jumps.csv and provenance.json.', '']
    (output / 'report.md').write_text('\n'.join(lines))
    return report
