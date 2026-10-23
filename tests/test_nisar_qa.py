"""Offline R36/R37 checks with synthetic native products and immutable QA evidence."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar import catalog
from nisar.geometry import BoundingBox, INVERSE
from nisar.observations import GRID, window_layout
from nisar.qa.metadata import QAError, inspect_fields
from nisar.qa.diagnostics import summarize_frame
from nisar.qa.reports import (AUDIT, DIAGNOSTICS, QASession, file_digest, read_record, require_qa, write_record)
from nisar.selection import read_combined_frame
from regridding.inputs import VERSION_FIELDS
from nisar_fixtures import write_valid_product
from nisar.qa.workflow import prepare_frozen
import pull_nisar_sme2


class QATests(unittest.TestCase):
    def setUp(self):
        """Create a complete small original and a partially clipped native domain."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'NISAR_TEST.h5'
        write_valid_product(self.path)
        west, south = INVERSE.transform(-450, -250)
        east, north = INVERSE.transform(50, 250)
        self.view = BoundingBox(west, south, east, north)
        with h5py.File(self.path) as source:
            self.layout = window_layout(self.view, source)
        self.output = self.root / 'qa'
        self.context = dict(bounds_wgs84=list(self.view.bounds), combine_window_minutes=10)

    def source(self):
        """Bind an intentional synthetic fixture mutation to a fresh archive checksum."""
        return SimpleNamespace(name=self.path.name, checksum_algorithm='sha256', checksum=file_digest(self.path))

    def run_qa(self):
        """Exercise the same shared metadata, diagnostics and artifact writer as ingest."""
        granule = self.source()
        with QASession(self.output, [granule], self.context, VERSION_FIELDS, ['synthetic-test']) as qa:
            catalog.verify_file(self.path, granule)
            qa.record_file(self.path, granule)
            qa.finish_fields()
            qa.diagnose([read_combined_frame([self.path], self.layout)], self.layout)

    def test_storage_zero_is_not_missing_and_optional_bounds_are_unavailable(self):
        """Scientific -9999 fills remain distinct from HDF5 storage zeros and absent ranges."""
        with h5py.File(self.path, 'r+') as source:
            source[f'{GRID}/algorithmCandidates/PMI/soilMoisture'][0, 0] = 0
            source[f'{GRID}/algorithmCandidates/PMI/soilMoistureUncertainty'][0, 0] = 0
        before = self.path.read_bytes()
        self.run_qa()
        self.assertEqual(self.path.read_bytes(), before)
        metadata = read_record(self.output / AUDIT)['payload']['files'][self.path.name]['metadata']
        self.assertFalse(metadata['candidates']['TSR']['present'])
        self.assertEqual(metadata['candidates']['PMI']['fields']['soilMoisture']['hdf5_storage_fill'], 0)
        counts = read_record(self.output / DIAGNOSTICS)['payload']['acquisitions'][0]['candidates']['PMI']['counts']
        self.assertEqual(counts['moisture_available']['valid_cells'], 9)
        self.assertEqual(counts['uncertainty_zero']['valid_cells'], 1)
        self.assertIsNone(counts['soilMoisture_supplier_upper']['count_rate_percent'])
        self.assertEqual(counts['soilMoisture_supplier_upper']['evaluable_cells'], 0)

    def test_unsupported_units_packing_fill_and_partial_triplets_stop(self):
        """Reject unsupported representations before any scientific field read."""
        for key, value in (('units', 'percent'), ('scale_factor', .01), ('add_offset', 2),
                           ('missing_value', -99), ('_FillValue', 0)):
            with self.subTest(attribute=key):
                write_valid_product(self.path)
                with h5py.File(self.path, 'r+') as source:
                    source[f'{GRID}/algorithmCandidates/PMI/soilMoisture'].attrs[key] = value
                with self.assertRaises(QAError):
                    inspect_fields(self.path, VERSION_FIELDS)
        write_valid_product(self.path)
        with h5py.File(self.path, 'r+') as source:
            del source[f'{GRID}/algorithmCandidates/PMI/retrievalQualityFlag']
        with self.assertRaisesRegex(QAError, 'candidate_triplet'):
            inspect_fields(self.path)

    def test_bad_dtype_dimensions_grid_and_bounds_stop(self):
        """Do not accept partial arrays, conflicting coordinates or inconsistent supplier ranges."""
        for case in ('dtype', 'shape', 'grid', 'bounds'):
            with self.subTest(case=case):
                write_valid_product(self.path)
                with h5py.File(self.path, 'r+') as source:
                    group = source[f'{GRID}/algorithmCandidates/PMI']
                    if case in ('dtype', 'shape'):
                        attrs = dict(group['soilMoisture'].attrs)
                        del group['soilMoisture']
                        ds = group.create_dataset('soilMoisture', data=np.zeros((3, 2) if case == 'shape' else (3, 3),
                                                                                 dtype='f8' if case == 'dtype' else 'f4'))
                        ds.attrs.update(attrs)
                    elif case == 'grid':
                        group['projection'] = 5070
                    else:
                        group['soilMoisture'].attrs.update(valid_min=.5, valid_range=[0., 1.])
                with self.assertRaises(QAError):
                    inspect_fields(self.path)

    def test_source_specific_bounds_and_independent_warning_counts_preserve_values(self):
        """Use retained tuple bounds, preserve extrema and zeros, and keep flag/missingness populations separate."""
        other = self.root / 'NISAR_SECOND.h5'
        write_valid_product(other)
        with h5py.File(self.path, 'r+') as source:
            group = source[f'{GRID}/algorithmCandidates/PMI']
            group['soilMoisture'][...] = [[-9999., -0.1, 1.2], [0., 1., .5], [.2, .2, .2]]
            group['soilMoisture'].attrs['valid_max'] = np.float32(.5)
            group['soilMoistureUncertainty'][0, 1] = -1
            group['retrievalQualityFlag'][...] = [[0, 4, 1], [-9999, 0, 0], [0, 0, 0]]
        with h5py.File(other, 'r+') as source:
            group = source[f'{GRID}/algorithmCandidates/PMI']
            group['soilMoisture'][...] = .8
            group['soilMoisture'].attrs['valid_max'] = np.float32(.9)
        frame = read_combined_frame([self.path, other], self.layout)
        before = {k: v.copy() for k, v in frame['data']['PMI'].items() if isinstance(v, np.ndarray)}
        audits = {path.name: dict(metadata=inspect_fields(path)) for path in (self.path, other)}
        counts = summarize_frame(frame, self.layout, audits)['PMI']['counts']
        self.assertEqual(counts['moisture_below_zero']['valid_cells'], 1)
        self.assertEqual(counts['moisture_above_one']['valid_cells'], 1)
        self.assertEqual(counts['soilMoisture_supplier_upper']['valid_cells'], 2)
        self.assertEqual(counts['flag_bit_0_set']['valid_cells'], 1)
        self.assertEqual(counts['flag_nonzero']['valid_cells'], 2)
        self.assertEqual(counts['uncertainty_unavailable']['valid_cells'], 1)
        self.assertAlmostEqual(counts['moisture_available']['intersection_area_m2'], 250000, places=4)
        for key, value in before.items():
            np.testing.assert_array_equal(frame['data']['PMI'][key], value)

    def test_completed_reports_reuse_without_changes_and_gate_rechecks_bytes(self):
        """Reuse matching complete reports, accept warnings, then reject changed source bytes."""
        self.run_qa()
        before = {p.name: p.read_bytes() for p in self.output.glob('*.json')}
        self.run_qa()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.glob('*.json')})
        require_qa(self.output, self.root, context=self.context)
        with self.path.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            require_qa(self.output, self.root)

    def test_stale_incomplete_tampered_and_mismatched_reports_are_rejected(self):
        """Completed status cannot bypass report digests, source lineage or current code identity."""
        self.run_qa()
        path = self.output / DIAGNOSTICS
        original = path.read_bytes()
        for field, value in (('state', 'incomplete'), ('field_audit_sha256', 'bad')):
            payload = read_record(path)['payload']
            payload[field] = value
            write_record(path, payload)
            with self.assertRaises(ValueError):
                require_qa(self.output, self.root)
            path.write_bytes(original)
        with patch('nisar.qa.reports.code_identity', return_value={}):
            with self.assertRaisesRegex(ValueError, 'stale'):
                require_qa(self.output, self.root)
        payload = json.loads(original)
        payload['payload']['state'] = 'failed'
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            require_qa(self.output, self.root)

    def test_failed_audit_is_durable_and_does_not_modify_cached_original(self):
        """Persist a structured metadata error while leaving the original bytes untouched."""
        with h5py.File(self.path, 'r+') as source:
            source[f'{GRID}/algorithmCandidates/PMI/soilMoisture'].attrs['units'] = 'percent'
        before = self.path.read_bytes()
        with self.assertRaises(QAError):
            self.run_qa()
        payload = read_record(self.output / AUDIT)['payload']
        self.assertEqual(payload['state'], 'failed')
        self.assertEqual(payload['error']['check'], 'units')
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(QAError):
            self.run_qa()
        self.assertEqual(len(read_record(self.output / AUDIT)['payload']['attempts']), 1)

    def test_frozen_preparation_is_offline_and_rejects_changed_selection(self):
        """Prepare the frozen selected population without rediscovery or missing-file downloads."""
        source = self.source()
        selection = self.root / 'selection.json'
        record = dict(selected_granule_names=[source.name], selected_granules=1, selected_frames=1,
            bounds_wgs84=dict(zip(('west', 'south', 'east', 'north'), self.view.bounds)),
            nisar_combine_window_minutes=10,
            provenance=dict(source_checksums={source.name: dict(algorithm=source.checksum_algorithm, value=source.checksum)}))
        selection.write_text(json.dumps(record))
        sha = file_digest(selection)
        with patch.object(catalog, 'earthdata_opener') as auth:
            prepare_frozen(selection, self.root, self.output, sha, ['test'])
            auth.assert_not_called()
        require_qa(self.output, self.root, selection_sha256=sha)
        selection.write_text(selection.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'selection SHA-256'):
            prepare_frozen(selection, self.root, self.output, sha, ['test'])

    def test_file_only_cli_runs_cached_qa_without_auth_and_labels_dataset_pending(self):
        """The legacy pull path writes file QA on cache reuse and cannot claim dataset readiness."""
        source = self.source()
        source.start = __import__('datetime').datetime.fromisoformat('2026-06-24T03:02:12+00:00')
        source.track, source.frame = 143, 65
        with patch.object(pull_nisar_sme2, 'search_granules', return_value=[source]), \
             patch.object(pull_nisar_sme2, 'earthdata_opener') as auth, \
             patch('sys.stdout', new_callable=io.StringIO) as console:
            self.assertEqual(pull_nisar_sme2.main(['--cache-dir', str(self.root), '--output-dir', str(self.output)]), 0)
            auth.assert_not_called()
        self.assertIn('Dataset QA NOT RUN', console.getvalue())
        self.assertIn(read_record(self.output / AUDIT)['payload']['state'], ('complete', 'complete_with_warnings'))
        self.assertFalse((self.output / DIAGNOSTICS).exists())

    def test_reuse_failure_preserves_completed_payload_and_records_failure(self):
        """A failed cache recheck cannot hide behind an older complete report."""
        self.run_qa()
        original = read_record(self.output / AUDIT)
        source = self.source()
        with self.assertRaisesRegex(ValueError, 'simulated'):
            with QASession(self.output, [source], self.context, VERSION_FIELDS, ['retry']) as qa:
                raise ValueError('simulated cache verification failure')
        failed = read_record(self.output / AUDIT)
        self.assertEqual(failed['payload'], original['payload'])
        self.assertEqual(failed['sha256'], original['sha256'])
        self.assertEqual(failed['verification_attempts'][-1]['state'], 'failed')
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            require_qa(self.output, self.root)
        self.run_qa()
        require_qa(self.output, self.root)

    def test_regridding_cli_stops_before_production_without_qa(self):
        """The supported regridding command cannot call a production stage without QA."""
        import regrid_eastern_washington as cli
        with patch.object(cli, 'describe_plan', return_value={}), \
             patch('regridding.native_stage.run_native') as run:
            with self.assertRaises(FileNotFoundError):
                cli.main(['--stage', 'native', '--output-dir', str(self.output), '--cache-dir', str(self.root)])
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
