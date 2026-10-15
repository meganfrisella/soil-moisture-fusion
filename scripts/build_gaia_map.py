#!/usr/bin/env python3
"""Build the GAIA domain station map and PNG using shared map conventions.

Run from the repository root after pixi install:
    pixi run python scripts/build_gaia_map.py --output-dir outputs
    pixi run python scripts/build_gaia_map.py --refresh --output-dir outputs

Offline rebuilds reuse outputs/station_map.html. Refresh requires the local
GAIA checkout (--gaia-root), curl, and internet. Catalog responses are verified
and reused in --cache-dir (default data/station_catalogs/YYYY-MM-DD).
Chrome/Chromium and internet are required for the PNG preview. Use --browser
for a nonstandard browser path. --sme2-map reads footprint metadata from an
existing SME2 viewer; HDF5 downloads and credentials are unnecessary.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from maps.station_map import main


if __name__ == "__main__":
    main()
