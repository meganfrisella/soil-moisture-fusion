"""Candidate SME2 algorithm identities and bit-0/missing flag masks.

Bit 0 set means not recommended. Even nonzero flags are not rejected by this
test; negative and nonfinite fill values are tracked separately as missing.
"""

import numpy as np

ALGORITHMS = ('PMI', 'TSR', 'DSG')


def flag_masks(flags):
    """Return bit-0 rejection and missing masks; even nonzero flags are not rejected."""
    missing = ~np.isfinite(flags) | (flags < 0)
    safe = np.where(missing, 0, flags).astype(np.int64)
    return ~missing & ((safe & 1) != 0), missing


def coverage_counts(frame, mask):
    """Count finite soil moisture independently for each algorithm over the full box."""
    return {a: int(np.count_nonzero(np.isfinite(frame['data'][a]['soilMoisture']) & mask))
            for a in ALGORITHMS}


def qualifies(counts, total, minimum_percent=50):
    """Apply a strict percent cutoff to at least one algorithm, ignoring flags."""
    if not np.isfinite(minimum_percent) or not 0 <= minimum_percent < 100:
        raise ValueError('Minimum numeric percent must be at least 0 and less than 100 (strict > cutoff)')
    return total > 0 and max(counts.values(), default=0) * 100 > minimum_percent * total


def cell_counts(values, flags, mask):
    """Count numeric/recommended cells over the supplied native-grid mask."""
    odd, missing = flag_masks(flags)
    numeric = np.isfinite(values) & mask
    return {'numeric_cells': int(numeric.sum()),
            'recommended_cells': int((numeric & ~odd & ~missing).sum()),
            'total_cells': int(mask.sum())}
