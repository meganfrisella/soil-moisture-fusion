"""Small offline R10 boundary, missing-source, operator and geometry checks."""

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from shapely.geometry import box
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'),str(ROOT/'scripts')]
from regridding.nearest import nearest_indices, apply_nearest, geometry_batch
from regridding.geometry import target_cells
from regridding.nearest_stage import initialize_nearest
from regridding.schema import store_template
from regridding.storage import RunStore
from regridding.comparison_figures import crop_native
from test_regridding_native import synthetic_inputs
from test_regridding_geometry import Identity, Curved


class NearestTests(unittest.TestCase):
    def test_exact_ties_and_adjacent_floats_without_halo(self):
        """Exact edges go east/south; adjacent floats stay on their side; exterior ties are missing."""
        layout = dict(xo=0.,yo=200.,dx=200.,dy=-200.,r0=0,r1=2,c0=0,c1=3)
        x = np.array([-100,100,np.nextafter(100.,-np.inf),np.nextafter(100.,np.inf),500,0,0,0,0])
        y = np.array([300,100,200,200,200,100,np.nextafter(100.,np.inf),np.nextafter(100.,-np.inf),-100])
        out = nearest_indices(x,y,layout,box(-100,-100,500,300),np.ones(x.shape,dtype=bool))
        np.testing.assert_array_equal(out,[0,4,0,1,-1,3,0,3,-1])

    def test_actual_posting_large_origin_and_strict_domain(self):
        """Use exact HDF5 posting at large coordinates; exclude outside centers without snapping."""
        dx,dy = 200.17900466918945,-200.17900467012078
        layout = dict(xo=-17367430.38886722,yo=7314440.739474776,dx=dx,dy=dy,r0=100,r1=102,c0=100,c1=103)
        left,top = layout['xo']+100*dx-dx/2,layout['yo']+100*dy-dy/2
        edge = left+dx
        x = np.array([np.nextafter(edge,-np.inf),edge,np.nextafter(edge,np.inf),left])
        y = np.full(4,top+dy/2)
        out = nearest_indices(x,y,layout,box(left+1,top+2*dy,left+3*dx,top),np.ones(4,dtype=bool))
        np.testing.assert_array_equal(out,[0,1,1,-1])

    def test_missing_sources_independent_sigma_and_shared_error(self):
        """Do not cross holes; preserve negative/zero moisture and zero sigma; reuse shared sources."""
        moisture = np.array([[np.nan,0.,-.2,.4]])
        sigma = np.array([[.1,0.,np.nan,.03]])
        indices = np.array([[0,1,2,3,3,-1]])
        result = apply_nearest(moisture,sigma,indices)
        np.testing.assert_array_equal(result['soil_moisture'],[[np.nan,0.,-.2,.4,.4,np.nan]])
        np.testing.assert_array_equal(result['retrieval_error_std'],[[np.nan,0.,np.nan,.03,.03,np.nan]])
        # The same native perturbation must appear identically in both mapped cells.
        perturbed = apply_nearest(moisture+np.array([[0,0,0,.012]]),sigma,indices)
        np.testing.assert_array_equal(perturbed['soil_moisture'][0,3:5],[.4+.012,.4+.012])

    def test_vectorized_fixed_geometry_matches_scalar(self):
        """Batch intersections/eligibility equal scalar geometry including curves and partial cells."""
        for transform in (Identity(),Curved()):
            grid = dict(metadata=dict(shape_rows_columns=[2,3],bounds_5070=(0,0,270,180)),
                        inverse=transform,domain_6933=box(60,20,135,160))
            rows,cols = np.indices((2,3)).reshape(2,-1)
            scalar = target_cells(grid,rows,cols)
            area,centers,partial,*_ = geometry_batch(grid,rows,cols)
            np.testing.assert_array_equal(area,[p['intersection_area_m2'] for p in scalar])
            np.testing.assert_array_equal(centers,[p['center_eligible'] for p in scalar])
            np.testing.assert_array_equal(partial,[p['partial_cell'] for p in scalar])

    def test_operator_is_immutable_and_checked_on_resume(self):
        """Commit static operator atomically, preserve task masks and reject later operator corruption."""
        _,_,sources,acquisitions,_ = synthetic_inputs()
        geometry = dict(epsg=5070,x=np.array([45.,135.]),y=np.array([45.]),
                        intersection_area_m2=np.full((1,2),8100.),center_eligible=np.ones((1,2),dtype=bool))
        template = store_template('nearest',geometry,acquisitions,sources)
        indices = np.array([[0,-1]],dtype='int64')
        options = dict(spatial_chunks=(2,2),compressors=None)
        with tempfile.TemporaryDirectory() as directory:
            with RunStore(directory,{'synthetic':True}) as run:
                initialize_nearest(run,template,indices,{'test':'operator'},{'shape':[1,1]},options)
                group = run.stores['nearest'][0]
                np.testing.assert_array_equal(group['operator/weight'][:],[[1.,0.]])
                result = apply_nearest(np.array([[0.]]),np.array([[0.]]),indices)
                run.write_task('nearest',0,'PMI',result,provenance={'synthetic':True})
            with RunStore(directory,{'synthetic':True},resume=True) as run:
                initialize_nearest(run,template,indices,{'test':'operator'},{'shape':[1,1]},options)
                np.testing.assert_array_equal(run.read_task('nearest',0,'PMI').soil_moisture.values,[[0.,np.nan]])
            group = zarr.open_group(Path(directory)/'nearest.zarr',mode='a')
            group['operator/source_index'][0,0]=-1
            with self.assertRaisesRegex(ValueError,'static checksum'):
                with RunStore(directory,{'synthetic':True},resume=True):
                    pass

    def test_original_crop_uses_native_rectangles_and_excludes_edge_touches(self):
        """Original-grid figure keeps exact native posting and positive-area intersection only."""
        class Group(dict):
            attrs = {'posting_m':[200.,-200.]}
        group = Group(x=np.array([0.,200.,400.]),y=np.array([200.,0.]))
        slices,polygons = crop_native(group,box(100,-100,500,100))
        self.assertEqual(slices,(slice(1,2),slice(1,3)))
        self.assertEqual([p.bounds for p in polygons],[(100.,-100.,300.,100.),(300.,-100.,500.,100.)])


if __name__=='__main__':
    unittest.main()
