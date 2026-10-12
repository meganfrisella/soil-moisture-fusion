"""Shared SME2 panel definitions, fixed scales and quality colors for GIF/HTML.

Both plotted fields use m³/m³. Scale clipping is a display operation and does
not change eligibility, candidate flags, native values or uncertainty calibration.
Imported helper for ``pixi run python scripts/visualize_nisar_sme2.py``.
"""

import os
from pathlib import Path
import tempfile

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'soil-fusion-matplotlib'))
import matplotlib

DEFAULT_LIMITS = ((0., .60), (0., .08))
FIELDS = ('soilMoisture', 'soilMoistureUncertainty')
FIELD_LABELS = ('soil moisture', 'reported uncertainty')
UNITS = 'm³/m³'
MISSING_COLOR = '#c7c7c7'
ODD_COLOR = '#d73027'
COLORMAPS = ('viridis', 'cividis')


def color_maps():
    """Use the same endpoint colors for finite values below/above either fixed scale."""
    return [matplotlib.colormaps[name].with_extremes(
        bad=MISSING_COLOR, under=matplotlib.colormaps[name](0.),
        over=matplotlib.colormaps[name](1.)) for name in COLORMAPS]
