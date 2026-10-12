"""Offline checks: pixi run python -m unittest discover -s tests -p test_nisar_footprints.py."""

import copy
from pathlib import Path
import sys
import unittest
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from maps.nisar_footprints import cmr_geometry, tracks_from_items, track_hulls


class NisarFootprintTests(unittest.TestCase):
    def test_track_hulls_combine_only_overlapping_frames_without_clipping(self):
        """A convex outline spans frames and gaps, excluding distant and edge-touching footprints."""
        frames = [dict(track=5, frame=number, direction='ASCENDING', geometry=mapping(box(*bounds)))
                  for number, bounds in [(25, (-2, 1, 1, 2)), (26, (3, 4, 7, 8)),
                                         (27, (20, 20, 21, 21))]]
        frames.append(dict(track=6, frame=28, direction='DESCENDING', geometry=mapping(box(5, 1, 6, 2))))
        original = copy.deepcopy(frames)
        result = track_hulls(frames, mapping(box(0, 0, 5, 5)))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['frames'], [25, 26])
        self.assertEqual(shape(result[0]['geometry']).bounds, (-2, 1, 7, 8))
        hull = shape(result[0]['geometry'])
        originals = unary_union([shape(frame['geometry']) for frame in frames[:2]])
        self.assertTrue(hull.equals(originals.convex_hull))
        self.assertTrue(hull.covers(originals))
        self.assertGreater(hull.area, originals.area)
        self.assertLess(hull.area, box(*hull.bounds).area)
        self.assertEqual(frames, original)

    def test_track_overlap_respects_polygon_holes(self):
        """A region inside a footprint hole is not covered just because its bounding box overlaps."""
        frame = dict(track=5, frame=25, direction='ASCENDING', geometry=cmr_geometry(self.item['umm']))
        self.assertEqual(track_hulls([frame], mapping(box(1.2, 1.2, 1.8, 1.8))), [])

    def test_repeated_acquisition_polygons_can_overlap(self):
        """Overlapping repeat acquisitions form a valid convex hull."""
        geometry = {'type': 'MultiPolygon', 'coordinates': [mapping(box(0, 0, 2, 2))['coordinates'],
                                                          mapping(box(1, 1, 3, 3))['coordinates']]}
        frames = [dict(track=5, frame=25, direction='ASCENDING', geometry=geometry)]
        result = track_hulls(frames, mapping(box(.5, .5, 1.5, 1.5)))
        self.assertEqual(shape(result[0]['geometry']).bounds, (0, 0, 3, 3))
        self.assertTrue(shape(result[0]['geometry']).is_valid)

    def test_nonoverlapping_acquisition_excluded_and_tracks_separate(self):
        """A distant acquisition in the same frame is excluded; separate tracks retain separate hulls."""
        near = box(0, 0, 2, 2)
        geometry = {'type': 'MultiPolygon', 'coordinates': [mapping(near)['coordinates'],
                      mapping(box(20, 20, 21, 21))['coordinates']]}
        frames = [dict(track=5, frame=25, direction='ASCENDING', geometry=geometry),
                  dict(track=6, frame=25, direction='DESCENDING', geometry=mapping(box(1, 1, 3, 3)))]
        result = track_hulls(frames, mapping(box(.5, .5, 1.5, 1.5)))
        self.assertEqual([hull['track'] for hull in result], [5, 6])
        self.assertTrue(shape(result[0]['geometry']).equals(near))
        self.assertEqual(shape(result[1]['geometry']).bounds, (1, 1, 3, 3))

    def setUp(self):
        points = [{'Longitude': lon, 'Latitude': lat}
                  for lon, lat in [(0, 0), (4, 0), (4, 4), (0, 4)]]
        self.item = {'meta': {'concept-id': 'G1-ASF'}, 'umm': {
            'GranuleUR': 'one', 'CollectionReference': {'ShortName': 'SME2'},
            'TemporalExtent': {'RangeDateTime': {
                'BeginningDateTime': '2026-01-01T00:00:00Z',
                'EndingDateTime': '2026-01-01T00:00:35Z'}},
            'AdditionalAttributes': [{'Name': key, 'Values': [value]} for key, value in (
                ('PRODUCT_TYPE', 'SME2'), ('TRACK_NUMBER', '5'), ('FRAME_NUMBER', '25'),
                ('ASCENDING_DESCENDING', 'ASCENDING'))],
            'SpatialExtent': {'HorizontalSpatialDomain': {'Geometry': {'GPolygons': [{
                'Boundary': {'Points': points}, 'ExclusiveZone': {'Boundaries': [
                    {'Points': [{'Longitude': lon, 'Latitude': lat}
                                for lon, lat in [(1, 1), (1, 2), (2, 2), (2, 1)]]}]}}]}}}}}
        self.donor = dict(granule='one.h5', source_pixels=3, trackNumber=5,
                          frameNumber=25, orbitPassDirection='Ascending')

    def tracks(self, items, donors):
        return tracks_from_items(items, {g['granule'].removesuffix('.h5'): g
                                        for g in donors if g['source_pixels'] > 0})

    def test_donor_filter_repeat_geometry_and_holes(self):
        """Exclude zero-pixel donors; repeats keep one footprint with original holes."""
        second = copy.deepcopy(self.item)
        second['umm']['GranuleUR'] = 'two'
        second['meta']['concept-id'] = 'G2-ASF'
        third = copy.deepcopy(self.item)
        third['umm']['GranuleUR'] = 'three'
        tracks = self.tracks([self.item, second, third], [self.donor,
                          dict(self.donor, granule='two.h5'),
                          dict(self.donor, granule='three.h5', source_pixels=0)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual([g['id'] for g in tracks[0]['granules']], ['one', 'two'])
        polygons = tracks[0]['geometry']['coordinates']
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(polygons[0]), 2)
        self.assertEqual(polygons[0][0][0], polygons[0][0][-1])
        self.assertEqual(polygons[0][1][0], polygons[0][1][-1])

    def test_mismatched_catalog_or_missing_donor(self):
        """Missing donors and disagreeing track identities fail instead of guessing."""
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.tracks([self.item], [dict(self.donor, granule='missing.h5')])
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.tracks([self.item], [dict(self.donor, trackNumber=6)])

    def test_distinct_footprints_and_missing_geometry(self):
        """Repeat acquisitions retain distinct rings; missing polygons are rejected."""
        second = copy.deepcopy(self.item)
        second['umm']['GranuleUR'] = 'two'
        second['umm']['SpatialExtent']['HorizontalSpatialDomain']['Geometry']['GPolygons'][0][
            'Boundary']['Points'][0]['Longitude'] = -1
        tracks = self.tracks([self.item, second], [self.donor, dict(self.donor, granule='two.h5')])
        self.assertEqual(len(tracks[0]['geometry']['coordinates']), 2)
        second['umm']['SpatialExtent']['HorizontalSpatialDomain']['Geometry']['GPolygons'] = []
        with self.assertRaisesRegex(ValueError, 'no polygon'):
            cmr_geometry(second['umm'])


if __name__ == '__main__':
    unittest.main()
