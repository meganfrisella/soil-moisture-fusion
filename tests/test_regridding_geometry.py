"""Small synthetic checks of R02 fixed geometry, support, and center rules."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from regridding import geometry


class Identity:
    """Synthetic coordinate mapping in metres, requiring explicit error checking."""
    def transform(self, x, y, errcheck):
        """Return identity coordinates without any installed PROJ grids."""
        if not errcheck:
            raise AssertionError("Coordinate errors must be checked")
        return x, y


class Curved(Identity):
    """Known nonlinear mapping that bends horizontal source edges."""
    def transform(self, x, y, errcheck):
        """Add a quadratic northing displacement while preserving eastings."""
        return np.asarray(x), np.asarray(y) + .001 * np.asarray(x)**2


class GeometryTests(unittest.TestCase):
    def test_outward_snap_handles_negative_coordinates(self):
        """Negative projected coordinates snap outward, with north-up center offsets."""
        self.assertEqual(geometry.snapped_bounds((-91, -1, 89, 181)), (-180, -90, 90, 270))

    def test_shared_edges_have_identical_vertices(self):
        """Neighboring rectangles share bit-identical edge vertices after reversal."""
        left = geometry.rectangle_vertices((0, 0, 90, 90), 30)
        right = geometry.rectangle_vertices((90, 0, 180, 90), 30)
        np.testing.assert_array_equal(
            np.unique(left[left[:, 0] == 90], axis=0),
            np.unique(right[right[:, 0] == 90], axis=0))

    def test_fixed_transform_preserves_area_and_rejects_bad_geometry(self):
        """Preserve exact area and fail on nonfinite coordinates or collapsed polygons."""
        polygon = geometry.transform_rectangle((0, 0, 90, 90), Identity(), 30)
        self.assertEqual(polygon.area, 8100)
        for value, message in ((np.nan, "Nonfinite"), (0, "Invalid")):
            transformer = Mock()
            transformer.transform.side_effect = lambda x, y, errcheck: (x, np.full_like(y, value))
            with self.assertRaisesRegex(ValueError, message):
                geometry.transform_rectangle((0, 0, 90, 90), transformer, 30)

    def test_curved_edges_transform_fixed_vertices_once(self):
        """Transform interior edge vertices, with bounded work even for curved edges."""
        transformer = Mock(wraps=Curved())
        polygon = geometry.transform_rectangle((0, 0, 90, 90), transformer, 30)
        transformer.transform.assert_called_once()
        self.assertEqual(len(polygon.exterior.coords), 13)
        np.testing.assert_allclose(np.asarray(polygon.exterior.coords)[:4],
                                   [[0, 0], [30, .9], [60, 3.6], [90, 8.1]])
        self.assertAlmostEqual(polygon.area, 8100, places=7)

    def test_partial_outside_center_and_boundary_center_masks(self):
        """Keep clipped slivers, exclude outside centers, and include exact boundary centers."""
        grid = {"metadata": {"shape_rows_columns": [1, 3], "bounds_5070": (0, 0, 270, 90)},
                "inverse": Identity(), "domain_6933": box(60, 0, 135, 90)}
        cells = geometry.target_cells(grid, [0, 0, 0], [0, 1, 2])
        self.assertEqual([c["intersection_area_m2"] for c in cells], [2700, 4050, 0])
        self.assertEqual([c["partial_cell"] for c in cells], [True, True, False])
        self.assertEqual([c["center_eligible"] for c in cells], [False, True, False])
        self.assertEqual(cells[0]["center_5070"], (45, 45))

    def test_curved_shared_edges_are_independent_of_batch(self):
        """Separate batches preserve identical curved shared edges and clipped areas."""
        grid = {"metadata": {"shape_rows_columns": [2, 1], "bounds_5070": (0, 0, 90, 180)},
                "inverse": Curved(), "domain_6933": box(10, 20, 80, 160)}
        together = geometry.target_cells(grid, [0, 1], [0, 0])
        separate = [geometry.target_cells(grid, [row], [0])[0] for row in (0, 1)]
        for first, second in zip(together, separate):
            self.assertTrue(first["polygon_6933"].equals_exact(second["polygon_6933"], 0))
            self.assertEqual(first["intersection_area_m2"], second["intersection_area_m2"])
        shared = together[0]["polygon_6933"].intersection(together[1]["polygon_6933"])
        self.assertEqual(shared.area, 0)
        self.assertGreater(shared.length, 90)

    def test_zero_area_touches_and_invalid_indices(self):
        """Exclude edge-only touches and reject indices outside the defined lattice."""
        self.assertEqual(geometry.clipped_geometry(box(0, 0, 1, 1), box(1, 0, 2, 1))[1:],
                         (0, False, False))
        grid = {"metadata": {"shape_rows_columns": [1, 1], "bounds_5070": (0, 0, 90, 90)},
                "inverse": Identity(), "domain_6933": box(0, 0, 90, 90)}
        with self.assertRaisesRegex(ValueError, "outside"):
            geometry.target_cells(grid, [1], [0])
        with self.assertRaisesRegex(ValueError, "integers"):
            geometry.target_cells(grid, [0.5], [0])


if __name__ == "__main__":
    unittest.main()
