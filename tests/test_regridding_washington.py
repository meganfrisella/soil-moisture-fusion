"""Check coordinate operations, required-area gates and verified PROJ grid caching."""

import hashlib
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from pyproj import network
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'),str(ROOT/'scripts')]
from regridding import resources, transforms, washington
from test_regridding_geometry import Identity


def fixture(domain):
    """Return two synthetic cells, with the second outside the operation's stated bounds."""
    return dict(metadata=dict(shape_rows_columns=[1,2],bounds_5070=[0,0,180,90],
                              pinned_operation=dict(policy=washington.POLICY,operations={
                                  'inverse':{'area_of_use_bounds_wgs84':[0,0,90,90]}})),
                forward=Identity(),inverse=Identity(),domain_6933=domain)


class WashingtonTests(unittest.TestCase):
    def test_fixed_operation_ignores_candidate_ranking(self):
        """Select only the exact approved operation even when an alternative is ranked first."""
        directory = Path('/synthetic/grids')
        datum = SimpleNamespace(to_json_dict=lambda:{'id':{'code':1737}},grids=[
            SimpleNamespace(short_name='us_noaa_WO.tif',available=True,full_name=str(directory/'us_noaa_WO.tif'))])
        area = SimpleNamespace(west=-124.79,south=41.98,east=-116.47,north=49.05,name='Washington and Oregon')
        def candidates(source,target,**kwargs):
            pipeline = washington.FORWARD_PIPELINE if source==6933 else washington.INVERSE_PIPELINE
            good = SimpleNamespace(definition=pipeline,operations=[datum],area_of_use=area,description='Washington',accuracy=2.)
            return SimpleNamespace(transformers=[SimpleNamespace(definition='other pipeline'),good])
        fixed = SimpleNamespace(has_inverse=True,is_network_enabled=False)
        with patch.object(washington,'configure_local_grid',return_value=directory), \
                patch.object(washington,'TransformerGroup',side_effect=candidates), \
                patch.object(washington,'sha256_file',return_value=washington.GRID_SPECS['us_noaa_WO.tif'][1]), \
                patch.object(washington.Transformer,'from_pipeline',return_value=fixed) as factory:
            result = washington.washington_operations((-121.,46.,-118.,48.),directory)
            self.assertEqual([c.args[0] for c in factory.call_args_list],
                             [washington.FORWARD_PIPELINE,washington.INVERSE_PIPELINE])
            self.assertEqual(result['metadata']['policy'],washington.POLICY)
            with self.assertRaisesRegex(ValueError,'outside Washington'):
                washington.washington_operations((-130.,46.,-118.,48.),directory)

    def test_absent_exact_operation_has_no_fallback(self):
        """An available alternative cannot replace the explicitly approved Washington operation."""
        with patch.object(washington,'configure_local_grid',return_value=Path('/synthetic')), \
                patch.object(washington,'TransformerGroup',return_value=SimpleNamespace(
                    transformers=[SimpleNamespace(definition='available fallback')])):
            with self.assertRaisesRegex(ValueError,'absent or ambiguous'):
                washington.washington_operations((-121.,46.,-118.,48.),'/synthetic')

    def test_outside_area_padding_must_have_zero_intersection(self):
        """Permit only zero-support padding outside operation bounds; never drop a contributing cell."""
        with patch.object(washington.Transformer,'from_crs',return_value=Identity()):
            result = washington.verify_required_grid(fixture(box(0,0,90,90)))
            self.assertEqual(result['status'],'passed')
            self.assertEqual(result['outside_area_of_use_cells'],1)
            self.assertEqual(result['checked_vertex_center_entries'],28)
            self.assertEqual(result['max_roundtrip_residual_epsg5070_m'],0.)
            with self.assertRaisesRegex(ValueError,'contributing target cell'):
                washington.verify_required_grid(fixture(box(0,0,100,90)))

    def test_nonfinite_coordinate_fails_verification(self):
        """Finite input/output checks fail closed even when a transformer returns NaN without raising."""
        grid = fixture(box(0,0,90,90))
        grid['forward'] = SimpleNamespace(transform=lambda x,y,errcheck:(np.full_like(x,np.nan),y))
        with patch.object(washington.Transformer,'from_crs',return_value=Identity()):
            with self.assertRaisesRegex(ValueError,'Nonfinite'):
                washington.verify_required_grid(grid)


class GridCacheTests(unittest.TestCase):
    def test_verified_cache_is_reused_and_corruption_is_not_overwritten(self):
        """Download a missing fixture once, then reject corruption without recovery."""
        payload = b"synthetic grid bytes"
        def transfer(command, stdout, check):
            stdout.write(payload)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(resources.GRID_SPECS, {resources.GRID_NAME:
                           (len(payload), hashlib.sha256(payload).hexdigest())}), \
                patch.object(resources.subprocess, "run", side_effect=transfer) as fetch:
            self.assertEqual(resources.ensure_grid(directory)["status"], "downloaded")
            result = resources.ensure_grid(directory)
            self.assertEqual(result["status"], "validated existing")
            self.assertEqual(result["downloaded_bytes"], 0)
            self.assertEqual(fetch.call_count, 1)
            path = Path(directory) / resources.GRID_NAME
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum/size mismatch"):
                resources.ensure_grid(directory)
            self.assertEqual(path.read_bytes(), b"corrupt")
            self.assertEqual(fetch.call_count, 1)

    def test_bad_transfer_is_not_published(self):
        """Reject a wrong-checksum transfer and remove only the temporary download."""
        def transfer(command, stdout, check):
            stdout.write(b"bad")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(resources.subprocess, "run", side_effect=transfer):
            with self.assertRaises(ValueError):
                resources.ensure_grid(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])


class TransformationAvailabilityTests(unittest.TestCase):
    def setUp(self):
        """Keep tests independent of the installed grid inventory and network setting."""
        self.bounds = (-121.241744, 46.057370, -117.072163, 48.076483)
        previous_network = network.is_network_enabled()
        self.addCleanup(network.set_network_enabled, previous_network)
        self.candidate = SimpleNamespace(
            description="synthetic candidate", accuracy=4.0,
            definition="synthetic pipeline", operations=[])

    def test_available_fallback_does_not_satisfy_best_requirement(self):
        """Report the missing preferred grid without using the available fallback."""
        grid = SimpleNamespace(
            short_name="missing.tif", full_name="", available=False,
            url="https://example.org/missing.tif", open_license=True, direct_download=True)
        operation = SimpleNamespace(name="preferred", accuracy=2.0, grids=[grid])
        group = SimpleNamespace(
            best_available=False, transformers=[self.candidate],
            unavailable_operations=[operation])
        with patch.object(transforms, "TransformerGroup", return_value=group) as factory:
            result = transforms.inspect_transformations(self.bounds)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["coordinate_transformations_executed"])
        self.assertFalse(result["network_enabled"])
        self.assertEqual(result["operations"][0]["unavailable_candidates"][0]["grids"][0]["name"],
                         "missing.tif")
        for call in factory.call_args_list:
            self.assertFalse(call.kwargs["allow_ballpark"])
            self.assertTrue(call.kwargs["always_xy"])
            self.assertEqual(call.kwargs["area_of_interest"].west_lon_degree, self.bounds[0])

    def test_all_preferred_operations_available_reports_ready(self):
        """Readiness requires a usable preferred operation for every required CRS pair."""
        group = SimpleNamespace(
            best_available=True, transformers=[self.candidate], unavailable_operations=[])
        with patch.object(transforms, "TransformerGroup", return_value=group):
            result = transforms.inspect_transformations(self.bounds)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["operations"]), len(transforms.CRS_PAIRS))
        self.assertTrue(all(item["best_available"] for item in result["operations"]))
        group.transformers = []
        with patch.object(transforms, "TransformerGroup", return_value=group):
            result = transforms.inspect_transformations(self.bounds)
        self.assertEqual(result["status"], "unavailable")

    def test_invalid_domain_fails_before_operation_lookup(self):
        """Reject reversed WGS84 bounds without attempting any coordinate operation."""
        with patch.object(transforms, "TransformerGroup") as factory:
            with self.assertRaises(ValueError):
                transforms.inspect_transformations((0, 1, -1, 2))
        factory.assert_not_called()


if __name__=='__main__':
    unittest.main()
