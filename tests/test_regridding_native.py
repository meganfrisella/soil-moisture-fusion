"""Synthetic native-stage, exact ranking, missing-panel and R35 publication checks."""

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from nisar.quality import ALGORITHMS
from regridding.native_stage import native_geometry, normalized_candidate, choose_visual, resource_preflight, run_native
from regridding.schema import acquisition_records, store_template
from regridding.figures import record_inspection


class IdentityTransform:
    """Use metre-preserving synthetic display geometry without external datum grids."""
    def transform(self, x, y, **kwargs):
        """Return the test coordinates unchanged."""
        return x, y


def synthetic_inputs():
    """Create two equal-coverage source groups and a six-cell native grid."""
    xx, yy = np.meshgrid([0.,200.,400.],[200.,0.])
    domain = box(-100,-100,500,300)
    layout = dict(xx=xx, yy=yy, dx=200., dy=-200., mask=np.ones((2,3),dtype=bool),
                  square=domain, r0=0,r1=2,c0=0,c1=3,
                  bounds_wgs84=(-118.753749,47.260609,-118.622651,47.405850))
    metadata = dict(zeroDopplerStartTime='2026-06-17T02:53:18Z', zeroDopplerEndTime='2026-06-17T02:53:30Z',
                    trackNumber=1,frameNumber=2,absoluteOrbitNumber=3,orbitPassDirection='Ascending',
                    productVersion='1.0.11',productSpecificationVersion='1.0.2',compositeReleaseId='P05023')
    sources = [dict(filename=f'source-{i}.h5',checksum={'algorithm':'SHA256','value':str(i)*64},
                    start_utc=metadata['zeroDopplerStartTime'],end_utc=metadata['zeroDopplerEndTime'],
                    metadata=metadata) for i in range(2)]
    acquisitions = acquisition_records([[0],[1]], sources, {'synthetic':True})
    display = dict(domain_6933=domain, domain_5070=domain, forward=IdentityTransform(),metadata={'synthetic':True})
    data = {a:dict(soilMoisture=np.array([[0.,-.2,.8],[np.nan,.3,.4]]),
                   soilMoistureUncertainty=np.full((2,3), np.nan if a=='TSR' else 0.),
                   retrievalQualityFlag=np.array([[0.,0.,1.],[1.,2.,0.]]),
                   source_index=np.zeros((2,3), dtype='int32')) for a in ALGORITHMS}
    return layout,display,sources,acquisitions,dict(metadata=metadata,data=data)


class NativeStageTests(unittest.TestCase):
    def test_exact_hdf5_posting_is_preserved_at_large_projected_coordinates(self):
        """Nominal 200 m products retain actual noninteger posting in centers, bounds and affine."""
        layout,_,sources,acquisitions,_ = synthetic_inputs()
        dx,dy=200.17900466918945,-200.17900467012078
        x=-11500000.+np.arange(3)*dx
        y=5300000.+np.arange(2)*dy
        layout.update(dx=dx,dy=dy,xx=np.meshgrid(x,y)[0],yy=np.meshgrid(x,y)[1],
                      square=box(x[0]-dx/2,y[-1]+dy/2,x[-1]+dx/2,y[0]-dy/2))
        ds=store_template('native',native_geometry(layout),acquisitions,sources)
        self.assertEqual(ds.attrs['posting_m'],[dx,dy])
        self.assertEqual(ds.attrs['affine'][0],dx)
        np.testing.assert_array_equal(ds.x.values,x)
        np.testing.assert_allclose(ds.x_bounds.values[:,1]-ds.x_bounds.values[:,0],dx,rtol=0,atol=1e-8)

    def test_exact_native_partial_areas_and_center_eligibility(self):
        """Retain positive-area partial cells even when their centers lie outside."""
        layout,*_ = synthetic_inputs()
        layout['square'] = box(50,-50,500,300)
        grid = native_geometry(layout)
        self.assertEqual(grid['intersection_area_m2'][0,0], 50*200)
        self.assertFalse(grid['center_eligible'][0,0])
        self.assertTrue((grid['intersection_area_m2']>0).all())

    def test_native_normalization_independent_sigma_and_flag_rejection(self):
        """Preserve zero/negative moisture and uncertainty-only tuples; reject fractional flags."""
        *_,frame = synthetic_inputs()
        data = frame['data']['PMI']
        result = normalized_candidate(data)
        self.assertTrue(result['uncertainty_valid'][1,0])
        self.assertFalse(result['moisture_valid'][1,0])
        self.assertEqual(result['soil_moisture'][0,1], -.2)
        self.assertTrue(result['reported_zero'].all())
        data['retrievalQualityFlag'][0,0] = .5
        with self.assertRaisesRegex(ValueError,'int64'):
            normalized_candidate(data)

    def test_rank_ties_and_missing_candidate_without_using_uncertainty(self):
        """Apply mean, minimum, time and filename tie-breaks; every candidate needs coverage."""
        def record(counts,start,name):
            return dict(recommended_counts=dict(zip(ALGORITHMS,counts)),denominator=10,start_us=start,source_filenames=[name])
        records=[record((1,5,9),0,'a'),record((5,5,5),1,'b'),record((5,5,5),1,'a'),record((0,10,10),0,'c')]
        self.assertEqual(choose_visual(records),records[2])
        with self.assertRaisesRegex(ValueError,'No frozen acquisition'):
            choose_visual([records[3]])

    def test_preflight_stops_on_insufficient_disk(self):
        """Resource infeasibility stops without creating outputs or altering configuration."""
        vm='Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 1000000.\nPages inactive: 1000000.\nPages speculative: 1000000.\n'
        with tempfile.TemporaryDirectory() as directory, patch('regridding.native_stage.subprocess.check_output', return_value=vm), \
                patch('regridding.native_stage.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            output=Path(directory)/'new'
            with self.assertRaisesRegex(RuntimeError,'resource estimate'):
                resource_preflight((2,3),2,output)
            self.assertFalse(output.exists())

    def test_native_pipeline_resume_figure_metadata_and_inspection_digest(self):
        """Write six synthetic tasks, show missing uncertainty, resume immutably and gate review."""
        layout,display,sources,acquisitions,frame=synthetic_inputs()
        prepared=(layout,layout,(slice(None),slice(None)),display,display,sources,acquisitions,{'synthetic':True})
        def read(paths, *args, **kwargs):
            result=copy.deepcopy(frame)
            source=kwargs['source_indices'][paths[0].name]
            for value in result['data'].values():
                value['source_index'][:]=source
            return result
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            selection=root/'selection.json'
            selection.write_text('{}')
            args=SimpleNamespace(selection=selection,cache_dir=root,proj_cache_dir=root,output_dir=root/'output',resume=False)
            plan=dict(command=['synthetic fixture'],code_sha256={},environment={'synthetic':True},storage={'synthetic':True})
            with patch('regridding.native_stage.audit_inputs',return_value={'synthetic':True}), \
                    patch('regridding.native_stage.prepare_native',return_value=prepared), \
                    patch('regridding.native_stage.resource_preflight',return_value={'synthetic':True}), \
                    patch('regridding.native_stage.verify_required_grid',return_value={'status':'passed','synthetic':True}), \
                    patch('regridding.native_stage.read_combined_frame',side_effect=read):
                run_native(args,plan)
                manifest=json.loads((args.output_dir/'run_manifest.json').read_text())
                figure=manifest['figures']['native']
                self.assertEqual(figure['panel_counts']['TSR/reported_uncertainty']['valid'],0)
                self.assertEqual(figure['panel_counts']['PMI/soil_moisture']['below_scale'],1)
                self.assertEqual(figure['panel_counts']['PMI/soil_moisture']['above_scale'],1)
                self.assertEqual(figure['dimensions_px'][0],3200)
                with Image.open(args.output_dir/figure['path']) as png:
                    self.assertEqual(png.size,tuple(figure['dimensions_px']))
                    self.assertIn('conditional σ proxy',png.info['Description'])
                    self.assertEqual(json.loads(png.info['Provenance'])['source_crs'],'EPSG:6933')
                tasks=manifest['stores']['native']['tasks']
                args.resume=True
                run_native(args,plan)
                resumed=json.loads((args.output_dir/'run_manifest.json').read_text())
                self.assertEqual(resumed['stores']['native']['tasks'],tasks)
                self.assertEqual(resumed['figures']['native']['sha256'],figure['sha256'])
                with self.assertRaisesRegex(ValueError,'digest'):
                    record_inspection(args.output_dir,'incorrect')
                record_inspection(args.output_dir,figure['sha256'])
                reviewed=json.loads((args.output_dir/'run_manifest.json').read_text())
                self.assertEqual(reviewed['native_stage_status'],'complete')


if __name__=='__main__':
    unittest.main()
