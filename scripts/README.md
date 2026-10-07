# Scripts

Run scripts from the repository root with `pixi run python scripts/<name>.py`.
Downloader stations, dates, and destinations are configured with constants; dates are inclusive UTC days.

- [build_station_map.py](build_station_map.py): Builds the Odessa station and NISAR footprint HTML map from `data/station_map.json`. Use `--refresh` to update the snapshot from public catalogs.
- [pull_seismic.py](pull_seismic.py): Downloads daily native miniSEED waveforms and response-level StationXML for UW.OD2 and UW.WOLL to `data/seismic/`. Use `--list-only` to print planned requests without downloading.
- [pull_soil_moisture.py](pull_soil_moisture.py): Downloads ODSW soil-moisture CSV to `data/soil_moisture/`, selecting the UTC interval while preserving native local timestamps, values, and the documented “percent” unit. Use `--list-only` to print the request without downloading.
- [pull_nisar_sme2.py](pull_nisar_sme2.py): Downloads checksum-verified native SME2 HDF5 products to `data/nisar/` using exported `EARTHDATA_USER` and `EARTHDATA_PASS`. Use `--list-only` to query and list catalog matches without saving files or authenticating.

## Data sources

| Name | Link | Data source / hosting | Date range (UTC, inclusive) | Spatial resolution / support | Temporal resolution | Download size | Average download speed | Download duration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PNSN seismic — UW.OD2, UW.WOLL | EarthScope [waveforms](https://service.earthscope.org/fdsnws/dataselect/1/) and [StationXML](https://service.earthscope.org/fdsnws/station/1/) | [AWS S3, us-east-2 (Ohio)](https://docs.earthscope.org/sponsored-open-data) | 2026-06-24–2026-09-16 | na | 100 Hz (0.01 s) | 4.07 GiB | 5.99 MiB/s | 696.084 s |
| AgriMet soil moisture — ODSW | USBR [CSV API](https://www.usbr.gov/pn-bin/instant.pl?list=odsw%20xsm2,odsw%20xsm8,odsw%20xsm20,odsw%20xsm40&start=2026-06-23&end=2026-09-16&format=csv&flags=true) and [station inventory](https://www.usbr.gov/pn/agrimet/aginfo/station_params.html#ODSW) | USBR server `www.usbr.gov/pn-bin/instant.pl` | 2026-06-24–2026-09-16 | Point probes at 2, 8, 20, 40 in | 15 minutes | 298.36 KiB | 130.65 KiB/s | 2.284 s |
| NISAR L3 SME2 — PROVISIONAL V1, track 143/frame 65 | ASF [product guide](https://nisar-docs.asf.alaska.edu/sme2/) and [HDF5 specification](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-107677_RevC_NASA_SDS_Product_Specification_L3_SME2_Nov8_2024_w-sigs.pdf) | [AWS S3, us-west-2 (Oregon)](https://nisar-docs.asf.alaska.edu/aws-s3-access/) | 2026-06-24–2026-09-16 | 200 m EASE-Grid 2.0 spacing (EPSG:6933) | 12 days | 674.00 MiB | 7.15 MiB/s | 94.232 s |