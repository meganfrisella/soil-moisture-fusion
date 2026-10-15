# Scripts

Run from the repository root with `pixi run python scripts/<name>.py --output-dir DIR`.

- [build_gaia_map.py](build_gaia_map.py): Builds the GAIA HTML map and PNG preview with one convex hull per NISAR track across overlapping frames (preview requires Chrome/Chromium and internet).
- [pull_nisar_sme2.py](pull_nisar_sme2.py): Downloads checksum-verified SME2 granules for configured tracks and UTC dates into `data/nisar/`.
- [visualize_nisar_sme2.py](visualize_nisar_sme2.py): Generates bounding-box SME2 GIF/PNG/HTML visuals with configurable numeric coverage, same-track combining, and red outlines for odd candidate flags.
- [summarize_nisar_sme2.py](summarize_nisar_sme2.py): Reports area, intersecting native cells, source granules/combined frames, UTC dates/span, mean candidate coverage, and mean per-cell observation intervals using the same filtering and combining as the visualizer (defaults: 50%, 10 minutes). Intervals use finite values on distinct UTC dates, averaged equally over cells with at least two dates, separately for PMI/TSR/DSG.
