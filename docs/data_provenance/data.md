# Data provenance inputs

Scope: Eastern Washington, 46.057370–48.076483°N and 121.241744–117.072163°W
(EPSG:4326), 2026-06-17–2026-10-10 UTC. The table identifies products reviewed for
lineage, not a downloaded or validated fusion dataset. See [methods](methods.md).

| Product | Native support | Fields and units |
| --- | --- | --- |
| NISAR SME2 PROVISIONAL V1 | Nominal 200 m, EPSG:6933; per acquisition | Soil moisture and reported uncertainty (m³/m³), quality metadata |
| NLDAS-2 Noah hourly v2.0 | 0.125° latitude–longitude; hourly | `Rainf`, `Snowf` (kg m⁻² per preceding hour); `SoilM_0_10cm`, `SMLiq_0_10cm`, `RootMoist` (kg m⁻²); `SoilT_0_10cm` (K); `SMAvail_0_100cm`, `SMAvail_0_200cm` (%) |
| NLDAS-2 Mosaic hourly v2.0 | 0.125° latitude–longitude; hourly | `Rainf`, `Snowf`; `SoilM_0_10cm`, `SoilM_0_40cm`; `SoilT`; `SMAvail_0_40cm`, `SMAvail_0_200cm` (same quantity-specific units as Noah) |
| NLDAS-2 VIC hourly v2.0 | 0.125° latitude–longitude; hourly | `Rainf`, `Snowf`; `SoilM_layer1`, `SMLiq_layer1`, `RootMoist`; `SoilT_layer1`; `SMAvail_0_100cm`, `SMAvail_total` (same quantity-specific units as Noah) |
| ERA5-Land hourly | Native approximately 9 km; CDS 0.1° latitude–longitude grid | `volumetric_soil_water_layer_1` (0–7 cm, m³/m³), `soil_temperature_level_1` (K), `total_precipitation` (m water equivalent; retain accumulation convention) |
| SMAP L4 v8 (`SPL4SMGP`) | Nominal 9 km, EPSG:6933; three-hour averages | Surface (0–5 cm) and root-zone (0–100 cm) soil moisture (m³/m³) |
| GNSS receiver observations: CWU/PANGA, SOPAC/Garner, NOAA CORS | Station points; daily RINEX 2/3 files with nominal 1, 15 or 30 s samples | Signal strength/SNR and receiver/antenna metadata; preserve header time system (sampled files use GPS), reference frame and epoch. RINEX 3 SNR is dB-Hz; RINEX 2 units and L2 signal identity need receiver-specific interpretation. |

[edge_evidence.json](../../metadata/data_provenance/edge_evidence.json) stores reviewed nodes and relationships;
[sources.json](../../metadata/data_provenance/sources.json) stores citations, versions, access dates, query parameters,
response hashes and compact findings.
