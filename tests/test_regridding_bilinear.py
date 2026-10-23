"""Offline R12 stencil, missing-neighbor, covariance and immutable-operator checks."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.interpolate import RegularGridInterpolator
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from regridding.bilinear import bilinear_operator, apply_bilinear
from regridding.bilinear_stage import initialize_bilinear, bilinear_preflight, build_bilinear_operator
from regridding.comparison_figures import render_comparison
from regridding.schema import store_template
from regridding.storage import RunStore
from test_regridding_native import synthetic_inputs, IdentityTransform
from nisar.quality import ALGORITHMS
from shapely.geometry import box
from PIL import Image


class BilinearTests(unittest.TestCase):
    def test_interior_ties_last_center_and_adjacent_floats(self):
        """Choose east/south at exact interior centers and inward at the closed final center."""
        nx, ny = np.array([0., 200., 400.]), np.array([400., 200., 0.])
        x = np.array([200., np.nextafter(200., -np.inf), 400., np.nextafter(400., np.inf), -1.])
        y = np.full(x.shape, 200.)
        idx, weights = bilinear_operator(x, y, nx, ny, np.ones(x.shape, dtype=bool))
        np.testing.assert_array_equal(idx, [[4, 5, 7, 8], [3, 4, 6, 7], [4, 5, 7, 8], [-1]*4, [-1]*4])
        np.testing.assert_array_equal(weights[0], [1., 0., 0., 0.])
        self.assertGreater(weights[1, 0], 0.)
        np.testing.assert_array_equal(weights[2], [0., 1., 0., 0.])

    def test_missing_zero_weight_neighbor_is_still_required(self):
        """At an exact valid center, a missing zero-weight neighbor invalidates target moisture/sigma."""
        idx, w = bilinear_operator([0.], [200.], [0., 200.], [200., 0.], np.array([True]))
        result = apply_bilinear(np.array([[.2, np.nan], [.3, .4]]), np.zeros((2, 2)), idx, w)
        self.assertFalse(result['moisture_valid'][0])
        self.assertFalse(result['uncertainty_valid'][0])
        result = apply_bilinear(np.array([[.2, .1], [.3, .4]]), np.array([[0., np.nan], [0., 0.]]), idx, w)
        self.assertTrue(result['moisture_valid'][0])
        self.assertEqual(result['soil_moisture'][0], .2)
        self.assertFalse(result['uncertainty_valid'][0])

    def test_positive_weight_hole_and_invalid_sigma_have_separate_masks(self):
        """Positive-weight missing values never trigger renormalization; negative sigma is unavailable."""
        idx, w = bilinear_operator([100., 100.], [100., 100.], [0., 200.], [200., 0.], np.array([True, False]))
        moisture = np.array([[-.2, 0.], [.2, .4]])
        result = apply_bilinear(moisture, np.zeros((2, 2)), idx, w)
        np.testing.assert_allclose(result['soil_moisture'], [.1, np.nan])
        np.testing.assert_array_equal(result['retrieval_error_std'], [0., np.nan])
        sigma = np.array([[.1, -.1], [.1, .1]])
        result = apply_bilinear(moisture, sigma, idx, w)
        self.assertTrue(result['moisture_valid'][0])
        self.assertFalse(result['uncertainty_valid'][0])
        moisture[0, 1] = np.nan
        self.assertFalse(apply_bilinear(moisture, np.zeros((2, 2)), idx, w)['moisture_valid'][0])

    def test_finite_field_matches_scipy_at_actual_large_coordinate_posting(self):
        """Ordered-axis interpolation agrees with an independent regular-grid implementation."""
        nx = -11500000. + np.arange(3) * 200.17900466918945
        ny = 5300000. - np.arange(3) * 200.17900467012078
        values = np.array([[.1, .3, .7], [.2, .8, -.2], [.9, .4, 0.]])
        x = np.array([nx[0], nx[1], nx[-1], .3*nx[0]+.7*nx[1]])
        y = np.array([ny[0], ny[1], ny[-1], .6*ny[1]+.4*ny[2]])
        idx, w = bilinear_operator(x, y, nx, ny, np.ones(4, dtype=bool))
        result = apply_bilinear(values, np.ones((3, 3)), idx, w, batch_size=1)
        expected = RegularGridInterpolator((ny, nx), values)(np.column_stack((y, x)))
        np.testing.assert_allclose(result['soil_moisture'], expected, rtol=1e-14, atol=1e-15)
        np.testing.assert_array_equal(result['soil_moisture'], apply_bilinear(values, np.ones((3, 3)), idx, w)['soil_moisture'])

    def test_analytic_sigma_and_shared_source_covariance(self):
        """Quadrature matches W diag(sigma²) W.T and retains dependence across overlapping stencils."""
        idx, w = bilinear_operator([100., 150.], [100., 150.], [0., 200.], [200., 0.], np.array([True, True]))
        sigma = np.array([[.02, .04], [.06, .08]])
        result = apply_bilinear(np.ones((2, 2)), sigma, idx, w)
        covariance = (w * sigma.ravel()**2) @ w.T
        np.testing.assert_allclose(result['retrieval_error_std']**2, np.diag(covariance))
        self.assertGreater(covariance[0, 1], 0.)
        self.assertNotAlmostEqual(result['retrieval_error_std'][0], float(w[0] @ sigma.ravel()))

    def test_batched_fixed_transform_and_eligibility(self):
        """Operator construction uses stored target centers and preserves strict domain exclusions."""
        geometry = dict(x=np.array([45., 135.]), y=np.array([135., 45.]),
                        center_eligible=np.array([[True, False], [True, True]]))
        idx, w = build_bilinear_operator({'inverse': IdentityTransform()}, [0., 200.], [200., 0.], geometry)
        x, y = np.meshgrid(geometry['x'], geometry['y'])
        expected = bilinear_operator(x, y, [0., 200.], [200., 0.], geometry['center_eligible'])
        np.testing.assert_array_equal(idx, expected[0])
        np.testing.assert_array_equal(w, expected[1])

    def test_operator_resume_corruption_and_completed_task_protection(self):
        """Round-trip all four slots, preserve completed slabs on resume, and detect altered weights."""
        _, _, sources, acquisitions, _ = synthetic_inputs()
        geometry = dict(epsg=5070, x=np.array([45., 135.]), y=np.array([45.]),
                        intersection_area_m2=np.full((1, 2), 8100.), center_eligible=np.ones((1, 2), dtype=bool))
        template = store_template('bilinear', geometry, acquisitions, sources)
        idx, w = bilinear_operator([[45., 135.]], [[45., 45.]], [0., 200.], [200., 0.], np.ones((1, 2), dtype=bool))
        options = dict(spatial_chunks=(2, 2), compressors=None)
        arrays = apply_bilinear(np.zeros((2, 2)), np.zeros((2, 2)), idx, w)
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(directory, {'synthetic': True}) as run:
                initialize_bilinear(run, template, idx, w, {'test': True}, {'shape': [2, 2]}, options)
                run.write_task('bilinear', 0, 'PMI', arrays, provenance={'test': True})
            with RunStore(directory, {'synthetic': True}, resume=True) as run:
                initialize_bilinear(run, template, idx, w, {'test': True}, {'shape': [2, 2]}, options)
                np.testing.assert_array_equal(run.read_task('bilinear', 0, 'PMI').soil_moisture, [[0., 0.]])
                with self.assertRaises(FileExistsError):
                    run.write_task('bilinear', 0, 'PMI', arrays, provenance={'test': True})
            group = zarr.open_group(Path(directory) / 'bilinear.zarr', mode='r+')
            group['operator/weight'][0, 0, 0] = 0.
            with self.assertRaisesRegex(ValueError, 'static checksum'):
                with RunStore(directory, {'synthetic': True}, resume=True):
                    pass

    def test_preflight_includes_four_slot_operator_and_pending_tasks(self):
        """Disk estimates include all four coefficient slots without compression savings."""
        vm = 'Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 1.\nPages inactive: 2.\nPages speculative: 3.\n'
        with patch('regridding.bilinear_stage.subprocess.check_output', return_value=vm), \
                patch('regridding.bilinear_stage.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            result = bilinear_preflight((2, 3), (2, 2), 6, '.', True)
        self.assertEqual(result['estimated_disk_bytes'], 1024**2 * (6*18 + 75 + 64))
        self.assertGreater(result['estimated_disk_bytes'], result['available_disk_bytes'])
        self.assertGreater(result['estimated_peak_memory_bytes'], result['available_reclaimable_memory_bytes'])

    def test_bilinear_figure_caption_counts_and_missing_uncertainty(self):
        """Export the proper method/uncertainty labels and retain independent missing-panel counts."""
        _, _, _, acquisitions, frame = synthetic_inputs()
        selected = dict(metadata=frame['metadata'], recommended_percent={a: 100. for a in ALGORITHMS},
                        source_filenames=['synthetic.h5'], score=1., acquisition_id=acquisitions[0]['id'])
        polygon = box(0, 0, 90, 90)
        data = {a: dict(soil_moisture=np.array([[.2]]), retrieval_error_std=np.array([[np.nan if a == 'TSR' else .03]]))
                for a in ALGORITHMS}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bilinear.png'
            result = render_comparison(path, 'bilinear', polygon, [polygon], np.array([True]), [polygon],
                                       {a: np.array([0]) for a in ALGORITHMS}, data, selected, [3200, 3232], {'synthetic': True})
            self.assertIn('Bilinear interpolation', result['caption'])
            self.assertIn('weighted native variances', result['caption'])
            self.assertIn('A12', result['assumptions'])
            self.assertNotIn('A10', result['assumptions'])
            self.assertEqual(result['panel_counts']['TSR/retrieval_error_std']['missing'], 1)
            with Image.open(path) as png:
                self.assertEqual(json.loads(png.info['Provenance'])['panel_counts'], result['panel_counts'])


if __name__ == '__main__':
    unittest.main()
