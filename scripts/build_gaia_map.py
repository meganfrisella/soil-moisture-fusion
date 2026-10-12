#!/usr/bin/env python3
"""Build GAIA's domain, source-station and NISAR footprint map.

GAIA's network_map_pygmt figure draws the enclosing WGS84 rectangle; this map
draws the actual projected DOMAIN boundary, transformed with densified edges.

Run from the repository root after ``pixi install``:
    pixi run python scripts/build_gaia_map.py
    pixi run python scripts/build_gaia_map.py --refresh
    pixi run python scripts/build_gaia_map.py --refresh --gaia-root ../gwl-space-time-smooth
    pixi run python scripts/build_gaia_map.py \
        --sme2-map outputs/enumclaw/enumclaw.html

The default build reuses metadata embedded in outputs/station_map.html and writes
station_map.html plus station_map.png. PNG capture requires Chrome/Chromium
(auto-detected, or --browser PATH) and internet for Leaflet and Esri tiles.
Refresh needs curl and the
local GAIA checkout. No credentials are required. USGS field measurements are
queried to identify wells passing GAIA's QC; only station summaries are saved.
Catalog membership is not proof of assimilation or continuous observations.
--output-dir sets the HTML/PNG destination directory, keeping the --output filename
(default: station_map.html); --input-map selects the HTML containing saved metadata.

Credits: GAIA's Domain class supplies the snapped EPSG:5070 grid; pyproj/PROJ
transforms its densified edges and clips station coordinates. Leaflet provides
mapping, symbols, tooltips, bounds and navigation. Styling and metadata cards
follow the Odessa map. Coordinates in the snapshot are WGS84 degrees;
grid bounds are metres.
NISAR outlines are convex hulls in WGS84 longitude/latitude, one per track across
frames, enclosing the original CMR footprints that overlap GAIA's domain. Hulls
are not clipped to the domain and do not imply valid retrieval coverage. --sme2-map updates the
source frames from contributing granules embedded in generalized SME2 HTML.
Rebuilds and
station-catalog refreshes retain the footprint snapshot without reading SME2 files.
"""

import argparse
import ast
from collections import Counter
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from maps.common import read_manifest, render_template
from maps.nisar_footprints import track_hulls
from maps.preview import render_preview
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


def function_defaults(path, name):
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    result = {}
    for arg, value in zip(node.args.args[-len(node.args.defaults):], node.args.defaults):
        try:
            result[arg.arg] = ast.literal_eval(value)
        except (ValueError, TypeError):
            pass
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
    tf = Transformer.from_crs('EPSG:4326', domain['crs'], always_xy=True)
    x, y = tf.transform(station['longitude'], station['latitude'], errcheck=True)
    x0, y0, x1, y1 = domain['bounds_m']
    return x0 <= x <= x1 and y0 <= y <= y1


def point(identifier, name, kind, lat, lon, source, rows=()):
    return dict(id=identifier, name=name, kind=kind, latitude=float(lat), longitude=float(lon),
                rows=list(rows), links=[['Source metadata', source]])


class Catalog:
    def __init__(self):
        self.sources = []
        self.bytes = 0

    def get(self, address):
        result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                                 '--connect-timeout', '15', '--max-time', '120', '--retry', '3', address],
                                capture_output=True, check=True)
        self.bytes += len(result.stdout)
        return result.stdout

    def record(self, name, address):
        self.sources.append({'name': name, 'url': address})
        print(f'Fetching {name}…', file=sys.stderr, flush=True)
        return self.get(address)


def seismic_stations(xml, source, period):
    stations = {}
    for net in ET.fromstring(xml).findall('s:Network', NS):
        for st in net.findall('s:Station', NS):
            identifier = net.get('code') + '.' + st.get('code')
            stations[identifier] = point(identifier, st.findtext('s:Site/s:Name', identifier, NS),
                'seismic', st.findtext('s:Latitude', namespaces=NS), st.findtext('s:Longitude', namespaces=NS),
                source, [('Network', net.get('code')), ('Inventory overlap (UTC)', ' — '.join(period)),
                         ('Channels queried', 'BHZ, HHZ, EHZ, HNZ')])
    return list(stations.values())


def awdb_stations(items, networks, source):
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


def rdb_stations(raw, gages):
    lines = [line for line in raw.splitlines() if line and not line.startswith('#')]
    reader = csv.DictReader([lines[0]] + lines[2:], delimiter='\t')
    result = [point('USGS-' + r['site_no'], gages[r['site_no']], 'streamflow',
                    r['dec_lat_va'], r['dec_long_va'],
                    'https://waterdata.usgs.gov/monitoring-location/USGS-' + r['site_no'] + '/',
                    [('Network', 'USGS NWIS'), ('Measurement', 'Stream discharge (00060)')])
              for r in reader if r['site_no'] in gages]
    if {s['id'][5:] for s in result} != set(gages):
        raise ValueError('USGS catalog did not return every configured discharge gauge')
    return result


def refresh(gaia_root):
    started = time.monotonic()
    catalog = Catalog()
    config = gaia_root / 'src/data'
    domain = load_domain(gaia_root)
    d = dict(name=domain.name, crs=domain.crs, bounds_m=list(domain.bounds()),
             resolution_m=domain.res_m, geometry=domain_geometry(domain))
    w, s, e, n = domain.bbox_4326
    sc = literals(config / 'fetch_seismic.py')
    defaults = function_defaults(config / 'fetch_seismic.py', 'fetch_inventory')
    period = [defaults['start'], defaults['end']]
    address = url(FDSN, net=','.join(sc['NETWORKS']), cha=sc['CHANNELS'],
                  starttime=period[0], endtime=period[1], minlatitude=s, maxlatitude=n,
                  minlongitude=w, maxlongitude=e, level='station', format='xml', nodata=404)
    stations = seismic_stations(catalog.record('EarthScope UW/CC station inventory', address), address, period)
    networks = literals(config / 'fetch_insitu_sm.py')['SM_NETWORKS']
    all_awdb = json.loads(catalog.record('NRCS AWDB station catalog', AWDB))
    triplets = [st['stationTriplet'] for st in all_awdb if st.get('networkCode') in networks
                and st.get('latitude') and w <= st['longitude'] <= e and s <= st['latitude'] <= n]
    if triplets:
        address = url(AWDB, stationTriplets=','.join(triplets), returnStationElements='true')
        stations += awdb_stations(json.loads(catalog.record('NRCS AWDB sensor inventory', address)), networks, address)
    crn_count = 0
    for row in csv.DictReader(io.StringIO(catalog.record('NOAA USCRN station catalog', USCRN).decode()), delimiter='\t'):
        if row.get('STATUS', 'OPERATIONAL').upper() not in ('OPERATIONAL', 'COMMISSIONED'):
            continue
        lat, lon = float(row['LATITUDE']), float(row['LONGITUDE'])
        if w <= lon <= e and s <= lat <= n:
            stations.append(point('USCRN.' + row['WBAN'], row['LOCATION'] + ' ' + row['VECTOR'], 'soil', lat, lon,
                                  USCRN, [('Network', 'USCRN'), ('Measurement', 'Soil moisture')]))
            crn_count += 1
    gages = literals(config / 'fetch_usgs_discharge.py')['PUGET_GAGES']
    address = url(SITE, format='rdb', sites=','.join(gages), siteOutput='expanded')
    stations += rdb_stations(catalog.record('USGS configured discharge gauges', address).decode(), gages)
    gw = literals(config / 'download_nwis_domain.py')
    start = '2000-01-01'  # GAIA download_nwis_domain CLI default; full historical baseline.
    address = url(gw['_FM_ITEMS'], f='json', bbox=f'{w},{s},{e},{n}', parameter_code=gw['_GW_PARAM'],
                  datetime=f'{start}/..', limit=1000)
    catalog.sources.append({'name': 'USGS NWIS groundwater field measurements', 'url': address})
    stations += groundwater_stations(paged_features(catalog, address), gw['_DROP_QUALIFIERS'])
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
                selection=dict(seismic_period_utc=period, groundwater_start_utc=start,
                               awdb_networks=list(networks), outside_grid_removed=before_clip-len(stations),
                               uscrn_bbox_count=crn_count),
                download=dict(bytes=catalog.bytes, seconds=round(time.monotonic()-started, 3)),
                notes=['Source-network inventory, not a confirmed assimilation roster. Metadata does not guarantee observation availability.',
                       'Groundwater wells have at least one accepted field measurement since 2000; many are historical.',
                       'AWDB soil moisture and SWE roles share one marker where colocated. Element presence follows GAIA; sensor epochs are not filtered.',
                       'The canonical 90 m grid boundary is shown; it does not imply every model output runs at 90 m.',
                       'Coordinates use installed PROJ datum operations without remote grid downloads.'])


def validate(data):
    if data.get('schema_version') != 2:
        raise ValueError('Expected GAIA map snapshot schema 2')
    identifiers = set()
    for st in data['stations']:
        if st['id'] in identifiers:
            raise ValueError('Duplicate station ID: ' + st['id'])
        identifiers.add(st['id'])
        if not (-90 <= st['latitude'] <= 90 and -180 <= st['longitude'] <= 180) or not inside(st, data['domain']):
            raise ValueError('Invalid or out-of-domain station: ' + st['id'])
    ring = data['domain']['geometry']['coordinates'][0]
    if len(ring) < 4 or ring[0] != ring[-1] or not all(math.isfinite(v) for xy in ring for v in xy):
        raise ValueError('Invalid boundary ring')
    for track in data.get('tracks', []):
        if track['geometry']['type'] != 'MultiPolygon' or not track['granules']:
            raise ValueError('Invalid NISAR track footprint')
        for polygon in track['geometry']['coordinates']:
            for ring in polygon:
                if (len(ring) < 4 or ring[0] != ring[-1]
                        or not all(math.isfinite(lon) and math.isfinite(lat)
                                   and -180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in ring)):
                    raise ValueError('Invalid NISAR footprint ring')


def render(data):
    validate(data)
    manifest = dict(data)
    manifest.pop('track_boxes', None)
    manifest.pop('pilots', None)
    manifest['notes'] = [note for note in manifest.get('notes', [])
                         if not note.startswith('Pilot outlines are')]
    manifest['track_hulls'] = track_hulls(data.get('tracks', []), data['domain']['geometry'])
    return render_template(MAP_HTML, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--gaia-root', type=Path, default=ROOT.parent / 'gwl-space-time-smooth')
    parser.add_argument('--input-map', type=Path, default=ROOT / 'outputs/station_map.html')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/station_map.html')
    parser.add_argument('--output-dir', type=Path,
                        help='HTML/PNG destination directory; overrides the directory in --output')
    parser.add_argument('--browser', type=Path, help='Chrome/Chromium executable for the PNG preview')
    parser.add_argument('--sme2-map', type=Path, help='Sync contributing NISAR footprints from this SME2 HTML')
    args = parser.parse_args()
    if args.output_dir is not None:
        args.output = args.output_dir / args.output.name
    data = refresh(args.gaia_root) if args.refresh else read_manifest(args.input_map)
    if args.refresh and args.input_map.exists():
        previous = read_manifest(args.input_map)
        for key in ('tracks', 'nisar_source'):
            if key in previous:
                data[key] = previous[key]
    if args.sme2_map:
        from maps.nisar_footprints import contributing_tracks
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

MAP_HTML = (ROOT / 'scripts/maps/gaia_map.html').read_text()

if __name__ == '__main__':
    main()
