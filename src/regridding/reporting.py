"""Print verified stage completion and support counts without creating new artifacts.

From repository root after a stage run and pixi install --locked:
    pixi run python scripts/regrid_eastern_washington.py --stage nearest --summary --output-dir outputs/eastern_washington/regridding_washington

``stage_summary(output_dir, method)`` verifies registered store/task checksums
under the existing single-writer lock, then reports stored completion, spatial
support, per-candidate valid cell-acquisition counts and PNG identities. Counts
are derived from committed records; they are not accuracy or calibration metrics.
Areas use EPSG:6933 m², source/target coordinates are metres, fields m³/m³,
timestamps UTC. No field changes, downloads, extra tables or exports.
"""

import json
from pathlib import Path

import numpy as np
import zarr

from nisar.quality import ALGORITHMS
from regridding.inputs import sha256_file
from regridding.storage import RunStore


def stage_summary(output_dir, method):
    """Return a JSON-compatible summary exclusively from verified stored outputs."""
    root = Path(output_dir)
    identity = json.loads((root/'run_manifest.json').read_text())['identity']
    with RunStore(root,identity,resume=True) as run:
        if method not in run.manifest['stores']:
            raise ValueError('Requested stage has not been initialized')
        group = zarr.open_group(root/f'{method}.zarr',mode='r',use_consolidated=False)
        status = group['completion_status'][:]
        area = group['domain_intersection_area'][:]
        centers = group['center_eligible'][:].astype(bool)
        candidate_counts = {}
        for c,candidate in enumerate(ALGORITHMS):
            totals = dict(completed_acquisitions=0,valid_moisture_cell_acquisition_pairs=0,
                          valid_uncertainty_cell_acquisition_pairs=0,all_missing_acquisitions=0)
            for a in range(status.shape[0]):
                if status[a,c]!=2:
                    continue
                record = json.loads((root/'records'/method/f'{a}-{c}.json').read_text())
                totals['completed_acquisitions'] += 1
                totals['valid_moisture_cell_acquisition_pairs'] += record['counts']['moisture_valid']
                totals['valid_uncertainty_cell_acquisition_pairs'] += record['counts']['uncertainty_valid']
                totals['all_missing_acquisitions'] += int(record['counts']['moisture_valid']==0)
            candidate_counts[candidate] = totals
        figures = {}
        for name,figure in run.manifest.get('figures',{}).items():
            if figure['sha256']!=sha256_file(root/figure['path']):
                raise ValueError('Recorded figure checksum mismatch')
            figures[name] = {k:figure[k] for k in ('path','sha256','dimensions_px','inspection_status','panel_counts')}
        def utc(value):
            return str(np.datetime64(int(value),'us'))+'Z'
        return dict(method=method,stage_status=run.manifest.get(f'{method}_stage_status'),
                    completed_tasks=int((status==2).sum()),unfinished_tasks=int((status!=2).sum()),
                    shape=list(area.shape),intersecting_cells=int((area>0).sum()),
                    center_eligible_cells=int(centers.sum()),center_excluded_intersecting_cells=int(((area>0)&~centers).sum()),
                    domain_intersection_area_m2=float(area.sum()),area_crs='EPSG:6933',
                    source_crs='EPSG:6933',field_crs=group.attrs['crs'],posting_m=group.attrs['posting_m'],
                    source_posting_m=[identity['native_layout'][k] for k in ('dx','dy')],
                    field_units='m3 m-3',acquisition_count=status.shape[0],
                    start_utc=utc(min(a['start_us'] for a in identity['acquisitions'])),
                    end_utc=utc(max(a['end_us'] for a in identity['acquisitions'])),
                    candidate_counts=candidate_counts,assumptions=group.attrs['assumptions'],
                    uncertainty_scope=group.attrs['uncertainty_scope'],figures=figures,
                    coordinate_verification=identity.get('coordinate_verification'),
                    selection_sha256=identity['selection_sha256'],report_code_sha256=sha256_file(Path(__file__)))
