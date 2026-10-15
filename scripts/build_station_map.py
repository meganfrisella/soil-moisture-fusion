#!/usr/bin/env python3
"""Build a station map for any WGS84 bounding box using GAIA sensor conventions.

Run from the repository root after pixi install:
    pixi run python scripts/build_station_map.py \
        --top-left 48.076483 -121.241744 --bottom-right 46.057370 -117.072163 \
        --title "Eastern Washington" --output-dir outputs/eastern_washington \
        --sme2-map outputs/eastern_washington/eastern_washington.html

Includes UW/CC seismic, AWDB/USCRN soil moisture, SNOTEL SWE, USGS daily
discharge gauges and wells passing GAIA's groundwater QC since 2000.
Historical/inactive sites are included; activity is reported separately.
Corners are latitude longitude in WGS84 degrees. Catalog responses are cached
and verified in --cache-dir (default data/station_catalogs/YYYY-MM-DD).
Existing output metadata is reused unless --refresh is supplied.
Requires curl, internet, and the local GAIA checkout (--gaia-root) for source
configuration. No credentials are needed. Chrome/Chromium is required for the
PNG preview; use --browser to specify its executable. Outputs are station_map.html
and station_map.png; --output-dir selects their destination.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from maps.station_map import main


if __name__ == "__main__":
    main(box_required=True)
