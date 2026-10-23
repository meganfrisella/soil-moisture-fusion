#!/usr/bin/env python3
"""Prepare the frozen SME2 inputs and required Washington transform grids.

From the repository root after ``pixi install --locked``:
    pixi run python scripts/prepare_nisar_sme2.py --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_washington

The selection SHA-256 is the approved constant from regridding.run_plan.
Checksum-pinned PROJ grids are verified in --proj-cache-dir and only missing
grids are downloaded. No SME2 catalog query, replacement or reselection occurs.
The QA stage writes two records and a lock in --output-dir; existing complete
records require identical inputs/configuration/code and are verified on reuse.
An explicitly repeated command retries failed/incomplete QA, retaining failure
history. Unsupported encoding stops before field decoding. Diagnostic warnings
never alter observations. Native nominal-200 m support is EPSG:6933, moisture
and uncertainty m³/m³, times UTC. No uncertainty calibration or method run.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from nisar.qa.workflow import prepare_frozen
from regridding.resources import GRID_SPECS, ensure_grid
from regridding.run_plan import SELECTION_SHA256


def main(argv=None):
    """Verify or cache transforms, then audit frozen NISAR originals and frames."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--selection', type=Path, default=ROOT / 'outputs/eastern_washington/sme2_statistics.json')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'data/nisar')
    parser.add_argument('--proj-cache-dir', type=Path, default=ROOT / 'data/proj')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    command = ['pixi', 'run', 'python', 'scripts/prepare_nisar_sme2.py', *(sys.argv[1:] if argv is None else argv)]
    try:
        for grid_name in GRID_SPECS:
            ensure_grid(args.proj_cache_dir, grid_name)
        prepare_frozen(args.selection, args.cache_dir, args.output_dir, SELECTION_SHA256, command)
    except (ValueError, KeyError, RuntimeError, OSError) as error:
        print(f'QA preparation stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
