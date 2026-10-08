# Scripts

Run scripts from the repository root with `pixi run python scripts/<name>.py`.
Downloader stations, dates, and destinations are configured with constants; dates are inclusive UTC days.

- [build_station_map.py](build_station_map.py): Builds the Odessa station and NISAR footprint HTML map from `data/station_map.json`. Use `--refresh` to update the snapshot from public catalogs.
- [pull_nisar_sme2.py](pull_nisar_sme2.py): Downloads checksum-verified native SME2 HDF5 products to `data/nisar/` using exported `EARTHDATA_USER` and `EARTHDATA_PASS`. Use `--list-only` to query and list catalog matches without saving files or authenticating.

The seismic downloader, raw data, correlation inspection workflows, designs, tests, and figures are retained under [exploration/](../exploration/README.md). Future dv/v processing and uncertainty estimation are delegated to codameter.

The [ODSW soil-moisture downloader](../exploration/scripts/pull_soil_moisture.py), CSV data, provenance and tests are also retained under `exploration/`.
