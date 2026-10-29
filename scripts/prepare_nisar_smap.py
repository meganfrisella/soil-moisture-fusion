#!/usr/bin/env python3
"""Download/verify SMAP and prepare both products for comparison on 9 km EPSG:6933 cells.

Run from the repository root after pixi install and the completed native NISAR stage:
    pixi run python scripts/prepare_nisar_smap.py --cache-dir data/smap/SPL2SMP_E/006 \
      --manifest outputs/nisar_smap/smap_l2e_manifest.json \
      --run-dir outputs/eastern_washington/regridding_washington \
      --output-dir outputs/nisar_smap_verified

Omit --manifest for a new CMR selection. Missing SMAP originals require
EARTHDATA_USER/EARTHDATA_PASS with NSIDC Earthdata authorization. Cached originals
are verified and reused. All three stages run in order; use a new --output-dir.
Times are UTC, soil moisture and sigma are m³/m³; both quality screens are retained.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from smap.catalog import download
from smap.preparation import prepare as prepare_smap
from comparison.aggregation import prepare as aggregate_nisar


def main(argv=None):
    """Freeze/download originals, prepare SMAP and aggregate verified native NISAR."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    command = ['pixi', 'run', 'python', 'scripts/prepare_nisar_smap.py', *(sys.argv[1:] if argv is None else argv)]
    download(args.cache_dir, args.output_dir, args.manifest)
    prepare_smap(args.cache_dir, args.output_dir, command)
    aggregate_nisar(args.run_dir, args.output_dir, command)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
