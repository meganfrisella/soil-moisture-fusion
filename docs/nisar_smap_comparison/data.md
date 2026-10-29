# NISAR–SMAP comparison data

Eastern Washington: 46.057370–48.076483°N, 121.241744–117.072163°W (WGS 84).
The comparison window is 2026-06-17 02:53:18–2026-10-10 12:39:37 UTC;
SMAP selection covers the enclosing UTC days. Both products represent nominal
0–5 cm soil moisture. See [methods and results](methods.md).

| Input | Source and frozen selection | Fields, native support and units | Local inputs and preparation |
| --- | --- | --- | --- |
| NISAR SME2 | NASA CMR / ASF SME2 PROVISIONAL V1, P05023; 138 original granules combined into 69 acquisitions in the comparison window | PMI, TSR and DSG moisture and reported retrieval σ (m³/m³), candidate quality flags; nominal 200 m EPSG:6933 cells, acquisition start/end times (UTC) | Originals: `data/nisar/`; selection: `outputs/eastern_washington/sme2_statistics.json`; verified input: `outputs/eastern_washington/regridding_washington/native.zarr`. [Comparison preparation](../../scripts/prepare_nisar_smap.py) verifies the native run and aggregates it to `nisar_9km.zarr`. Original ingestion is documented in [regridding data dependencies](../regridding/data.md). |
| SMAP enhanced L2 passive moisture | NASA CMR / NSIDC SPL2SMP_E v006, R19240; 188 frozen half orbits, 2026-06-17–2026-10-10 UTC | `soil_moisture` and `soil_moisture_error` (m³/m³; error is all fill in these inputs), retrieval/surface flags and per-cell `tb_time_utc`; EASE-Grid 2.0 Global 9 km, EPSG:6933 | Originals: `data/smap/SPL2SMP_E/006/`; frozen selection: `outputs/nisar_smap/smap_l2e_manifest.json`. [Comparison preparation](../../scripts/prepare_nisar_smap.py) checks original byte counts/MD5, downloads only missing files and extracts `smap_l2e_box.zarr`. |