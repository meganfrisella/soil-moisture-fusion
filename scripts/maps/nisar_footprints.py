"""Catalog footprints for SME2 map donors, in WGS84 longitude/latitude degrees.

Footprints describe acquisition coverage, not valid retrievals or uncertainty.
Keep original CMR polygon rings and holes.
Used by ``pixi run python scripts/build_gaia_map.py --sme2-map PATH`` and by
``pixi run python scripts/visualize_nisar_sme2.py --help`` when publishing HTML.
"""

from maps.common import read_manifest
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


def track_hulls(frames, region):
    """Enclose each track's overlapping catalog footprints in one convex polygon.

    Test original polygons against the region before taking the convex hull in
    WGS84 longitude/latitude degrees; touching edges alone do not count. Retain
    full extents across frames, without clipping to the region. Hulls fill gaps
    and holes and therefore do not imply continuous acquisition coverage.
    """
    region = shape(region)
    groups = {}
    for frame in frames:
        footprint = shape(frame['geometry'])
        polygons = footprint.geoms if footprint.geom_type == 'MultiPolygon' else [footprint]
        # Test separately: repeat acquisitions can overlap within a MultiPolygon.
        overlapping = [polygon for polygon in polygons if polygon.intersection(region).area > 0]
        if not overlapping:
            continue
        group = groups.setdefault(frame['track'], {'frames': set(), 'directions': set(), 'footprints': []})
        group['frames'].add(frame['frame'])
        group['directions'].add(frame['direction'])
        group['footprints'].extend(overlapping)
    result = []
    for number, group in sorted(groups.items()):
        hull = mapping(unary_union(group['footprints']).convex_hull)
        result.append(dict(track=number, frames=sorted(group['frames']),
                           direction=' / '.join(sorted(group['directions'])),
                           geometry={'type': 'MultiPolygon', 'coordinates': [hull['coordinates']]}))
    return result


def cmr_geometry(umm):
    """Read original CMR polygon rings, closing unclosed boundaries."""
    geometry = umm['SpatialExtent']['HorizontalSpatialDomain']['Geometry']
    polygons = []
    for polygon in geometry.get('GPolygons', []):
        rings = [polygon['Boundary']] + polygon.get('ExclusiveZone', {}).get('Boundaries', [])
        coordinates = []
        for ring in rings:
            points = [[p['Longitude'], p['Latitude']] for p in ring['Points']]
            if not points:
                raise ValueError('Empty SME2 footprint ring')
            if points[0] != points[-1]:
                points.append(points[0])
            coordinates.append(points)
        polygons.append(coordinates)
    if not polygons:
        raise ValueError('SME2 granule has no polygon footprint')
    return {'type': 'MultiPolygon', 'coordinates': polygons}


def contributing_tracks(map_path):
    """Match positive-donor granules to the exact catalog used by the SME2 HTML.

    Acquisition times and query bounds are UTC. The HTML source-pixel counts
    identify donors within its domain; the returned footprints remain unclipped.
    """
    manifest = read_manifest(map_path)
    if manifest.get('schema') == 'nisar-sme2-box-v1':
        tracks = manifest.get('tracks', [])
        donors = {g['granule'].removesuffix('.h5') for f in manifest['frames'] for g in f['granules']}
        represented = {g['id'] for track in tracks for g in track['granules']}
        if not tracks or represented != donors:
            raise ValueError('SME2 contributing granules missing from embedded track metadata')
        return tracks, dict(manifest['provenance'], html_schema=manifest['schema'])
    raise ValueError('Expected nisar-sme2-box-v1 HTML schema')


def tracks_from_items(items, donors):
    """Group exact CMR polygons for contributing granules, preserving holes and identities."""
    groups, found = {}, set()
    for item in items:
        u = item['umm']
        name = u['GranuleUR'].removesuffix('.h5')
        if name not in donors:
            continue
        found.add(name)
        attrs = {a['Name']: a['Values'] for a in u['AdditionalAttributes']}
        if attrs.get('PRODUCT_TYPE') != ['SME2']:
            raise ValueError('Non-SME2 donor in selection')
        track, frame = int(attrs['TRACK_NUMBER'][0]), int(attrs['FRAME_NUMBER'][0])
        direction = attrs['ASCENDING_DESCENDING'][0]
        donor = donors[name]
        if (track, frame, direction.lower()) != (
                donor['trackNumber'], donor['frameNumber'], donor['orbitPassDirection'].lower()):
            raise ValueError('SME2 donor identity disagrees with catalog')
        group = groups.setdefault((track, direction, frame), {
            'id': f'{track:03d}_{direction[0]}_{frame:03d}',
            'track': track, 'frame': frame, 'direction': direction,
            'granules': [], 'footprints': []})
        geometry = cmr_geometry(u)
        if geometry not in group['footprints']:
            group['footprints'].append(geometry)
        concept = item['meta']['concept-id']
        period = u['TemporalExtent']['RangeDateTime']
        group['granules'].append({
            'id': name, 'concept_id': concept,
            'collection': u['CollectionReference']['ShortName'],
            'start': period['BeginningDateTime'], 'end': period['EndingDateTime'],
            'polarization': attrs.get('FREQUENCY_A_POLARIZATION', []),
            'source_pixels': donor['source_pixels'],
            'metadata_url': f'https://cmr.earthdata.nasa.gov/search/concepts/{concept}.umm_json'})
    if found != donors.keys() or not found:
        raise ValueError('SME2 contributing granules missing from catalog selection')
    tracks = []
    for key in sorted(groups):
        group = groups[key]
        group['granules'].sort(key=lambda g: (g['start'], g['concept_id']))
        group['geometry'] = {'type': 'MultiPolygon', 'coordinates': [
            polygon for shape in group.pop('footprints') for polygon in shape['coordinates']]}
        tracks.append(group)
    return tracks
