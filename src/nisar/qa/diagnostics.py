"""R37 combined-native diagnostics without altering observations or eligibility.

``summarize_frame(frame, layout, audits)`` takes an existing combined frame,
its native EPSG:6933 layout, and filename-keyed R36 records. It returns counts,
clipped m² areas and explicit evaluable denominators. Source indices must be
local to ``frame['metadata']['source_granules']`` (the existing reader default).
No raw-granule or derived-output diagnostics, clipping, uncertainty calibration
or pixel-level exports are performed. Repository scripts supply src and scripts
on PYTHONPATH to reuse the approved regridding mask and coverage functions.
"""

import numpy as np

from nisar.quality import ALGORITHMS, flag_masks
from regridding.quality import input_masks
from regridding.support import coverage


def native_areas(layout):
    """Compute exact rectangle intersections for the native equal-area lattice."""
    xmin, ymin, xmax, ymax = layout['square'].bounds
    x, y, dx, dy = layout['xx'], layout['yy'], layout['dx'], abs(layout['dy'])
    width = np.maximum(0, np.minimum(x + dx / 2, xmax) - np.maximum(x - dx / 2, xmin))
    height = np.maximum(0, np.minimum(y + dy / 2, ymax) - np.maximum(y - dy / 2, ymin))
    return np.where(layout['mask'], width * height, 0.)


def category(mask, evaluable, areas):
    """Summarize a reason independently, preserving both domain and evaluable denominators."""
    domain = areas > 0
    mask, evaluable = np.asarray(mask, bool) & domain, np.asarray(evaluable, bool) & domain
    if np.any(mask & ~evaluable):
        raise ValueError('Diagnostic reason lies outside its evaluable population')
    result = coverage(mask, areas, domain)
    count, area = int(evaluable.sum()), float(areas[evaluable].sum())
    result.update(evaluable_cells=count, evaluable_area_m2=area,
                  count_rate_percent=100 * result['valid_cells'] / count if count else None,
                  area_rate_percent=100 * result['valid_area_m2'] / area if area else None,
                  not_evaluable_cells=int((domain & ~evaluable).sum()))
    return result


def summarize_frame(frame, layout, audits):
    """Report source-aware bounds, independent missingness and raw flag bits for one acquisition."""
    areas = native_areas(layout)
    domain = areas > 0
    sources = frame['metadata']['source_granules']
    result = {}
    for candidate in ALGORITHMS:
        data = frame['data'][candidate]
        moisture, sigma, flags = (data[k] for k in
            ('soilMoisture', 'soilMoistureUncertainty', 'retrievalQualityFlag'))
        valid = input_masks(moisture, sigma, moisture_fill_value=None, uncertainty_fill_value=None)
        mv, uv = valid['moisture_valid'], valid['uncertainty_valid']
        bit0, missing_flags = flag_masks(flags)
        fv = ~missing_flags
        masks = dict(moisture_available=mv, moisture_unavailable=~mv,
                     uncertainty_available=uv, uncertainty_unavailable=~uv,
                     joint_available=mv & uv, uncertainty_zero=valid['reported_zero'],
                     flag_missing=missing_flags, flag_zero=fv & (flags == 0),
                     flag_nonzero=fv & (flags != 0))
        counts = {name: category(mask, domain, areas) for name, mask in masks.items()}
        counts.update(moisture_below_zero=category(mv & (moisture < 0), mv, areas),
                      moisture_above_one=category(mv & (moisture > 1), mv, areas))
        for name in ('flag_missing', 'flag_zero', 'flag_nonzero'):
            counts[name + '_among_moisture'] = category(masks[name] & mv, mv, areas)
        safe = np.where(fv, flags, 0).astype(np.int16)
        for bit in range(15):
            set_bit = fv & ((safe & (1 << bit)) != 0)
            counts[f'flag_bit_{bit}_set'] = category(set_bit, fv, areas)
            counts[f'flag_bit_{bit}_set_among_moisture'] = category(set_bit & mv, fv & mv, areas)
        counts['flag_bit_0_clear'] = category(fv & ~bit0, fv, areas)
        counts['recommended_among_moisture_conditional'] = category(mv & fv & ~bit0, mv, areas)
        source_index = data['source_index']
        if np.any(domain & ((source_index < -1) | (source_index >= len(sources)))):
            raise ValueError('Invalid combined source index')
        for field, values in (('soilMoisture', moisture), ('soilMoistureUncertainty', sigma)):
            finite = np.isfinite(values)
            for side, comparator in (('lower', np.less), ('upper', np.greater)):
                evaluable, violation = np.zeros(domain.shape, bool), np.zeros(domain.shape, bool)
                for i, source in enumerate(sources):
                    record = audits[source['granule']]['metadata']['candidates'][candidate]
                    if not record['present']:
                        continue
                    bound = record['fields'][field]['bounds'][side]
                    if bound is not None:
                        selected = domain & finite & (source_index == i)
                        evaluable |= selected
                        violation |= selected & comparator(values, bound)
                counts[f'{field}_supplier_{side}'] = category(violation, evaluable, areas)
        warning_checks = ('moisture_unavailable', 'uncertainty_unavailable', 'uncertainty_zero',
                          'flag_missing', 'flag_nonzero', 'moisture_below_zero', 'moisture_above_one')
        warnings = [dict(check=name, severity='warning', context=candidate,
                         cells=counts[name]['valid_cells']) for name in warning_checks
                    if counts[name]['valid_cells']]
        for name, value in counts.items():
            if '_supplier_' in name:
                if value['valid_cells']:
                    warnings.append(dict(check=name, severity='warning', context=candidate,
                                         cells=value['valid_cells']))
                if value['evaluable_cells'] == 0:
                    warnings.append(dict(check=name + '_not_evaluable', severity='warning', context=candidate))
        result[candidate] = dict(counts=counts, warnings=warnings)
    return result
