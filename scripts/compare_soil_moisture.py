#!/usr/bin/env python3
"""Compare verified NISAR/SMAP and NISAR algorithm pairs and render five agreement maps.

Run from the repository root after pixi install and prepare_nisar_smap.py:
    pixi run python scripts/compare_soil_moisture.py \
      --input-dir outputs/nisar_smap_verified --output-dir outputs/nisar_smap_verified/comparison

Reads the two existing prepared Zarr stores and their checksum records offline.
Matches and metrics remain in memory. Writes five PNGs, summary.csv and results.json
into a new directory. The comparison uses 9 km EPSG:6933 cells, UTC acquisition
midpoints/per-cell SMAP times and m³/m³ values under all/recommended screens.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comparison.workflow import run


def main(argv=None):
    """Compare both product families and publish their figures and compact results."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    command = ['pixi', 'run', 'python', 'scripts/compare_soil_moisture.py', *(sys.argv[1:] if argv is None else argv)]
    run(args.input_dir, args.output_dir, command)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
