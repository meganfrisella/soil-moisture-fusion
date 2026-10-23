"""Validate the approved R36 unpacked SME2 field contract without reading fields.

``inspect_fields(path, expected_versions=None)`` returns JSON-compatible field
metadata and warnings, raising ``QAError`` on unsupported encoding. Versions
are supplied by the caller's pinned dataset policy. Catalog integrity remains
the caller's responsibility. Native grids are EPSG:6933 metres; moisture and
uncertainty are m³/m³. HDF5 storage fill is not scientific missingness.
"""

import h5py
import numpy as np

from nisar.observations import GRID, grid_definition, native
from nisar.quality import ALGORITHMS

FIELDS = ('soilMoisture', 'soilMoistureUncertainty', 'retrievalQualityFlag')
CONTRACT = 'sme2-r36-r37-v1'


class QAError(ValueError):
    """A structured contract failure suitable for a durable QA record."""

    def __init__(self, check, context, observed, expected):
        self.finding = dict(check=check, severity='error', context=context,
                            observed=observed, expected=expected)
        super().__init__(f'{check}: {context}: observed {observed!r}; expected {expected!r}')


def require(condition, check, context, observed, expected):
    """Raise a structured failure rather than silently coercing unsupported metadata."""
    if not condition:
        raise QAError(check, context, observed, expected)


def json_metadata(value):
    """Preserve nonfinite attribute values as explicit strings in strict JSON."""
    if isinstance(value, np.ndarray) and value.ndim == 0:
        value = value.item()
    value = native(value)
    if isinstance(value, list):
        return [json_metadata(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    return value


def scalar(attrs, key, context):
    """Require a finite real scalar, never interpret a string as a number."""
    value = attrs[key]
    a = np.asarray(value)
    require(a.ndim == 0 and a.dtype.kind in 'iuf' and bool(np.isfinite(a)),
            'finite_scalar', context + '/' + key, json_metadata(value), 'finite numeric scalar')
    return float(a)


def validity_bounds(attrs, context):
    """Read optional explicit bounds; reject conflicts instead of inferring ranges."""
    bounds = {side: scalar(attrs, key, context) if key in attrs else None
              for side, key in (('lower', 'valid_min'), ('upper', 'valid_max'))}
    if 'valid_range' in attrs:
        a = np.asarray(attrs['valid_range'])
        require(a.shape == (2,) and a.dtype.kind in 'iuf' and bool(np.isfinite(a).all()),
                'valid_range', context, json_metadata(a), 'two finite numeric bounds')
        for side, value in zip(('lower', 'upper'), a):
            require(bounds[side] is None or bounds[side] == float(value),
                    'conflicting_bounds', context, bounds, json_metadata(a))
            bounds[side] = float(value)
    require(bounds['lower'] is None or bounds['upper'] is None
            or bounds['lower'] <= bounds['upper'], 'ordered_bounds', context, bounds, 'lower <= upper')
    return bounds


def inspect_fields(path, expected_versions=None):
    """Check present candidate triplets, dimensions, units, fills, packing and grids."""
    result = {'candidates': {}, 'warnings': [], 'versions': {}}
    with h5py.File(path, 'r') as source:
        if expected_versions is not None:
            observed = {key: native(source[key][()]) if key in source else None
                        for key in expected_versions}
            require(observed == expected_versions, 'product_versions', str(path), observed, expected_versions)
            result['versions'] = observed
        require(GRID in source, 'native_grid', str(path), GRID in source, GRID)
        grid = source[GRID]
        require('projection' in grid, 'projection', GRID, None if 'projection' not in grid else 6933, 6933)
        try:
            dx, dy, xo, yo, rows, cols = grid_definition(grid)
        except (KeyError, ValueError, TypeError) as error:
            raise QAError('native_grid', GRID, str(error), 'consistent EPSG:6933 grid') from error
        result['grid'] = dict(crs='EPSG:6933', shape=[len(rows), len(cols)],
                              dx_m=dx, dy_m=dy, x_origin_m=xo, y_origin_m=yo)
        for candidate in ALGORITHMS:
            prefix = f'algorithmCandidates/{candidate}'
            present = [f'{prefix}/{field}' in grid for field in FIELDS]
            if not any(present) and prefix not in grid:
                result['candidates'][candidate] = {'present': False}
                result['warnings'].append(dict(check='candidate_absent', severity='warning', context=candidate))
                continue
            require(all(present), 'candidate_triplet', prefix, present, list(FIELDS))
            group = grid[prefix]
            for name in ('xCoordinates', 'yCoordinates', 'EASEGridRowIndex', 'EASEGridColumnIndex',
                         'xCoordinateSpacing', 'yCoordinateSpacing', 'projection'):
                if name in group:
                    local, root = np.asarray(group[name][()]), np.asarray(grid[name][()])
                    require(local.shape == root.shape and bool(np.array_equal(local, root)),
                            'candidate_grid_alignment', prefix + '/' + name,
                            'different' if not np.array_equal(local, root) else 'matching', 'native grid values')
            record = {'present': True, 'fields': {}}
            for field in FIELDS:
                ds, context = group[field], prefix + '/' + field
                flag = field == 'retrievalQualityFlag'
                require(ds.dtype.kind == ('i' if flag else 'f') and ds.dtype.itemsize == (2 if flag else 4),
                        'dtype', context, str(ds.dtype), 'int16' if flag else 'float32')
                require(ds.shape == (len(rows), len(cols)), 'dimensions', context, list(ds.shape), result['grid']['shape'])
                attrs = ds.attrs
                units = native(attrs.get('units', ''))
                require(units in (('1', 'unitless') if flag else ('meter^3/meter^3',)),
                        'units', context, units, 'unitless' if flag else 'meter^3/meter^3')
                require('_FillValue' in attrs, 'scientific_fill', context, None, '-9999 attribute')
                require(scalar(attrs, '_FillValue', context) == -9999,
                        'scientific_fill', context, json_metadata(attrs['_FillValue']), -9999)
                for key, expected in (('missing_value', -9999), ('scale_factor', 1), ('add_offset', 0)):
                    if key in attrs:
                        require(scalar(attrs, key, context) == expected, 'encoding_' + key,
                                context, json_metadata(attrs[key]), expected)
                require(native(attrs.get('grid_mapping', '')) == 'projection',
                        'grid_mapping', context, native(attrs.get('grid_mapping', '')), 'projection')
                bounds = validity_bounds(attrs, context)
                record['fields'][field] = dict(dtype=str(ds.dtype), shape=list(ds.shape), bounds=bounds,
                    hdf5_storage_fill=json_metadata(ds.fillvalue),
                    attributes={k: json_metadata(v) for k, v in attrs.items()
                                if k not in ('DIMENSION_LIST', 'REFERENCE_LIST')})
                if not flag and None in bounds.values():
                    result['warnings'].append(dict(check='supplier_bounds_unavailable', severity='warning',
                                                   context=context, bounds=bounds))
                if flag:
                    definitions = {k: json_metadata(attrs[k]) for k in ('flag_masks', 'flag_values', 'flag_meanings')
                                   if k in attrs}
                    # Check explicit bit definitions against the cited Rev C labels;
                    # agreement still does not certify specification-version equivalence.
                    if definitions:
                        masks = definitions.get('flag_masks', [])
                        meanings = definitions.get('flag_meanings', '').split()
                        expected = {1: 'retrieval_recommended_flag', 2: 'retrieval_attempted_flag',
                            4: 'retrieval_success_flag', 8: 'soil_moisture_thresholds',
                            16: 'nisar_hh_anomaly_flag', 32: 'nisar_hv_anomaly_flag',
                            64: 'nisar_vv_anomaly_flag', 128: 'nisar_vh_anomaly_flag'}
                        require(isinstance(masks, list) and len(masks) == len(meanings) > 0
                                and len(set(masks)) == len(masks)
                                and 'flag_values' not in definitions
                                and all(expected.get(m) == label.lower() for m, label in zip(masks, meanings)),
                                'flag_definitions_require_reconciliation', context, definitions,
                                'consistent explicit Rev C bit masks/labels; unknown definitions require review')
                    result['warnings'].append(dict(check='flag_semantics_unverified', severity='warning', context=context))
            result['candidates'][candidate] = record
        result['warnings'].append(dict(check='specification_correspondence_unverified', severity='warning',
            context=str(path), observed='Rev C 1.0.0 versus approved HDF5 specification 1.0.2'))
    return result
