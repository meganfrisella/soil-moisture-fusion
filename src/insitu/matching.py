"""UTC nearest-label matching for prepared m3/m3 observations, including missing rows.

Used by ``scripts/validate_eastern_washington.py``. The approved tolerance is inclusive
±30 minutes. Exact ties remain explicitly unresolved rather than choosing a side.
"""

import pandas as pd

TOLERANCE = pd.Timedelta(minutes=30)


def nearest_label(times, instant, *, tolerance=TOLERANCE):
    """Return a unique nearest row position/status without inspecting its moisture."""
    tolerance = pd.Timedelta(tolerance)
    if pd.isna(tolerance) or tolerance < pd.Timedelta(0):
        raise ValueError("Require finite nonnegative tolerance")
    times = pd.DatetimeIndex(times)
    instant = pd.Timestamp(instant)
    if times.tz is None or instant.tzinfo is None or times.hasnans:
        raise ValueError('Require explicit timezone-aware timestamps without NaT')
    if not times.is_unique or not times.is_monotonic_increasing:
        raise ValueError('Require unique ascending prepared timestamps')
    if not len(times):
        return None, 'no_observations'
    position = times.searchsorted(instant)
    candidates = [i for i in (position-1, position) if 0 <= i < len(times)]
    distances = [abs(times[i]-instant) for i in candidates]
    distance = min(distances)
    if distance > tolerance:
        return None, 'outside_tolerance'
    winners = [i for i, d in zip(candidates, distances) if d == distance]
    if len(winners) != 1:
        return None, 'tie_requires_decision'
    return int(winners[0]), 'matched'
