"""Shared WGS84 boxes and EPSG:6933 coordinate transforms.

Corners use latitude/longitude in degrees; projected coordinates use metres.
Antimeridian-crossing boxes must be split before constructing BoundingBox.
"""

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely.geometry import box

FORWARD = Transformer.from_crs(4326, 6933, always_xy=True)
INVERSE = Transformer.from_crs(6933, 4326, always_xy=True)


@dataclass(frozen=True)
class BoundingBox:
    """A non-antimeridian-crossing WGS84 rectangle within EPSG:6933's latitude range."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self):
        if not all(np.isfinite(self.bounds)):
            raise ValueError('Bounding-box coordinates must be finite')
        if not -180 <= self.west < self.east <= 180:
            raise ValueError('Require -180 <= west < east <= 180; split antimeridian-crossing boxes')
        if not -86 <= self.south < self.north <= 86:
            raise ValueError('Require -86 <= south < north <= 86 for native EPSG:6933')

    @classmethod
    def from_corners(cls, top_left, bottom_right):
        """Convert (latitude, longitude) northwest/southeast corners to named bounds."""
        north, west = top_left
        south, east = bottom_right
        return cls(west, south, east, north)

    @property
    def bounds(self):
        return self.west, self.south, self.east, self.north

    @property
    def projected(self):
        xmin, ymin = FORWARD.transform(self.west, self.south)
        xmax, ymax = FORWARD.transform(self.east, self.north)
        return box(xmin, ymin, xmax, ymax)

    @property
    def geometry(self):
        """Return the exact WGS84 rectangle as a GeoJSON polygon."""
        return {'type': 'Polygon', 'coordinates': [[
            [self.west, self.south], [self.east, self.south],
            [self.east, self.north], [self.west, self.north], [self.west, self.south]]]}
