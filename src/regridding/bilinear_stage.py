"""Run the approved R12 strict bilinear stage on the frozen Washington dataset.

From repository root after native/nearest completion and pixi install --locked:
    pixi run python scripts/regrid_eastern_washington.py --stage bilinear --resume --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_washington
After reviewing the exact figure:
    pixi run python scripts/regrid_eastern_washington.py --stage bilinear --inspect-bilinear-figure SHA256 --output-dir outputs/eastern_washington/regridding_washington

Reads checksummed native/nearest stores and audits all frozen originals. Retains
their historical code identities as input provenance, without recomputing those
completed stages. Revalidates pinned geometry/environment, checks disk/RAM,
then writes one acquisition/candidate at a time to bilinear.zarr and records
its four-slot immutable operator. Fields are float64 m³/m³ on 90 m EPSG:5070,
source centers are actual nominal-200 m EPSG:6933, times are UTC. R06 retrieval
sigma excludes bias and representation/model error. A01–A09/A12 apply.

``bilinear_preflight(shape, native_shape, pending, output_dir, new_store)``
returns/prints a resource estimate, not a peak guarantee: uncompressed padded
fields (18 bytes/cell/task), static arrays/operator (75 bytes/padded cell),
64 MiB metadata allowance; RAM covers resident operator, fields/validation,
native arrays and 768 MiB working space. macOS available RAM is vm_stat free,
inactive and speculative pages. The runner stops if either resource fails.
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
from regridding.bilinear import RULE, bilinear_operator, apply_bilinear
from regridding.geometry import prepare_grid
from regridding.inputs import ROOT, audit_inputs, sha256_file
from regridding.run_plan import SELECTION_SHA256, SPATIAL_CHUNKS, storage_options
from regridding.schema import canonical_json, store_template
from regridding.storage import RunStore
from regridding.support import coverage
from regridding.washington import POLICY


def bilinear_preflight(shape, native_shape, pending, output_dir, new_store):
    """Estimate all pending slabs plus padded static/operator storage before allocating them."""
    padded = math.prod(math.ceil(n / c) * c for n, c in zip(shape, SPATIAL_CHUNKS))
    disk = padded * (pending * 18 + (75 if new_store else 0)) + 64 * 1024**2
    memory = math.prod(shape) * 144 + math.prod(native_shape) * 32 + 768 * 1024**2
    output = subprocess.check_output(['vm_stat'], text=True)
    page_size = int(re.search(r'page size of (\d+) bytes', output).group(1))
    pages = {key: int(value) for key, value in re.findall(r'^(Pages [^:]+):\s+(\d+)\.', output, re.M)}
    free_memory = sum(pages[k] for k in ('Pages free', 'Pages inactive', 'Pages speculative')) * page_size
    result = dict(shape=list(shape), pending_tasks=pending, estimated_disk_bytes=disk,
                  estimated_peak_memory_bytes=memory, available_disk_bytes=shutil.disk_usage(output_dir).free,
                  available_reclaimable_memory_bytes=free_memory,
                  memory_availability_method='macOS vm_stat free + inactive + speculative pages',
                  estimate_limits='Padded uncompressed chunks; no compression savings; not a peak-use guarantee')
    print('Bilinear resource preflight: ' + json.dumps(result), flush=True)
    return result


def build_bilinear_operator(grid, native_x, native_y, geometry):
    """Map target centers in 64×1024 batches using the fixed Washington inverse."""
    height, width = geometry['center_eligible'].shape
    indices = np.full((height, width, 4), -1, dtype='int64')
    weights = np.zeros((height, width, 4), dtype='float64')
    for r in range(0, height, 64):
        rr = slice(r, min(r + 64, height))
        for c in range(0, width, SPATIAL_CHUNKS[1]):
            cc = slice(c, min(c + SPATIAL_CHUNKS[1], width))
            xx, yy = np.meshgrid(geometry['x'][cc], geometry['y'][rr])
            x, y = grid['inverse'].transform(xx, yy, errcheck=True)
            indices[rr, cc], weights[rr, cc] = bilinear_operator(
                x, y, native_x, native_y, geometry['center_eligible'][rr, cc])
        if r % 512 == 0 or rr.stop == height:
            print(f'Bilinear operator: {rr.stop}/{height} rows', flush=True)
    return indices, weights


def initialize_bilinear(run, template, indices, weights, stage, native_layout, options):
    """Publish verified static geometry and operator before registering any field task."""
    if 'bilinear' not in run.manifest['stores']:
        if (run.root / 'bilinear.zarr').exists():
            raise FileExistsError('Unregistered bilinear store exists; refusing overwrite')
        with tempfile.TemporaryDirectory(prefix='.bilinear-operator-', dir=run.root) as temporary:
            with RunStore(Path(temporary) / 'work', run.identity) as staging:
                staging.initialize('bilinear', template, stage_identity=stage, **options)
                operator = staging.stores['bilinear'][0].create_group('operator')
                operator.attrs.update(native_layout=native_layout, native_store='../native.zarr',
                                      source_index_order='zero-based flattened native y,x; row-major; -1 absent',
                                      neighbor_order=['NW', 'NE', 'SW', 'SE'], validity_rule=RULE,
                                      source_granule_lookup='native.zarr/source_index[acquisition,candidate,native_y,native_x]',
                                      effective_rows='Filter with task moisture_valid or uncertainty_valid',
                                      covariance='W diag(native reported_uncertainty squared) W.T; R06 baseline',
                                      source_crs='EPSG:6933', target_crs='EPSG:5070')
                chunks = (*[min(n, c) for n, c in zip(indices.shape[:2], options['spatial_chunks'])], 4)
                for name, values, dtype, fill in (('source_index', indices, 'int64', -1),
                                                  ('weight', weights, 'float64', 0.)):
                    array = operator.create_array(name, shape=values.shape, dtype=dtype, chunks=chunks,
                                                  fill_value=fill, compressors=options['compressors'],
                                                  dimension_names=('y', 'x', 'neighbor'))
                    for r in range(0, values.shape[0], chunks[0]):
                        for c in range(0, values.shape[1], chunks[1]):
                            tile = (slice(r, r + chunks[0]), slice(c, c + chunks[1]), slice(None))
                            array[tile] = values[tile]
                            if not np.array_equal(array[tile], values[tile]):
                                raise ValueError('Bilinear operator readback mismatch')
                record = staging.manifest['stores']['bilinear']
                record['static_files'] = staging._static_inventory('bilinear')
                os.rename(staging.root / 'bilinear.zarr', run.root / 'bilinear.zarr')
                run.manifest['stores']['bilinear'] = record
                run._save()
    run.initialize('bilinear', template, stage_identity=stage, **options)
    group = run.stores['bilinear'][0]['operator']
    for r in range(0, indices.shape[0], SPATIAL_CHUNKS[0]):
        tile = slice(r, r + SPATIAL_CHUNKS[0])
        for name, values in (('source_index', indices), ('weight', weights)):
            if not np.array_equal(group[name][tile], values[tile]):
                raise ValueError('Recomputed bilinear operator differs from stored operator')


def run_bilinear(args, plan):
    """Run all frozen tasks after nearest completion; finish only after exact PNG inspection."""
    started = perf_counter()
    root = Path(args.output_dir)
    identity = json.loads((root / 'run_manifest.json').read_text())['identity']
    verification = identity.get('coordinate_verification', {})
    if identity['selection_sha256'] != SELECTION_SHA256:
        raise ValueError('Frozen selection identity changed')
    if verification.get('status') != 'passed' or verification.get('policy') != POLICY:
        raise ValueError('Verified fixed Washington operation required')
    for key in ('environment', 'storage'):
        if identity[key] != plan[key]:
            raise ValueError(f'Frozen {key} changed')
    with RunStore(root, identity, resume=args.resume) as run:
        for method in ('native', 'nearest'):
            if run.manifest.get(f'{method}_stage_status') != 'complete':
                raise ValueError('Complete and inspect native and nearest before bilinear')
            figure = run.manifest['figures'][method]
            if figure['inspection_status'] != 'passed' or sha256_file(root / figure['path']) != figure['sha256']:
                raise ValueError('Prior figure integrity/inspection failed')
        execution = dict(command=plan['command'], status='running', requested_plan=plan)
        run.manifest.setdefault('executions', []).append(execution)
        run._save()
        try:
            print('Auditing frozen originals; completed input stores verified.', flush=True)
            execution['audit'] = audit_inputs(args.selection, args.cache_dir, SELECTION_SHA256)
            selection = json.loads(Path(args.selection).read_text())
            grid = prepare_grid(tuple(selection['bounds_wgs84'][k] for k in ('west', 'south', 'east', 'north')),
                                args.proj_cache_dir)
            actual, frozen = [json.loads(canonical_json(value)) for value in (grid['metadata'], identity['target_grid'])]
            for record in (actual, frozen):
                environment = record['transform_availability']['environment']
                environment.pop('proj_data_dir')
                environment.pop('proj_user_data_dir')
            if actual != frozen:
                raise ValueError('Frozen target geometry/transform identity changed')
            native = zarr.open_group(root / 'native.zarr', mode='r', use_consolidated=False)
            nearest = zarr.open_group(root / 'nearest.zarr', mode='r', use_consolidated=False)
            if not all(np.all(g['completion_status'][:] == 2) for g in (native, nearest)):
                raise ValueError('Incomplete input tasks')
            exists = 'bilinear' in run.manifest['stores']
            status = (zarr.open_group(root / 'bilinear.zarr', mode='r', use_consolidated=False)['completion_status'][:]
                      if exists else np.zeros(native['completion_status'].shape))
            resource = bilinear_preflight(grid['metadata']['shape_rows_columns'], native['soil_moisture'].shape[-2:],
                                          int((status != 2).sum()), root, not exists)
            execution['preflight'] = resource
            run._save()
            if (resource['estimated_disk_bytes'] > resource['available_disk_bytes']
                    or resource['estimated_peak_memory_bytes'] > resource['available_reclaimable_memory_bytes']):
                raise RuntimeError('Bilinear resource estimate exceeds available disk or reclaimable RAM; no settings changed')
            geometry = dict(epsg=5070, x=nearest['x'][:], y=nearest['y'][:],
                            intersection_area_m2=nearest['domain_intersection_area'][:],
                            center_eligible=nearest['center_eligible'][:].astype(bool), partial_cell=nearest['partial_cell'][:])
            template = store_template('bilinear', geometry, identity['acquisitions'], identity['sources'])
            template['partial_cell'] = (('y', 'x'), geometry['partial_cell'])
            template['partial_cell'].encoding['_FillValue'] = None
            template.attrs['assumptions'].append('A12')
            template.attrs['bilinear_rule'] = RULE
            names = ('bilinear.py', 'bilinear_stage.py', 'geometry.py', 'washington.py', 'transforms.py',
                     'resources.py', 'inputs.py', 'schema.py', 'storage.py', 'support.py', 'quality.py', 'run_plan.py')
            stage = dict(code_sha256={f'src/regridding/{n}': sha256_file(ROOT / 'src/regridding' / n) for n in names},
                         resolved=['R12', 'R32'], validity_rule=RULE, geometry_batch_shape=[64, 1024],
                         input_store_identities={m: run.manifest['stores'][m]['identity'] for m in ('native', 'nearest')},
                         native_layout=identity['native_layout'], geometry_source='nearest.zarr immutable static arrays')
            indices, weights = build_bilinear_operator(grid, native['x'][:], native['y'][:], geometry)
            initialize_bilinear(run, template, indices, weights, stage, identity['native_layout'], storage_options())
            execution['stage_identity'] = stage
            execution['geometrically_available_cells'] = int(np.count_nonzero(indices[:, :, 0] >= 0))
            run._save()
            for a, acquisition in enumerate(identity['acquisitions']):
                frame_start = perf_counter()
                for c, candidate in enumerate(ALGORITHMS):
                    if int(run.stores['bilinear'][0]['completion_status'][a, c]) == 2:
                        continue
                    arrays = apply_bilinear(native['soil_moisture'][a, c], native['reported_uncertainty'][a, c], indices, weights)
                    counts = {name: coverage(arrays[name], geometry['intersection_area_m2'], geometry['center_eligible'])
                              for name in ('moisture_valid', 'uncertainty_valid')}
                    provenance = dict(command=plan['command'], acquisition_id=acquisition['id'],
                                      start_us=acquisition['start_us'], end_us=acquisition['end_us'],
                                      source_indices=acquisition['source_indices'], operator='bilinear.zarr/operator',
                                      native_source_granule_lookup='native.zarr/source_index', validity_rule=RULE,
                                      masks=['moisture_valid', 'uncertainty_valid'], counts=counts,
                                      source_crs='EPSG:6933', target_crs='EPSG:5070', area_crs='EPSG:6933',
                                      units='m3 m-3', assumptions=template.attrs['assumptions'])
                    if not arrays['moisture_valid'].any():
                        provenance['all_missing_reason'] = 'No eligible stencil with all four finite native moisture values; no fallback'
                    run.write_task('bilinear', a, candidate, arrays, provenance=provenance)
                    del arrays
                print(f'Bilinear {a + 1}/{len(identity["acquisitions"])} complete; {perf_counter() - frame_start:.1f} s', flush=True)
            from regridding.comparison_figures import export_comparison
            figure = export_comparison(run, 'bilinear', args.proj_cache_dir)
            run.manifest['bilinear_stage_status'] = 'complete' if figure['inspection_status'] == 'passed' else 'awaiting_figure_inspection'
            execution.update(status='arrays_and_figure_ready', elapsed_seconds=perf_counter() - started)
            run._save()
        except Exception as error:
            execution.update(status='failed', error_type=type(error).__name__, message=str(error), elapsed_seconds=perf_counter() - started)
            run._save()
            raise
