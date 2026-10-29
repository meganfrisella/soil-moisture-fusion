# Workflow scripts

Run from the repository root after `pixi install`. Every command-line script accepts `--output-dir`; downloaded and generated data stay local under ignored `data/` and `outputs/` paths. Map, visualization, ingestion, station, and regridding helpers are in `src/`. Local regression and covariate experiments remain under the ignored `exploration/` directory.

- [validate_eastern_washington.py](validate_eastern_washington.py): Audit and prepare shallow station series, plot the five selected sensors, match acquisition times, and validate native/nearest/bilinear fields on common pairs. It also overlays the series with all native SME2 acquisitions. Optional precipitation checking adds context for sharp jumps without changing masks or scores.
- [pull_nisar_sme2.py](pull_nisar_sme2.py): Download missing original SME2 HDF5 products and verify existing files, product fields and checksums. File-only downloads leave combined-acquisition QA to the preparation workflow.
- [prepare_nisar_sme2.py](prepare_nisar_sme2.py): Verify or cache both checksum-pinned PROJ grids, then audit the frozen SME2 originals and combined acquisitions. QA warnings do not alter observations or eligibility.
- [regrid_eastern_washington.py](regrid_eastern_washington.py): Run the native, nearest or bilinear stage with verified resume and explicit figure inspection. `--plan-only` reports configuration, and `--summary` verifies completed stores and reports support counts.
- [summarize_nisar_sme2.py](summarize_nisar_sme2.py): Summarize selected original SME2 coverage and acquisition timing. Its source preparation also records required QA diagnostics.
- [visualize_nisar_sme2.py](visualize_nisar_sme2.py): Render SME2 GIF, PNG or HTML source views for a selected region and format. It records the same mandatory source and combined-native QA checks before rendering.
- [build_station_map.py](build_station_map.py): Build either a WGS84 bounding-box station map or the projected GAIA domain map with `--gaia-domain`. This general map workflow is separate from the selected station time-series validation.

Selected methods, assumptions, final figures and exact result/provenance hashes are in [the experiment record](../docs/regridding/methods.md).
