"""Persist checksum-bound QA records and reject stale/incomplete downstream use.

``QASession(output_dir, sources, context, expected_versions, command)`` owns an
exclusive writer lock. Record each verified file, finish the field audit, then
diagnose selected combined frames. Cached complete records are immutable and
reused only with matching identities. ``require_qa`` verifies both reports and
original checksums before downstream use. Units/support are retained in reports.
"""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile

import h5py
import numpy as np

from nisar.qa.metadata import CONTRACT, QAError, inspect_fields

ROOT = Path(__file__).resolve().parents[3]
AUDIT = 'sme2_field_audit.json'
DIAGNOSTICS = 'sme2_diagnostics.json'
COMPLETE = ('complete', 'complete_with_warnings')


def encode(value):
    """Encode a strict, stable JSON identity."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    """Hash a JSON record independent of whitespace."""
    return hashlib.sha256(encode(value)).hexdigest()


def file_digest(path):
    """Stream a local SHA-256 fingerprint."""
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def code_identity():
    """Pin QA, scientific dependencies and the environment without unrelated run code."""
    paths = [*sorted((ROOT / 'src/nisar').glob('*.py')),
             *sorted((ROOT / 'src/nisar/qa').glob('*.py')),
             ROOT / 'src/ingest/downloads.py', ROOT / 'pixi.lock',
             *(ROOT / 'scripts' / name for name in ('prepare_nisar_sme2.py', 'pull_nisar_sme2.py',
                 'summarize_nisar_sme2.py', 'visualize_nisar_sme2.py')),
             *(ROOT / 'src/regridding' / name for name in
               ('inputs.py', 'quality.py', 'support.py'))]
    return {str(p.relative_to(ROOT)): file_digest(p) for p in paths}


def now():
    """Use timezone-qualified UTC execution times."""
    return datetime.now(timezone.utc).isoformat()


def environment_identity():
    """Record runtime versions in addition to the lock-file fingerprint."""
    return dict(python=platform.python_version(), h5py=h5py.__version__,
                hdf5=h5py.version.hdf5_version, numpy=np.__version__)


def write_record(path, payload, verification_attempts=None):
    """Atomically publish a strict JSON payload and its content digest."""
    record = dict(payload=payload, sha256=digest(payload))
    if verification_attempts:
        record.update(verification_attempts=verification_attempts,
                      verification_attempts_sha256=digest(verification_attempts))
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(encode(record) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path):
    """Reject truncated, edited or otherwise inconsistent report content."""
    record = json.loads(Path(path).read_text())
    allowed = ({'payload', 'sha256'},
               {'payload', 'sha256', 'verification_attempts', 'verification_attempts_sha256'})
    if (set(record) not in allowed or digest(record['payload']) != record['sha256']
            or ('verification_attempts' in record
                and digest(record['verification_attempts']) != record['verification_attempts_sha256'])):
        raise ValueError(f'QA report checksum mismatch: {path}')
    return record


def source_identity(granules):
    """Freeze each named original and its archive checksum before transfer or analysis."""
    records = [dict(name=g.name, checksum_algorithm=g.checksum_algorithm, checksum=g.checksum) for g in granules]
    if not records or len({r['name'] for r in records}) != len(records):
        raise ValueError('QA requires unique nonempty source names')
    if any(Path(r['name']).name != r['name'] for r in records):
        raise ValueError('Unsafe source filename')
    return sorted(records, key=lambda r: r['name'])


class QASession:
    """Own a file-audit and optional combined-diagnostic execution; never mutate inputs."""

    def __init__(self, output_dir, sources, context, expected_versions, command):
        self.root = Path(output_dir)
        self.identity = dict(contract=CONTRACT, sources=source_identity(sources), context=context,
                             expected_versions=expected_versions, code_sha256=code_identity(),
                             environment=environment_identity())
        self.command = list(map(str, command))
        self.files = {}
        self.current = AUDIT
        self.reused = {}
        self.payloads = {}
        self.verification_attempts = {}

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / '.sme2_qa.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for name in (AUDIT, DIAGNOSTICS):
                path = self.root / name
                if path.exists():
                    record = read_record(path)
                    payload = record['payload']
                    if payload['identity'] != self.identity:
                        raise ValueError('QA identity changed; use a new output directory/execution')
                    self.payloads[name] = payload
                    self.verification_attempts[name] = record.get('verification_attempts', [])
                    self.reused[name] = payload['state'] in COMPLETE
            if not self.reused.get(AUDIT):
                self.start(AUDIT)
            return self
        except BaseException:
            self.lock.close()
            raise

    def start(self, name):
        """Record incomplete execution, retaining prior failed-attempt evidence."""
        previous = self.payloads.get(name)
        history = [] if previous is None else previous.get('attempts', []) + [
            {k: previous.get(k) for k in ('started_utc', 'finished_utc', 'state', 'error')}]
        self.payloads[name] = dict(identity=self.identity, command=self.command, state='incomplete',
                                   started_utc=now(), attempts=history)
        write_record(self.root / name, self.payloads[name])

    def record_file(self, path, granule):
        """Record R36 evidence after the caller verifies archive integrity on this invocation."""
        metadata = inspect_fields(path, self.identity['expected_versions'])
        self.files[granule.name] = dict(name=granule.name, bytes=Path(path).stat().st_size,
            archive_checksum=dict(algorithm=granule.checksum_algorithm, value=granule.checksum), metadata=metadata)

    def finish_fields(self):
        """Publish complete field evidence only after every required original was validated."""
        if set(self.files) != {s['name'] for s in self.identity['sources']}:
            raise ValueError('Field audit is incomplete')
        files = dict(sorted(self.files.items()))
        warnings = [dict(w, source=name) for name, record in files.items() for w in record['metadata']['warnings']]
        result = self.finish(AUDIT, dict(files=files, warnings=warnings, dataset_diagnostics='separate required stage'))
        if self.identity['context'].get('scope') != 'file-only':
            self.current = DIAGNOSTICS
            if not self.reused.get(DIAGNOSTICS):
                self.start(DIAGNOSTICS)
        return result

    def finish(self, name, values):
        """Keep matching completed evidence immutable; reject changed results on reuse."""
        if self.reused.get(name):
            if any(self.payloads[name].get(k) != v for k, v in values.items()):
                raise ValueError('Completed QA content differs on verified reuse')
            attempts = self.verification_attempts.get(name, [])
            if attempts and attempts[-1]['state'] != 'complete':
                attempts.append(dict(state='complete', finished_utc=now(), command=self.command))
                write_record(self.root / name, self.payloads[name], attempts)
        else:
            self.payloads[name].update(values, state='complete_with_warnings' if values['warnings'] else 'complete',
                                       finished_utc=now())
            write_record(self.root / name, self.payloads[name])
        checks = sorted({w['check'] for w in values['warnings']})
        print(f'QA {self.root / name}: {self.payloads[name]["state"]}; '
              f'{len(values["warnings"])} warnings ({", ".join(checks) or "none"})', flush=True)
        return read_record(self.root / name)['sha256']

    def diagnose(self, frames, layout):
        """Compute diagnostics from selected frames, binding them to the completed field audit."""
        from nisar.qa.diagnostics import summarize_frame
        from nisar.selection import acquisition_time
        if self.current != DIAGNOSTICS:
            raise ValueError('Complete field QA before combined diagnostics')
        audit_sha = read_record(self.root / AUDIT)['sha256']
        records, warnings = [], []
        for frame in frames:
            sources = frame['metadata']['source_granules']
            names = [s['granule'] for s in sources]
            acquisition_id = digest(dict(sources=names, domain=self.identity['context']))
            candidates = summarize_frame(frame, layout, self.files)
            records.append(dict(acquisition_id=acquisition_id, sources=names,
                start_utc=min(acquisition_time(s['zeroDopplerStartTime']) for s in sources).isoformat(),
                end_utc=max(acquisition_time(s['zeroDopplerEndTime']) for s in sources).isoformat(), candidates=candidates))
            for candidate, record in candidates.items():
                warnings.extend(dict(w, acquisition_id=acquisition_id) for w in record['warnings'])
            print(f'QA combined acquisition {len(records)}: {len(names)} sources', flush=True)
        if not records:
            raise ValueError('No selected acquisitions for dataset diagnostics')
        return self.finish(DIAGNOSTICS, dict(field_audit_sha256=audit_sha, acquisitions=records,
            warnings=warnings, units='m3/m3', area_units='m2', crs='EPSG:6933',
            support='domain-clipped native nominal-200m cells',
            limitations=['Diagnostic reasons overlap; do not sum independent counts.',
                          'Raw missing-value causes may already be collapsed by ingestion.',
                          'Flag meanings and uncertainty interpretation remain conditional.',
                          'No filtering, accuracy assessment or uncertainty calibration.']))

    def __exit__(self, kind, error, traceback):
        try:
            if error is not None:
                finding = error.finding if isinstance(error, QAError) else dict(
                    check='execution_failure', severity='error', type=kind.__name__, observed=str(error))
                payload = self.payloads[self.current]
                if self.reused.get(self.current):
                    attempts = self.verification_attempts.get(self.current, [])
                    attempts.append(dict(state='failed', finished_utc=now(), command=self.command, error=finding))
                    write_record(self.root / self.current, payload, attempts)
                else:
                    if self.current == AUDIT:
                        payload['files'] = dict(self.files)
                    payload.update(state='failed', finished_utc=now(), error=finding)
                    write_record(self.root / self.current, payload)
        finally:
            self.lock.close()


def require_qa(output_dir, cache_dir, *, selection_sha256=None, context=None):
    """Verify matching complete reports and originals before analysis; warnings are allowed."""
    from types import SimpleNamespace
    from nisar.catalog import verify_file
    root = Path(output_dir)
    audit, diagnostic = read_record(root / AUDIT), read_record(root / DIAGNOSTICS)
    a, d = audit['payload'], diagnostic['payload']
    for record in (audit, diagnostic):
        attempts = record.get('verification_attempts', [])
        if attempts and attempts[-1]['state'] != 'complete':
            raise ValueError('Latest QA verification failed; rerun dataset preparation')
    if a['state'] not in COMPLETE or d['state'] not in COMPLETE:
        raise ValueError('Complete field audit and diagnostics required')
    identity = a['identity']
    if d['identity'] != identity or d['field_audit_sha256'] != audit['sha256']:
        raise ValueError('QA report lineage mismatch')
    if (identity['contract'] != CONTRACT or identity['code_sha256'] != code_identity()
            or identity['environment'] != environment_identity()):
        raise ValueError('QA code/contract identity is stale')
    if selection_sha256 is not None and identity['context'].get('selection_sha256') != selection_sha256:
        raise ValueError('QA frozen-selection identity mismatch')
    if context is not None and identity['context'] != context:
        raise ValueError('QA domain/grouping/selection context mismatch')
    if set(a['files']) != {s['name'] for s in identity['sources']}:
        raise ValueError('QA source inventory incomplete')
    for source in identity['sources']:
        path = Path(cache_dir) / source['name']
        verify_file(path, SimpleNamespace(**source))
        inspect_fields(path, identity['expected_versions'])
    return dict(field_audit_sha256=audit['sha256'], diagnostics_sha256=diagnostic['sha256'])
