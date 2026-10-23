"""Materialize the frozen native reference, rank Odessa, and export its R35 PNG.

From repository root after ``pixi install --locked`` and both PROJ downloads:
    pixi run python scripts/regrid_eastern_washington.py --stage native --output-dir outputs/eastern_washington/regridding
Add --resume only for the same inputs/configuration and immutable stage code.
No credentials/network: original HDF5 files must exist under --cache-dir.

Native fields are nominal 200 m EPSG:6933 (exact HDF5 posting preserved), m³/m³,
original UTC acquisitions. The target
90 m EPSG:5070 lattice is described but no target fields are generated. Native
rectangular intersections are analytic. Odessa display polygons alone use the
approved fixed edge subdivisions and strict transforms. No new QC or sampling.
``run_native(args, plan)`` consumes the CLI namespace and describe_plan record.
Resource estimates are conservative engineering estimates, not measured peak RAM
or compressed size. Disk estimates include padded uncompressed chunks; memory
allows simultaneous input mosaics, normalization/readback and static geometry.
On macOS available RAM is free + inactive + speculative pages from vm_stat.
The manifest records estimates/actual resources, audits, timings, counts and
failures. A figure remains awaiting inspection until --inspect-native-figure
records an explicit review of that exact PNG checksum; no other stage runs here.
"""

from fractions import Fraction
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from time import perf_counter
from types import SimpleNamespace

import h5py
import numpy as np

from nisar.observations import GRID, grid_definition, native, window_layout
from nisar.quality import ALGORITHMS, cell_counts
from nisar.selection import group_paths, read_combined_frame
from regridding.geometry import prepare_grid
from regridding.inputs import ROOT, audit_inputs, sha256_file
from regridding.quality import input_masks
from regridding.run_plan import SELECTION_SHA256, SPATIAL_CHUNKS, storage_options
from regridding.schema import acquisition_records, store_template, task_fields
from regridding.storage import RunStore
from regridding.washington import verify_required_grid

ODESSA_BOUNDS = (-118.753749, 47.260609, -118.622651, 47.405850)


def native_geometry(layout):
    """Return exact positive-area rectangular intersections on the existing native grid."""
    x, y = layout['xx'][0], layout['yy'][:, 0]
    xmin, ymin, xmax, ymax = layout['square'].bounds
    dx, dy = layout['dx'], abs(layout['dy'])
    widths = np.maximum(0, np.minimum(x + dx/2, xmax) - np.maximum(x - dx/2, xmin))
    heights = np.maximum(0, np.minimum(y + dy/2, ymax) - np.maximum(y - dy/2, ymin))
    area = heights[:, None] * widths[None, :]
    if not np.array_equal(area > 0, layout['mask']):
        raise ValueError('Native intersection mask disagrees with existing ingestion')
    return dict(epsg=6933, x=x, y=y, dx_m=layout['dx'], dy_m=layout['dy'], intersection_area_m2=area,
                center_eligible=((y[:, None] >= ymin) & (y[:, None] <= ymax)
                                 & (x[None, :] >= xmin) & (x[None, :] <= xmax)),
                native_rows=np.arange(layout['r0'], layout['r1']),
                native_columns=np.arange(layout['c0'], layout['c1']))


def normalized_candidate(candidate):
    """Encode independent validity/source tuples without flag screening or zero filling."""
    moisture, sigma = candidate['soilMoisture'], candidate['soilMoistureUncertainty']
    masks = input_masks(moisture, sigma, moisture_fill_value=None, uncertainty_fill_value=None)
    flags = candidate['retrievalQualityFlag']
    fv = np.isfinite(flags) & (flags >= 0)
    if np.any(fv & ((flags != np.floor(flags)) | (flags >= 2**63))):
        raise ValueError('Candidate flags cannot be represented as int64')
    return dict(soil_moisture=np.where(masks['moisture_valid'], moisture, np.nan),
                reported_uncertainty=np.where(masks['uncertainty_valid'], sigma, np.nan),
                **masks, retrieval_quality_flag=np.where(fv, flags, -1).astype('int64'),
                flag_valid=fv, source_index=candidate['source_index'])


def choose_visual(ranking):
    """Rank exact coverage fractions; break ties by minimum coverage, UTC then filenames."""
    eligible = [r for r in ranking if all(r['recommended_counts'][a] > 0 for a in ALGORITHMS)]
    if not eligible:
        raise ValueError('No frozen acquisition has positive recommended Odessa coverage for all candidates')
    return min(eligible, key=lambda r: (
        -sum(Fraction(r['recommended_counts'][a], r['denominator']) for a in ALGORITHMS),
        -min(Fraction(r['recommended_counts'][a], r['denominator']) for a in ALGORITHMS),
        r['start_us'], r['source_filenames']))


def resource_preflight(shape, acquisitions, output_dir):
    """Stop before large work if estimated native-stage disk/RAM exceeds available resources."""
    cells = math.prod(shape)
    padded = math.prod(math.ceil(n/c)*c for n, c in zip(shape, SPATIAL_CHUNKS))
    bytes_per_cell = sum(np.dtype(dtype).itemsize for dtype, _ in task_fields('native').values())
    disk_estimate = padded * acquisitions * len(ALGORITHMS) * bytes_per_cell + padded * 64 + 64 * 1024**2
    memory_estimate = cells * 400 + 512 * 1024**2
    existing = Path(output_dir).resolve()
    while not existing.exists():
        existing = existing.parent
    free_disk = shutil.disk_usage(existing).free
    output = subprocess.check_output(['vm_stat'], text=True)
    page_size = int(re.search(r'page size of (\d+) bytes', output).group(1))
    pages = {key: int(value) for key, value in re.findall(r'^(Pages [^:]+):\s+(\d+)\.', output, re.M)}
    free_memory = sum(pages[key] for key in ('Pages free', 'Pages inactive', 'Pages speculative')) * page_size
    record = dict(native_shape=list(shape), padded_cells=padded, estimated_disk_bytes=disk_estimate,
                  estimated_peak_memory_bytes=memory_estimate, available_disk_bytes=free_disk,
                  available_reclaimable_memory_bytes=free_memory,
                  memory_availability_method='macOS vm_stat free + inactive + speculative pages',
                  estimate_limits='No compression savings assumed; estimates are not peak-memory guarantees.')
    print('Native resource preflight: ' + json.dumps(record), flush=True)
    if disk_estimate > free_disk or memory_estimate > free_memory:
        raise RuntimeError('Native resource estimate exceeds available disk or reclaimable RAM; no settings changed')
    return record


def prepare_native(selection, cache_dir, projection_cache):
    """Read frozen metadata/groups and strict geometry without reading moisture fields."""
    paths = [Path(cache_dir) / name for name in selection['selected_granule_names']]
    target = prepare_grid(tuple(selection['bounds_wgs84'][k] for k in ('west', 'south', 'east', 'north')),
                          projection_cache)
    display = prepare_grid(ODESSA_BOUNDS, projection_cache)
    with h5py.File(paths[0]) as product:
        layout = window_layout(SimpleNamespace(projected=target['domain_6933'],
                                               bounds=target['metadata']['bounds_wgs84']), product)
        crop = window_layout(SimpleNamespace(projected=display['domain_6933'], bounds=ODESSA_BOUNDS), product)
    slices = (slice(crop['r0']-layout['r0'], crop['r1']-layout['r0']),
              slice(crop['c0']-layout['c0'], crop['c1']-layout['c0']))
    if any(s.start < 0 or s.stop > n for s, n in zip(slices, layout['mask'].shape)):
        raise ValueError('Odessa crop is outside native study support')
    indices = {path.name: i for i, path in enumerate(paths)}
    sources = []
    keys = ('zeroDopplerStartTime', 'zeroDopplerEndTime', 'trackNumber', 'frameNumber',
            'absoluteOrbitNumber', 'orbitPassDirection', 'productVersion',
            'productSpecificationVersion', 'compositeReleaseId')
    for path in paths:
        with h5py.File(path) as product:
            identity = product['science/LSAR/identification']
            metadata = {k: native(identity[k][()]) for k in keys}
            dx, dy, xo, yo, _, _ = grid_definition(product[GRID])
            if not np.allclose([dx, dy, xo, yo], [layout[k] for k in ('dx', 'dy', 'xo', 'yo')], rtol=0, atol=1e-5):
                raise ValueError(f'Grid mismatch in {path.name}')
        sources.append(dict(filename=path.name, checksum=selection['provenance']['source_checksums'][path.name],
                            start_utc=metadata['zeroDopplerStartTime'], end_utc=metadata['zeroDopplerEndTime'],
                            metadata=metadata))
    groups = group_paths(paths, selection['nisar_combine_window_minutes'])
    layout_identity = {k: layout[k] for k in ('r0', 'r1', 'c0', 'c1', 'dx', 'dy', 'xo', 'yo')}
    layout_identity.update(epsg=6933, order='row-major', domain_bounds=list(layout['square'].bounds))
    acquisitions = acquisition_records([[indices[p.name] for p in group] for group in groups], sources, layout_identity)
    if len(acquisitions) != selection['selected_frames']:
        raise ValueError('Frozen acquisition group count changed')
    return layout, crop, slices, target, display, sources, acquisitions, layout_identity


def run_native(args, plan):
    """Audit, preflight, persist each native task and freeze/export the ranked Odessa frame."""
    started = perf_counter()
    print('Verifying every frozen original; no download or refresh.', flush=True)
    audit = audit_inputs(args.selection, args.cache_dir, SELECTION_SHA256)
    selection = json.loads(Path(args.selection).read_text())
    layout, crop, slices, target, display, sources, acquisitions, layout_identity = prepare_native(
        selection, args.cache_dir, args.proj_cache_dir)
    resources = resource_preflight(layout['mask'].shape, len(acquisitions), args.output_dir)
    verification = verify_required_grid(target)
    template = store_template('native', native_geometry(layout), acquisitions, sources)
    # Exclude future method modules/CLI/figure presentation code from immutable native-array fingerprints.
    relevant = ('inputs.py', 'native_stage.py', 'geometry.py', 'transforms.py', 'resources.py', 'washington.py',
                'quality.py', 'schema.py', 'storage.py')
    code = {name: sha256_file(ROOT / name) for name in plan['code_sha256']
            if name.startswith(('src/nisar/', 'src/ingest/'))
            or (name.startswith('src/regridding/') and Path(name).name in relevant)}
    identity = dict(selection_sha256=SELECTION_SHA256, sources=sources, acquisitions=acquisitions,
                    native_layout=layout_identity, target_grid=target['metadata'],
                    environment=plan['environment'], storage=plan['storage'],
                    coordinate_verification=verification,
                    assumptions=template.attrs['assumptions'],
                    resolved=['R01','R02','R03','R04','R05','R06','R07','R08','R09','R32','R33','R34','R35'])
    source_indices = {s['filename']: i for i, s in enumerate(sources)}
    with RunStore(args.output_dir, identity, resume=args.resume) as run:
        run.initialize('native', template, stage_identity={'code_sha256': code}, **storage_options())
        execution = dict(command=plan['command'], audit=audit, preflight=resources,
                         requested_plan=plan, status='running')
        run.manifest.setdefault('executions', []).append(execution)
        run._save()
        try:
            ranking = []
            for a, acquisition in enumerate(acquisitions):
                frame_start = perf_counter()
                filenames = [sources[i]['filename'] for i in acquisition['source_indices']]
                frame = read_combined_frame([Path(args.cache_dir)/name for name in filenames], layout,
                                            source_indices=source_indices)
                recommended = {}
                for c, candidate in enumerate(ALGORITHMS):
                    data = frame['data'][candidate]
                    recommended[candidate] = cell_counts(data['soilMoisture'][slices],
                                                         data['retrievalQualityFlag'][slices],
                                                         crop['mask'])['recommended_cells']
                    if int(run.stores['native'][0]['completion_status'][a, c]) == 2:
                        continue
                    arrays = normalized_candidate(data)
                    area = template.domain_intersection_area.values
                    counts = {field: dict(valid=int(arrays[field].sum()),
                                          missing=int(layout['mask'].sum()-arrays[field].sum()),
                                          valid_area_m2=float(area[arrays[field]].sum()))
                              for field in ('moisture_valid', 'uncertainty_valid')}
                    provenance = dict(command=plan['command'], acquisition_id=acquisition['id'],
                                      source_indices=acquisition['source_indices'], counts=counts,
                                      denominator_cells=int(layout['mask'].sum()),
                                      domain_area_m2=float(area.sum()), area_crs='EPSG:6933',
                                      units='m3 m-3', start_us=acquisition['start_us'], end_us=acquisition['end_us'])
                    if not arrays['moisture_valid'].any():
                        provenance['all_missing_reason'] = 'No finite non-fill native moisture in frozen domain/source group'
                    run.write_task('native', a, candidate, arrays, provenance=provenance)
                    del arrays
                denominator = int(crop['mask'].sum())
                ranking.append(dict(acquisition=a, acquisition_id=acquisition['id'],
                                    recommended_counts=recommended, denominator=denominator,
                                    score=sum(recommended.values())/(3*denominator),
                                    recommended_percent={k: 100*v/denominator for k,v in recommended.items()},
                                    start_us=acquisition['start_us'], end_us=acquisition['end_us'],
                                    source_filenames=filenames, metadata=frame['metadata']))
                del frame
                print(f'Native {a+1}/{len(acquisitions)} complete; {perf_counter()-frame_start:.1f} s', flush=True)
            selected = choose_visual(ranking)
            frozen = run.manifest.get('visual_selection')
            if frozen is not None and frozen != selected:
                raise ValueError('Frozen visual selection changed')
            run.manifest['visual_ranking'] = ranking
            run.manifest['visual_selection'] = selected
            run.manifest['display_geometry'] = display['metadata']
            run._save()
            from regridding.figures import export_native
            export_native(run, template, crop, slices, display, selected)
            execution.update(status='arrays_and_figure_ready', elapsed_seconds=perf_counter()-started)
            inspected = run.manifest['figures']['native']['inspection_status'] == 'passed'
            run.manifest['native_stage_status'] = 'complete' if inspected else 'awaiting_figure_inspection'
            run._save()
            print(json.dumps(dict(selected_visual=selected, native_stage_status=run.manifest['native_stage_status'],
                                  figure=str(Path(args.output_dir)/'figures/odessa_native.png')), indent=2), flush=True)
        except Exception as error:
            execution.update(status='failed', error_type=type(error).__name__, message=str(error),
                             elapsed_seconds=perf_counter()-started)
            run._save()
            raise
