"""Shared sensor-catalog discovery and caching for geographic and GAIA station maps.

Run through scripts/build_station_map.py or scripts/build_gaia_map.py.
Station coordinates are WGS84 degrees; GAIA clipping uses projected grid metres.
Inventory membership and catalog activity do not establish live observations.
Groundwater QC follows the configured GAIA checkout; raw depth-to-water values
are in feet and only station summaries, not resampled measurements, enter maps.
"""

import ast
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode, urljoin
import xml.etree.ElementTree as ET

import numpy as np
from pyproj import Transformer, network

from ingest.downloads import publish_file

FDSN = 'https://service.earthscope.org/fdsnws/station/1/query'
AWDB = 'https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations'
USCRN = 'https://www.ncei.noaa.gov/pub/data/uscrn/products/stations.tsv'
SITE = 'https://waterservices.usgs.gov/nwis/site/'
NS = {'s': 'http://www.fdsn.org/xml/station/1'}
# Reproducible offline transforms: installed PROJ data, no on-demand datum grids.
network.set_network_enabled(False)


def url(base, **params):
    return base + '?' + urlencode(params)


def literals(path):
    """Read simple GAIA constants without importing fetchers or their dependencies."""
    result = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = value
    return result


def load_domain(gaia_root):
    spec = importlib.util.spec_from_file_location('gaia_map_domain', gaia_root / 'src/config/domain.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.DOMAIN


def domain_geometry(domain):
    x0, y0, x1, y1 = domain.bounds()
    edge = ([(x, y0) for x in np.linspace(x0, x1, 50)]
            + [(x1, y) for y in np.linspace(y0, y1, 50)]
            + [(x, y1) for x in np.linspace(x1, x0, 50)]
            + [(x0, y) for y in np.linspace(y1, y0, 50)])
    tf = Transformer.from_crs(domain.crs, 'EPSG:4326', always_xy=True)
    ring = [list(tf.transform(x, y, errcheck=True)) for x, y in edge]
    return {'type': 'Polygon', 'coordinates': [ring]}


def inside(station, domain):
    if 'bounds_wgs84' in domain:
        w, s, e, n = domain['bounds_wgs84']
        return w <= station['longitude'] <= e and s <= station['latitude'] <= n
    tf = Transformer.from_crs('EPSG:4326', domain['crs'], always_xy=True)
    x, y = tf.transform(station['longitude'], station['latitude'], errcheck=True)
    x0, y0, x1, y1 = domain['bounds_m']
    return x0 <= x <= x1 and y0 <= y <= y1


def point(identifier, name, kind, lat, lon, source, rows=()):
    return dict(id=identifier, name=name, kind=kind, latitude=float(lat), longitude=float(lon),
                rows=list(rows), links=[['Source metadata', source]])


class Catalog:
    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.sources = []
        self.bytes = 0
        self.seconds = 0
        self.responses = {}

    def fetch(self, address):
        """Fetch public bytes without storing temporary request tokens in provenance."""
        started = time.monotonic()
        result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                                 '--connect-timeout', '15', '--max-time', '120', '--retry', '3', address],
                                capture_output=True, check=False)
        self.seconds += time.monotonic() - started
        if result.returncode:
            raise RuntimeError(f'Public catalog request failed (curl status {result.returncode})')
        self.bytes += len(result.stdout)
        return result.stdout

    def get(self, address, loader=None):
        target = self.cache_dir / (hashlib.sha256(address.encode()).hexdigest() + '.json')

        def validate(path):
            doc = json.loads(path.read_text())
            if (doc['url'] != address or not doc['body'] or
                    hashlib.sha256(doc['body'].encode()).hexdigest() != doc['sha256']):
                raise ValueError(f'Invalid cached catalog response: {path}')
            datetime.fromisoformat(doc['retrieved_at_utc'])

        def write(destination):
            body = (loader() if loader else self.fetch(address)).decode('utf-8')
            doc = dict(url=address, retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                       sha256=hashlib.sha256(body.encode()).hexdigest(), body=body)
            destination.write(json.dumps(doc, ensure_ascii=False).encode())

        publish_file(target, write, validate)
        doc = json.loads(target.read_text())
        self.responses[address] = {k: v for k, v in doc.items() if k != 'body'}
        return doc['body'].encode()

    def archive(self, metadata):
        """Reuse a Figshare file only after checking its published size and MD5."""
        address = metadata['download_url']
        target = self.cache_dir / Path(metadata['name']).name

        def validate(path):
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'md5').hexdigest()
            if path.stat().st_size != metadata['size'] or digest != metadata['computed_md5']:
                raise ValueError(f'Invalid cached archive: {path}')

        publish_file(target, lambda destination: destination.write(self.fetch(address)), validate)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.responses[address] = dict(url=address, sha256=digest,
            retrieved_at_utc=datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat())
        self.sources.append({'name': metadata['name'], 'url': address})
        return target

    def record(self, name, address):
        self.sources.append({'name': name, 'url': address})
        print(f'Reading {name}…', file=sys.stderr, flush=True)
        return self.get(address)


def epoch_activity(end, as_of):
    """Use a catalog end date, not the age of the latest observation, to flag closure."""
    if not end:
        return dict(status='unknown', basis='Catalog does not supply a closure date', as_of_utc=as_of)
    status = 'inactive' if end[:10] < as_of[:10] else 'active'
    basis = 'Open-ended catalog epoch' if end[:4] >= '2100' else f'Catalog station end date: {end[:10]}'
    return dict(status=status, basis=basis, as_of_utc=as_of)


def seismic_stations(xml, source, period, as_of):
    stations = {}
    for net in ET.fromstring(xml).findall('s:Network', NS):
        for st in net.findall('s:Station', NS):
            identifier = net.get('code') + '.' + st.get('code')
            station = point(identifier, st.findtext('s:Site/s:Name', identifier, NS),
                'seismic', st.findtext('s:Latitude', namespaces=NS), st.findtext('s:Longitude', namespaces=NS),
                source, [('Network', net.get('code')), ('Inventory overlap (UTC)', ' — '.join(period) if period else 'All catalog epochs'),
                         ('Channels queried', 'BHZ, HHZ, EHZ, HNZ')])
            end = st.get('endDate') or net.get('endDate')
            station['activity'] = epoch_activity(end, as_of)
            if not end:
                station['activity'].update(status='active', basis='Open station/network inventory epoch; not a live-data check')
            previous = stations.get(identifier)
            if previous is None or previous['activity']['status'] != 'active':
                stations[identifier] = station
    return list(stations.values())


def awdb_stations(items, networks, source, as_of=None):
    result = []
    for st in items:
        elements = {el.get('elementCode') for el in st.get('stationElements', [])}
        moisture = st['networkCode'] in networks and 'SMS' in elements
        snow = st['networkCode'] == 'SNTL' and 'WTEQ' in elements
        if not (moisture or snow):
            continue
        roles = (['Soil moisture (SMS)'] if moisture else []) + (['Snow water equivalent (WTEQ)'] if snow else [])
        result.append(point(st['stationTriplet'], st['name'], 'soil' if moisture else 'snow',
                            st['latitude'], st['longitude'], source,
                            [('Network', st['networkCode']), ('Measurements', '; '.join(roles))]))
        result[-1]['activity'] = epoch_activity(st.get('endDate'), as_of or datetime.now(timezone.utc).isoformat())
        inactive = []
        for code, label in (('SMS', 'Soil moisture'), ('WTEQ', 'Snow water equivalent')):
            sensors = [el for el in st.get('stationElements', []) if el.get('elementCode') == code]
            if sensors and all(epoch_activity(el.get('endDate'), result[-1]['activity']['as_of_utc'])['status'] == 'inactive'
                               for el in sensors):
                inactive.append(label)
        result[-1]['inactive_sensors'] = inactive
    return result


def groundwater_stations(features, drop_qualifiers):
    """Match GAIA's undisturbed, nonnegative depth-to-water QC and deduplicate wells."""
    sites = {}
    for f in features:
        c = (f.get('geometry') or {}).get('coordinates')
        p = f['properties']
        if not c or any(q.lower() in drop_qualifiers for q in (p.get('qualifier') or [])):
            continue
        try:
            v = float(p.get('value'))
        except (ValueError, TypeError):
            continue
        date = p.get('time', '')[:10]
        if not math.isfinite(v) or v < 0 or not date:
            continue
        identifier = p['monitoring_location_id']
        if identifier not in sites:
            sites[identifier] = point(identifier, p.get('monitoring_location_name') or identifier,
                                     'groundwater', c[1], c[0],
                                     'https://waterdata.usgs.gov/monitoring-location/' + identifier + '/',
                                     [('Network', 'USGS NWIS'), ('Measurement', 'Depth to water below land surface')])
            sites[identifier]['first'] = date
            sites[identifier]['last'] = date
        st = sites[identifier]
        st['first'] = min(st['first'], date)
        st['last'] = max(st['last'], date)
    for st in sites.values():
        st['rows'].append(('Accepted records (UTC)', st.pop('first') + ' — ' + st.pop('last')))
    return list(sites.values())


def paged_features(catalog, address):
    features, seen = [], set()
    while address:
        if address in seen:
            raise ValueError('Repeated USGS pagination link')
        seen.add(address)
        doc = json.loads(catalog.get(address))
        features.extend(doc['features'])
        address = next((urljoin(address, link['href']) for link in doc.get('links', [])
                        if link.get('rel') == 'next'), None)
        print(f'  USGS: {len(features)} field records', file=sys.stderr, flush=True)
    return features


def rdb_stations(raw, gages=None):
    lines = [line for line in raw.splitlines() if line and not line.startswith('#')]
    if not lines:
        return []
    if len(lines) < 2 or not {'site_no', 'station_nm', 'dec_lat_va', 'dec_long_va'} <= set(lines[0].split('\t')):
        raise ValueError('Invalid USGS site catalog header')
    reader = csv.DictReader([lines[0]] + lines[2:], delimiter='\t')
    result = [point('USGS-' + r['site_no'], gages[r['site_no']] if gages else r['station_nm'], 'streamflow',
                    r['dec_lat_va'], r['dec_long_va'],
                    'https://waterdata.usgs.gov/monitoring-location/USGS-' + r['site_no'] + '/',
                    [('Network', 'USGS NWIS'), ('Measurement', 'Stream discharge (00060)')])
              for r in reader if gages is None or r['site_no'] in gages]
    if gages is not None and {s['id'][5:] for s in result} != set(gages):
        raise ValueError('USGS catalog did not return every configured discharge gauge')
    return result


def refresh(gaia_root, cache_dir, region=None):
    """Discover all sensor epochs, retaining inactive sites and recording catalog status."""
    started = time.monotonic()
    catalog = Catalog(cache_dir)
    config = gaia_root / 'src/data'
    if region is None:
        domain = load_domain(gaia_root)
        d = dict(name=domain.name, label='GAIA domain', crs=domain.crs, bounds_m=list(domain.bounds()),
                 resolution_m=domain.res_m, geometry=domain_geometry(domain))
        w, s, e, n = domain.bbox_4326
    else:
        d = region
        w, s, e, n = d['bounds_wgs84']
    sc = literals(config / 'fetch_seismic.py')
    address = url(FDSN, net=','.join(sc['NETWORKS']), cha=sc['CHANNELS'],
                  minlatitude=s, maxlatitude=n, minlongitude=w, maxlongitude=e,
                  level='station', format='xml', nodata=404)
    raw = catalog.record('EarthScope UW/CC station inventory', address)
    stations = seismic_stations(raw, address, None, catalog.responses[address]['retrieved_at_utc'])
    networks = literals(config / 'fetch_insitu_sm.py')['SM_NETWORKS']
    all_awdb = json.loads(catalog.record('NRCS AWDB station catalog', AWDB))
    triplets = [st['stationTriplet'] for st in all_awdb if st.get('networkCode') in networks
                and st.get('latitude') and w <= st['longitude'] <= e and s <= st['latitude'] <= n]
    if triplets:
        address = url(AWDB, stationTriplets=','.join(triplets), returnStationElements='true')
        raw = catalog.record('NRCS AWDB sensor inventory', address)
        stations += awdb_stations(json.loads(raw), networks, address, catalog.responses[address]['retrieved_at_utc'])
    crn_count = 0
    for row in csv.DictReader(io.StringIO(catalog.record('NOAA USCRN station catalog', USCRN).decode()), delimiter='\t'):
        lat, lon = float(row['LATITUDE']), float(row['LONGITUDE'])
        if w <= lon <= e and s <= lat <= n:
            stations.append(point('USCRN.' + row['WBAN'], row['LOCATION'] + ' ' + row['VECTOR'], 'soil', lat, lon,
                                  USCRN, [('Network', 'USCRN'), ('Measurement', 'Soil moisture')]))
            status = row.get('STATUS', '').upper()
            active = status in ('OPERATIONAL', 'COMMISSIONED')
            inactive = status in ('CLOSED', 'DECOMMISSIONED', 'INACTIVE')
            stations[-1]['activity'] = dict(status='active' if active else 'inactive' if inactive else 'unknown',
                basis=f'NOAA station status: {status or "not supplied"}', as_of_utc=catalog.responses[USCRN]['retrieved_at_utc'])
            closed = epoch_activity(row.get('CLOSING'), catalog.responses[USCRN]['retrieved_at_utc'])
            if closed['status'] == 'inactive':
                stations[-1]['activity'] = closed
            crn_count += 1
    if region is None:
        gages = literals(config / 'fetch_usgs_discharge.py')['PUGET_GAGES']
        address = url(SITE, format='rdb', sites=','.join(gages), siteOutput='expanded', siteStatus='all')
        stations += rdb_stations(catalog.record('USGS configured discharge gauges', address).decode(), gages)
    else:
        address = url(SITE, format='rdb', bBox=f'{w},{s},{e},{n}', siteType='ST',
                      parameterCd='00060', hasDataTypeCd='dv', siteStatus='all', siteOutput='expanded')
        stations += rdb_stations(catalog.record('USGS daily-discharge gauge inventory', address).decode())
    gw = literals(config / 'download_nwis_domain.py')
    start = '2000-01-01'  # GAIA download_nwis_domain CLI default; full historical baseline.
    address = url(gw['_FM_ITEMS'], f='json', bbox=f'{w},{s},{e},{n}', parameter_code=gw['_GW_PARAM'],
                  datetime=f'{start}/..', limit=1000)
    catalog.sources.append({'name': 'USGS NWIS groundwater field measurements', 'url': address})
    stations += groundwater_stations(paged_features(catalog, address), gw['_DROP_QUALIFIERS'])
    status_sets, status_dates = {}, []
    for status in ('all', 'active'):
        address = url(SITE, format='rdb', bBox=f'{w},{s},{e},{n}', siteStatus=status, siteOutput='expanded')
        status_sets[status] = {st['id'] for st in rdb_stations(
            catalog.record(f'USGS {status} monitoring locations', address).decode())}
        status_dates.append(catalog.responses[address]['retrieved_at_utc'])
    for station in stations:
        if station['kind'] in ('streamflow', 'groundwater'):
            identifier = station['id']
            status = 'active' if identifier in status_sets['active'] else 'inactive' if identifier in status_sets['all'] else 'unknown'
            station['activity'] = dict(status=status, as_of_utc=min(status_dates),
                basis='USGS station activity classification; individual sensor telemetry not checked')
    from maps.soil_stations import additional_soil_stations
    stations += additional_soil_stations(catalog, d)
    before_clip = len(stations)
    stations = sorted((st for st in stations if inside(st, d)), key=lambda st: (st['kind'], st['id']))
    files = ['src/config/domain.py'] + ['src/data/' + f for f in (
        'fetch_seismic.py', 'fetch_insitu_sm.py', 'fetch_snotel_swe.py', 'fetch_uscrn.py',
        'fetch_usgs_discharge.py', 'download_nwis_domain.py')]
    revision = subprocess.run(['git', '-C', str(gaia_root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    return dict(schema_version=2, retrieved_at=datetime.now(timezone.utc).isoformat(),
                domain=d,
                stations=stations, sources=catalog.sources,
                gaia=dict(checkout=str(gaia_root.resolve()), revision=revision,
                          file_sha256={f: hashlib.sha256((gaia_root / f).read_bytes()).hexdigest() for f in files}),
                selection=dict(seismic_period_utc=None, seismic_networks=list(sc['NETWORKS']),
                               groundwater_start_utc=start,
                               streamflow_selection='GAIA configured gauges' if region is None else 'All USGS stream sites with daily discharge (00060), including historical sites',
                               activity_filter='None; retain inactive sites',
                               awdb_networks=list(networks), outside_grid_removed=before_clip-len(stations),
                               uscrn_bbox_count=crn_count),
                catalog_responses=list(catalog.responses.values()),
                download=dict(bytes=catalog.bytes, seconds=round(catalog.seconds, 3),
                              run_seconds=round(time.monotonic()-started, 3)),
                notes=['Source-network inventory, not a confirmed assimilation roster. Metadata does not guarantee observation availability.',
                       'Groundwater wells have at least one accepted field measurement since 2000; many are historical.',
                       'AWDB soil moisture and SWE roles share one marker where colocated. Element presence follows GAIA; sensor epochs are not filtered.',
                       'Activity comes from source catalogs, not a live telemetry check; missing status is unknown.',
                       'Additional soil sites require explicit moisture channels, public sensor inventories, archived measurements or published instrumentation; soil temperature alone is insufficient.',
                       'Cook Farm archive markers represent individual probe profiles; AmeriFlux markers represent tower-site soil instrumentation, not eddy-covariance footprint averages.',
                       'Private WSU Smart Farms locations and sites without confirmed soil-moisture instrumentation are not plotted.',
                       ('The canonical 90 m grid boundary is shown; it does not imply every model output runs at 90 m.'
                        if region is None else 'The boundary is a WGS84 viewing box, not a model grid or drainage-basin boundary.'),
                       'Coordinates use installed PROJ datum operations without remote grid downloads.'])


