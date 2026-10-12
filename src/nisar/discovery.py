"""Shared bounding-box catalog search and checksum-verified granule caching.

Used by the visualization and statistics scripts. Search
bounds are WGS84 longitude/latitude degrees; date limits are inclusive UTC days.
CMR pagination: https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ingest.downloads import utc_date_bounds
from nisar import catalog


def date_limits(start_date=None, end_date=None):
    """Optional inclusive UTC calendar dates become a half-open interval."""
    lower = utc_date_bounds(start_date, start_date)[0] if start_date else None
    upper = utc_date_bounds(end_date, end_date)[1] if end_date else None
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError('Start date must not be after end date')
    return lower, upper


def fetch_page(address, cursor=None):
    """Read a public CMR page and its Search-After cursor, without credentials."""
    headers = {'Accept': 'application/json'}
    if cursor:
        headers['CMR-Search-After'] = cursor
    with urlopen(Request(address, headers=headers), timeout=catalog.TIMEOUT_SECONDS) as response:
        return json.load(response), response.headers.get('CMR-Search-After')


def search_box(bounds, start_date=None, end_date=None, get_page=fetch_page):
    """Read all current PROVISIONAL granules intersecting west/south/east/north.

    CMR supplies the latest metadata revision per catalog granule. Distinct
    products are retained even when their acquisition timestamps coincide.
    Detect changed hit counts, repeated records and incomplete pagination.
    """
    lower, upper = date_limits(start_date, end_date)
    params = {'short_name': catalog.COLLECTION, 'options[short_name][pattern]': 'true',
              'bounding_box': ','.join(map(str, bounds)), 'page_size': 1000,
              'sort_key[]': 'start_date'}
    if lower is not None or upper is not None:
        params['temporal'] = ','.join([
            lower.isoformat().replace('+00:00', 'Z') if lower else '',
            (upper - timedelta(microseconds=1)).isoformat().replace('+00:00', 'Z') if upper else ''])
    address = catalog.CMR_URL + '?' + urlencode(params)
    granules, seen, cursors = [], set(), set()
    cursor, expected = None, None
    while True:
        page, next_cursor = get_page(address, cursor)
        if 'errors' in page:
            raise ValueError('CMR rejected the catalog query')
        hits = int(page['hits'])
        if expected is not None and hits != expected:
            raise RuntimeError('CMR results changed during pagination; rerun')
        expected = hits
        for item in page['items']:
            granule = catalog.parse_granule(item, catalog.COLLECTION, lower, upper)
            if granule.name in seen:
                raise RuntimeError('Repeated granule during CMR pagination')
            seen.add(granule.name)
            granules.append(granule)
        if len(granules) == expected:
            break
        if len(granules) > expected or not page['items'] or not next_cursor or next_cursor in cursors:
            raise RuntimeError('Incomplete or inconsistent CMR pagination')
        cursors.add(next_cursor)
        cursor = next_cursor
    granules.sort(key=lambda g: (g.start, g.track, g.frame, g.name))
    return granules, {'catalog_query': address, 'catalog_granules': len(granules),
                      'catalog_retrieved_at_utc': datetime.now(timezone.utc).isoformat()}


def ensure_granules(granules, data_dir, stats=None):
    """Reuse verified files; authenticate lazily and cache missing originals."""
    data_dir = Path(data_dir)
    opener = None
    for number, granule in enumerate(granules, 1):
        path = data_dir / granule.name
        if path.exists():
            catalog.verify_file(path, granule)
            status = 'verified existing'
        else:
            if opener is None:
                opener = catalog.earthdata_opener()
            status = catalog.download_granule(granule, opener, data_dir, stats)
        print(f'[{number}/{len(granules)}] {status}: {granule.name}', flush=True)
        yield path
