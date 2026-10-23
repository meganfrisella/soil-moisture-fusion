"""Discover documented soil-moisture sites for the shared station-map workflow.

Run through scripts/build_station_map.py --refresh --output-dir <directory>.
Public catalogs and the Cook Farm archive use that workflow's verified cache.
Coordinates are WGS84 degrees; Cook probe coordinates are transformed from the
archive's NAD83 / UTM zone 11N CRS. Inventory presence is not a live-data check.
"""

import ast
import io
import json
import re
from urllib.parse import urljoin
import zipfile

from pyproj import CRS, Transformer
import shapefile

from maps.station_catalogs import epoch_activity, inside, point, url

AGRIMET = 'https://www.usbr.gov/pn/agrimet/agrimetmap/usbr_map.json'
AGRIMET_PARAMS = 'https://www.usbr.gov/pn/agrimet/aginfo/station_params.html'
AWN = 'https://weather.wsu.edu/api/v1/network/stations'
AWN_SENSORS = 'https://weather.wsu.edu/api/v1/network/details'
AMERIFLUX = 'https://ameriflux.lbl.gov/sites/site-search/'
COOK = 'https://api.figshare.com/v2/articles/24852273'
COOK_DOI = 'https://doi.org/10.15482/USDA.ADC/1349683'

# Published sensor evidence supplements missing SWC variable lists in AmeriFlux.
# Coordinates and closure years always come from the public AmeriFlux catalog.
RESEARCH_SENSORS = [
    dict(sites=('US-Hn1',), reference='https://doi.org/10.1029/2020JD032889',
         description='CS616 volumetric water-content probes at 2.5, 5, 10, 15, 20 cm; 30-minute averages',
         availability='Published instrumentation; SWC is not listed in the public BASE variable catalog'),
    dict(sites=('US-RC1', 'US-RC2', 'US-RC3', 'US-RC5'),
         reference='https://doi.org/10.1002/2017JG004148',
         description='Decagon 5TM soil water-content sensors; observations were sporadic because of maintenance',
         availability='Historical research measurements; SWC is not listed in the public BASE variable catalog'),
]


def unknown_activity(station, as_of, basis):
    station['activity'] = dict(status='unknown', as_of_utc=as_of, basis=basis)
    return station


def agrimet_stations(geojson, parameters, as_of):
    """Match explicit soil-moisture channels to station coordinates, excluding soil temperature."""
    sensors = {}
    for match in re.finditer(r'<a\s+name=[\"\']?(\w+)[\"\']?\s*>(.*?)(?=<a\s+name=|</pre>)',
                             parameters, re.IGNORECASE | re.DOTALL):
        channels = re.findall(r'(XSM\d+)\s+SOIL MOISTURE\s*-\s*(\d+)\s+INCH DEPTH\s*\(PERCENT\)',
                              match[2], re.IGNORECASE)
        if channels:
            sensors[match[1].upper()] = channels
    if not sensors:
        raise ValueError('AgriMet inventory has no recognizable soil-moisture channels')
    stations = []
    for feature in geojson['features']:
        p = feature['properties']
        identifier = p['siteid'].upper()
        if identifier not in sensors:
            continue
        lon, lat = feature['geometry']['coordinates'][:2]
        station = point('AgriMet.' + identifier, p['description'], 'soil', lat, lon,
                        urljoin(AGRIMET, p['url']),
                        [('Network', 'USBR AgriMet'), ('Measurement', 'Volumetric soil moisture (%)'),
                         ('Channels / depths', '; '.join(f'{code}: {depth} in ({float(depth)*2.54:g} cm)'
                                                       for code, depth in sensors[identifier])),
                         ('Station installed', p.get('install') or 'Not supplied')])
        station['links'].append(['Sensor inventory', AGRIMET_PARAMS + '#' + identifier])
        stations.append(unknown_activity(station, as_of, 'Sensor channels listed; current telemetry not checked'))
    return stations


def awn_stations(inventory, metadata, as_of):
    """Join public station coordinates to affirmative 8-inch moisture-probe inventory flags."""
    by_id = {str(st['UNIT_ID']): st for st in metadata['data']}
    stations = []
    for sensor in inventory['data']:
        st = by_id.get(str(sensor['unit_id']))
        if (sensor.get('soil_mois_8_in') != 'Y' or not st
                or str(st.get('STATION_VISIBILITY')).lower() != 'public'):
            continue
        if not st.get('STATION_LATDEG') or not st.get('STATION_LNGDEG'):
            continue
        station = point('AWN.' + str(sensor['unit_id']), st['STATION_NAME'], 'soil',
                        st['STATION_LATDEG'], -abs(float(st['STATION_LNGDEG'])),
                        url('https://weather.wsu.edu/weather/stations', location=st['STATION_NAME']),
                        [('Network', 'WSU AgWeatherNet'), ('Measurement', 'Soil moisture'),
                         ('Sensor depth', '8 in (20.32 cm)'),
                         ('Inventory date', inventory.get('overview_date') or 'Not supplied'),
                         ('Installed', st.get('INSTALLATION_DATE') or 'Not supplied'),
                         ('Soil series', sensor.get('soil_series') or 'Not supplied')])
        station['links'].append(['Network / sensor inventory', 'https://weather.wsu.edu/network'])
        stations.append(unknown_activity(station, as_of, 'Public moisture-probe inventory; current sensor operation not supplied'))
    return stations


def ameriflux_stations(html, as_of):
    """Accept SWC variable availability or cited instrumentation, retaining closed towers."""
    match = re.search(r"const jsonSites\s*=\s*('(?:\\.|[^'\\])*');", html)
    if not match:
        raise ValueError('AmeriFlux page has no recognizable public station catalog')
    metadata = json.loads(ast.literal_eval(match[1]))
    evidence = {site: record for record in RESEARCH_SENSORS for site in record['sites']}
    stations = []
    for st in metadata:
        variables = set()
        for product in ('base_vars', 'fluxnet_vars'):
            values = st.get(product) or []
            if len(values) > 1:
                variables.update(v.strip() for v in values[1].split(','))
        research = evidence.get(st['site_id'])
        if 'SWC' not in variables and research is None:
            continue
        if st.get('latitude') in (None, '') or st.get('longitude') in (None, ''):
            continue
        station = point('AmeriFlux.' + st['site_id'], st['site_name'], 'soil',
                        st['latitude'], st['longitude'],
                        'https://ameriflux.lbl.gov/sites/siteinfo/' + st['site_id'],
                        [('Network', 'AmeriFlux'), ('Measurement', 'Soil water content (SWC)'),
                         ('Tower years', str(st.get('tower_start') or 'Unknown') + ' — '
                          + str(st.get('tower_end') or 'Open catalog epoch'))])
        if 'SWC' in variables:
            years = st.get('base_data') or st.get('fluxnet_data') or []
            station['rows'] += [('Evidence', 'SWC listed in public AmeriFlux data-variable inventory'),
                                ('Flux data years (SWC completeness not checked)', ', '.join(map(str, years)) or 'Not supplied'),
                                ('Units / depth', 'SWC convention: % water by volume; consult site metadata for depths')]
        else:
            station['rows'] += [('Instrumentation', research['description']),
                                ('Availability', research['availability'])]
            station['links'].append(['Published sensor evidence', research['reference']])
        station['links'].append(['Variable and activity catalog', AMERIFLUX])
        end = st.get('tower_end')
        station['activity'] = epoch_activity(f'{end}-12-31' if end else None, as_of)
        if end:
            station['activity']['basis'] = f'Tower operation ended in {end} in AmeriFlux catalog; soil-probe closure dates not separately supplied'
        else:
            station['activity']['basis'] = 'Open tower catalog epoch; current soil-probe operation not independently established'
        stations.append(station)
    return stations


def cook_probe_stations(archive, as_of):
    """Read measured probe locations rather than using a farm centroid for every profile."""
    stations = []
    with zipfile.ZipFile(archive) as files:
        crs = CRS.from_wkt(files.read('CAF_sensors.prj').decode())
        transform = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
        with shapefile.Reader(shp=io.BytesIO(files.read('CAF_sensors.shp')),
                              dbf=io.BytesIO(files.read('CAF_sensors.dbf'))) as shapes:
            for item in shapes.iterShapeRecords():
                if item.shape.shapeType != shapefile.POINT:
                    raise ValueError('Cook Farm sensor geometry must contain points')
                lon, lat = transform.transform(*item.shape.points[0], errcheck=True)
                identifier = item.record['Location']
                station = point('CookFarm.' + identifier, 'Cook Farm probe ' + identifier, 'soil', lat, lon,
                                COOK_DOI, [('Network', 'Cook Farm field sensor network'),
                                          ('Measurement', 'Volumetric water content (m³/m³)'),
                                          ('Depths', '30, 60, 90, 120, 150 cm'),
                                          ('Archive period', '2007-04-20 — 2016-06-16 (archive dates; time zone not specified)'),
                                          ('Temporal resolution', 'Hourly readings and daily summaries'),
                                          ('Coordinates', 'Archive point geometry: NAD83 / UTM 11N → WGS84; metre-scale datum approximation')])
                stations.append(unknown_activity(station, as_of,
                    'Historical measurement archive; its ending date does not establish sensor closure'))
    return stations


def additional_soil_stations(catalog, domain):
    """Discover public and historical soil instruments, then clip using the map's shared rule."""
    coordinates = json.loads(catalog.record('USBR AgriMet station locations', AGRIMET))
    parameters = catalog.record('USBR AgriMet sensor channels', AGRIMET_PARAMS).decode()
    stations = agrimet_stations(coordinates, parameters, catalog.responses[AGRIMET_PARAMS]['retrieved_at_utc'])
    metadata = json.loads(catalog.record('WSU AgWeatherNet public station metadata', AWN))

    def inventory_request():
        # This is the anonymous website's public nonce flow, not account credentials.
        nonce = json.loads(catalog.fetch(url(AWN_SENSORS, action='get_token')))['temp']
        return catalog.fetch(url(AWN_SENSORS, temp=nonce))

    catalog.sources.append({'name': 'WSU AgWeatherNet soil-sensor inventory', 'url': AWN_SENSORS})
    inventory = json.loads(catalog.get(AWN_SENSORS, loader=inventory_request))
    stations += awn_stations(inventory, metadata, catalog.responses[AWN_SENSORS]['retrieved_at_utc'])
    html = catalog.record('AmeriFlux sensor variables and tower epochs', AMERIFLUX).decode()
    stations += ameriflux_stations(html, catalog.responses[AMERIFLUX]['retrieved_at_utc'])
    for record in RESEARCH_SENSORS:
        catalog.sources.append({'name': 'Published soil-sensor instrumentation', 'url': record['reference']})
    metadata = json.loads(catalog.record('Cook Farm soil-probe archive metadata', COOK))
    product = next(f for f in metadata['files'] if f['name'] == 'CAF_Sensor_Dataset_2.zip')
    archive = catalog.archive(product)
    stations += cook_probe_stations(archive, catalog.responses[product['download_url']]['retrieved_at_utc'])
    return [station for station in stations if inside(station, domain)]
