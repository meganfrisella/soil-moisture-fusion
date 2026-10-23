"""Verify the frozen SME2 source selection and combined native observations.

Source moisture and reported uncertainty are m³/m³ on the native EPSG:6933
grid; acquisition bounds are UTC. This workflow reads cached originals and
writes file-level and combined-acquisition QA records without filtering values.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import h5py

from nisar.catalog import verify_file
from nisar.geometry import BoundingBox
from nisar.observations import window_layout
from nisar.qa.reports import QASession, file_digest
from nisar.selection import group_paths, read_combined_frame
from regridding.inputs import VERSION_FIELDS


def prepare_frozen(selection_path, cache_dir, output_dir, expected_sha256, command):
    """Verify the frozen selection and prepare its combined acquisitions."""
    selection_path, cache_dir = Path(selection_path), Path(cache_dir)
    if file_digest(selection_path) != expected_sha256:
        raise ValueError('Frozen selection SHA-256 mismatch')
    selection = json.loads(selection_path.read_text())
    names = selection['selected_granule_names']
    checksums = selection['provenance']['source_checksums']
    if len(names) != len(set(names)) or len(names) != selection['selected_granules'] or set(names) != set(checksums):
        raise ValueError('Frozen selection names/count/checksums disagree')
    sources = [SimpleNamespace(name=name, checksum_algorithm=checksums[name]['algorithm'],
                               checksum=checksums[name]['value']) for name in names]
    view = BoundingBox(**selection['bounds_wgs84'])
    minutes = selection['nisar_combine_window_minutes']
    context = dict(selection_sha256=expected_sha256, bounds_wgs84=list(view.bounds),
                   combine_window_minutes=minutes, selected_frames=selection['selected_frames'],
                   selection_rule='frozen source list; no reselection')
    with QASession(output_dir, sources, context, VERSION_FIELDS, command) as qa:
        paths = []
        for i, source in enumerate(sources, 1):
            path = cache_dir / source.name
            verify_file(path, source)
            qa.record_file(path, source)
            paths.append(path)
            print(f'QA verified {i}/{len(sources)} originals: {source.name}', flush=True)
        qa.finish_fields()
        groups = group_paths(paths, minutes)
        if len(groups) != selection['selected_frames']:
            raise ValueError('Frozen combined-acquisition count changed')
        with h5py.File(paths[0], 'r') as source:
            layout = window_layout(view, source)
        qa.diagnose((read_combined_frame(group, layout) for group in groups), layout)
