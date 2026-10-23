"""Run all frozen R10 nearest tasks with copied retrieval sigma and immutable operator.

From repository root after native completion/inspection and pixi install --locked:
    pixi run python scripts/regrid_eastern_washington.py --stage nearest --resume --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding
After reviewing the exact PNG:
    pixi run python scripts/regrid_eastern_washington.py --stage nearest --inspect-nearest-figure SHA256 --output-dir outputs/eastern_washington/regridding

Reads the verified frozen originals/native Zarr. Writes nearest.zarr (90 m
EPSG:5070, float64 m³/m³, UTC acquisitions), operator/{source_index,weight},
task records and the selected six-panel PNG under the existing run manifest.
The operator indexes native EPSG:6933 row-major cells with exact actual posting.
Unit weights and task masks define W; covariance is W diag(sigma²) W.T under
R06's baseline. No dense covariance, extra simulations, clipping or fallback.
All code/input checks precede field execution; a single writer holds the lock.
Static geometry uses fixed 30 m edges; observed last-used PROJ operations do
not constitute a complete per-coordinate trace. Evaluation remains R23 onward.
Resource estimates assume no compression savings and include padded chunks;
macOS reclaimable RAM is free+inactive+speculative, not a peak-use guarantee.
"""

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from time import perf_counter

import numpy as np
import zarr

from nisar.quality import ALGORITHMS
from regridding.geometry import prepare_grid
from regridding.inputs import ROOT, audit_inputs, sha256_file
from regridding.nearest import build_target_geometry, apply_nearest
from regridding.run_plan import SELECTION_SHA256, SPATIAL_CHUNKS, storage_options
from regridding.schema import canonical_json, store_template
from regridding.storage import RunStore
from regridding.support import coverage
from regridding.washington import POLICY


def nearest_preflight(shape, native_shape, pending, output_dir, new_store):
    """Check conservative remaining disk and single-task memory before allocation."""
    padded = math.prod(math.ceil(n/c)*c for n,c in zip(shape,SPATIAL_CHUNKS))
    disk = padded*pending*18 + (padded*40 if new_store else 0) + 64*1024**2
    memory = math.prod(shape)*80 + math.prod(native_shape)*32 + 512*1024**2
    free_disk = shutil.disk_usage(output_dir).free
    output = subprocess.check_output(['vm_stat'],text=True)
    page_size = int(re.search(r'page size of (\d+) bytes',output).group(1))
    pages = {key:int(value) for key,value in re.findall(r'^(Pages [^:]+):\s+(\d+)\.',output,re.M)}
    free_memory = sum(pages[key] for key in ('Pages free','Pages inactive','Pages speculative'))*page_size
    result = dict(shape=list(shape),pending_tasks=pending,estimated_disk_bytes=disk,
                  estimated_peak_memory_bytes=memory,available_disk_bytes=free_disk,
                  available_reclaimable_memory_bytes=free_memory,
                  memory_availability_method='macOS vm_stat free + inactive + speculative pages',
                  estimate_limits='Padded uncompressed chunks; no compression savings; not a peak-use guarantee')
    print('Nearest resource preflight: '+json.dumps(result),flush=True)
    return result


def initialize_nearest(run, template, indices, stage_identity, native_layout, options):
    """Publish static fields and the complete operator together before registering tasks."""
    if 'nearest' not in run.manifest['stores']:
        if (run.root/'nearest.zarr').exists():
            raise FileExistsError('Unregistered nearest store exists; refusing overwrite')
        with tempfile.TemporaryDirectory(prefix='.nearest-operator-',dir=run.root) as temporary:
            with RunStore(Path(temporary)/'work',run.identity) as staging:
                staging.initialize('nearest',template,stage_identity=stage_identity,**options)
                group = staging.stores['nearest'][0].create_group('operator')
                group.attrs.update(native_layout=native_layout,native_store='../native.zarr',
                                   source_index_order='zero-based flattened native y,x; row-major; -1 absent',
                                   source_granule_lookup='native.zarr/source_index[acquisition,candidate,native_y,native_x]',
                                   effective_rows='Filter with each task moisture_valid or uncertainty_valid',
                                   covariance='W diag(native reported_uncertainty squared) W.T; R06 baseline',
                                   source_crs='EPSG:6933',target_crs='EPSG:5070')
                chunks = tuple(min(n,c) for n,c in zip(indices.shape,options['spatial_chunks']))
                for name,dtype,fill in (('source_index','int64',-1),('weight','float64',0.)):
                    array = group.create_array(name,shape=indices.shape,dtype=dtype,chunks=chunks,
                                               fill_value=fill,compressors=options['compressors'],
                                               dimension_names=('y','x'))
                    for r in range(0,indices.shape[0],chunks[0]):
                        for c in range(0,indices.shape[1],chunks[1]):
                            tile = (slice(r,r+chunks[0]),slice(c,c+chunks[1]))
                            values = indices[tile] if name=='source_index' else (indices[tile]>=0).astype('float64')
                            array[tile] = values
                            if not np.array_equal(array[tile],values):
                                raise ValueError('Operator readback mismatch')
                record = staging.manifest['stores']['nearest']
                record['static_files'] = staging._static_inventory('nearest')
                os.rename(staging.root/'nearest.zarr',run.root/'nearest.zarr')
                run.manifest['stores']['nearest'] = record
                run._save()
    run.initialize('nearest',template,stage_identity=stage_identity,**options)
    if not np.array_equal(run.stores['nearest'][0]['operator/source_index'][:],indices):
        raise ValueError('Recomputed nearest operator differs from frozen operator')


def run_nearest(args, plan):
    """Execute all 69×3 tasks, respecting native completion and stopping at R12 afterward."""
    started = perf_counter()
    root = Path(args.output_dir)
    identity = json.loads((root/'run_manifest.json').read_text())['identity']
    if identity['selection_sha256']!=SELECTION_SHA256:
        raise ValueError('Frozen selection identity changed')
    verification = identity.get('coordinate_verification',{})
    if verification.get('status')!='passed' or verification.get('policy')!=POLICY:
        raise ValueError('Verified pinned Washington native run required; regenerate in a new output directory')
    for key in ('environment','storage'):
        if identity[key]!=plan[key]:
            raise ValueError(f'Frozen {key} changed')
    with RunStore(root,identity,resume=args.resume) as run:
        if run.manifest.get('native_stage_status')!='complete':
            raise ValueError('Complete and inspect native reference before nearest')
        native_figure = run.manifest['figures']['native']
        if (native_figure['inspection_status']!='passed'
                or native_figure['sha256']!=sha256_file(root/native_figure['path'])):
            raise ValueError('Native figure integrity/inspection failed')
        for name,digest in run.manifest['stores']['native']['identity']['stage']['code_sha256'].items():
            if sha256_file(ROOT/name)!=digest:
                raise ValueError(f'Frozen native-stage code changed: {name}')
        execution = dict(command=plan['command'],status='running',requested_plan=plan)
        run.manifest.setdefault('executions',[]).append(execution)
        run._save()
        try:
            print('Auditing all frozen source files and completed native tasks.',flush=True)
            execution['audit'] = audit_inputs(args.selection,args.cache_dir,SELECTION_SHA256)
            selection = json.loads(Path(args.selection).read_text())
            grid = prepare_grid(tuple(selection['bounds_wgs84'][k] for k in ('west','south','east','north')),args.proj_cache_dir)
            # Runtime PROJ search-path repetition/user-cache locations are not
            # scientific identity. Keep every database/grid/code/operation check.
            actual, frozen = json.loads(canonical_json(grid['metadata'])), json.loads(canonical_json(identity['target_grid']))
            for record in (actual,frozen):
                environment = record['transform_availability']['environment']
                environment.pop('proj_data_dir')
                environment.pop('proj_user_data_dir')
            if actual!=frozen:
                raise ValueError('Frozen target geometry/transform identity changed')
            execution['geometry_metadata'] = grid['metadata']
            native = zarr.open_group(root/'native.zarr',mode='r',use_consolidated=False)
            if not np.all(native['completion_status'][:]==2):
                raise ValueError('Native tasks incomplete')
            status = (zarr.open_group(root/'nearest.zarr',mode='r',use_consolidated=False)['completion_status'][:]
                      if 'nearest' in run.manifest['stores'] else np.zeros(native['completion_status'].shape))
            resources = nearest_preflight(grid['metadata']['shape_rows_columns'],native['soil_moisture'].shape[-2:],
                                           int((status!=2).sum()),root,'nearest' not in run.manifest['stores'])
            execution['preflight'] = resources
            run._save()
            if (resources['estimated_disk_bytes']>resources['available_disk_bytes']
                    or resources['estimated_peak_memory_bytes']>resources['available_reclaimable_memory_bytes']):
                raise RuntimeError('Nearest resource estimate exceeds available disk or reclaimable RAM; no settings changed')
            geometry,indices,operations = build_target_geometry(grid,identity['native_layout'])
            template = store_template('nearest',geometry,identity['acquisitions'],identity['sources'])
            template['partial_cell'] = (('y','x'),geometry['partial_cell'].astype('uint8'))
            template['partial_cell'].encoding['_FillValue'] = None
            template.attrs['assumptions'].append('A10')
            template.attrs['nearest_rule'] = 'Inverse EPSG:6933 center; Euclidean native-center nearest; exact ties east/south; strict domain; no fallback'
            names = ('nearest.py','nearest_stage.py','geometry.py','washington.py','transforms.py','resources.py','inputs.py','schema.py','storage.py','support.py','quality.py','run_plan.py')
            code = {f'src/regridding/{name}':sha256_file(ROOT/'src/regridding'/name) for name in names}
            stage = dict(code_sha256=code,resolved=['R10','R32'],native_layout=identity['native_layout'],
                         tie_rule='floor from northwest outer corner; right-sided exact edge lookup',
                         geometry_batch_shape=[64,SPATIAL_CHUNKS[1]],observed_last_used_inverse_operations=operations)
            initialize_nearest(run,template,indices,stage,identity['native_layout'],storage_options())
            execution['stage_identity'] = stage
            run._save()
            for a,acquisition in enumerate(identity['acquisitions']):
                frame_start = perf_counter()
                for c,candidate in enumerate(ALGORITHMS):
                    if int(run.stores['nearest'][0]['completion_status'][a,c])==2:
                        continue
                    arrays = apply_nearest(native['soil_moisture'][a,c],native['reported_uncertainty'][a,c],indices)
                    counts = {name:coverage(arrays[name],geometry['intersection_area_m2'],geometry['center_eligible'])
                              for name in ('moisture_valid','uncertainty_valid')}
                    provenance = dict(command=plan['command'],acquisition_id=acquisition['id'],
                                      start_us=acquisition['start_us'],end_us=acquisition['end_us'],
                                      source_indices=acquisition['source_indices'],operator='nearest.zarr/operator',
                                      native_source_granule_lookup='native.zarr/source_index',
                                      masks=['moisture_valid','uncertainty_valid'],counts=counts,
                                      source_crs='EPSG:6933',target_crs='EPSG:5070',area_crs='EPSG:6933',
                                      units='m3 m-3',assumptions=template.attrs['assumptions'])
                    if not arrays['moisture_valid'].any():
                        provenance['all_missing_reason']='No finite selected native moisture within eligible target centers; no fallback'
                    run.write_task('nearest',a,candidate,arrays,provenance=provenance)
                    del arrays
                print(f'Nearest {a+1}/{len(identity["acquisitions"])} complete; {perf_counter()-frame_start:.1f} s',flush=True)
            from regridding.comparison_figures import export_comparison
            figure = export_comparison(run,'nearest',args.proj_cache_dir)
            run.manifest['nearest_stage_status']='complete' if figure['inspection_status']=='passed' else 'awaiting_figure_inspection'
            execution.update(status='arrays_and_figure_ready',elapsed_seconds=perf_counter()-started)
            run._save()
        except Exception as error:
            execution.update(status='failed',error_type=type(error).__name__,message=str(error),elapsed_seconds=perf_counter()-started)
            run._save()
            raise
