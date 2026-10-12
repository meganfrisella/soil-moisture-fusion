# Outputs

## SME2 coverage

Coordinates are WGS84 degrees, ordered **latitude longitude**.

Catalog queried 2026-10-12 UTC. Areas use EPSG:6933; current summaries count all native cells intersecting each WGS84 box, including partial edge cells. Selection requires strictly more than `min-numeric-percent` numeric coverage in at least one candidate. Combining precedes filtering; means weight selected acquisition frames equally; recommended means finite moisture with a valid even candidate flag. Percentage triples are **PMI / TSR / DSG**.

Filter is **`min-numeric-percent` (%)** and combine window is **`nisar-combine-window` (minutes)**; `0` means no combining. Defaults are 50% and 10 minutes. Granules count original source products; frames count selected acquisition groups; dates count distinct source acquisition-start dates in UTC.

| Region | `--top-left LAT LON` | `--bottom-right LAT LON` | Filter (%) | Combine window (min) | Area (km²) | Native cells | Granules / frames / UTC dates | Acquisition span (UTC) | Mean numeric (%) | Mean recommended (%) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| [Enumclaw](enumclaw/) | `47.271621 -122.137915` | `47.170149 -121.951705` | 80 | 0 | 159.109 | 3,916 | 44 / 44 / 36 | 2025-11-10–2026-10-07 | 73.65 / 82.66 / 98.22 | 23.30 / 15.54 / 21.76 |
| [Odessa](odessa/) | `47.405850 -118.753749` | `47.260609 -118.622651` | 80 | 0 | 160.000 | 3,969 | 29 / 29 / 15 | 2026-06-19–2026-10-05 | 61.52 / 68.98 / 96.61 | 59.64 / 68.84 / 90.86 |
| [Puyallup](puyallup/) | `47.254429 -122.318983` | `47.109597 -122.187885` | 80 | 0 | 160.000 | 3,969 | 36 / 36 / 36 | 2025-11-10–2026-10-07 | 63.32 / 70.79 / 81.96 | 6.44 / 5.40 / 7.53 |
| [Snoqualmie](snoqualmie/) | `47.727191 -122.024896` | `47.581070 -121.893798` | 80 | 0 | 160.000 | 3,969 | 26 / 26 / 17 | 2026-06-21–2026-10-07 | 64.12 / 76.17 / 98.26 | 2.31 / 1.88 / 3.41 |
| [Western Washington (full GAIA domain)](gaia_domain/) | `48.321364 -122.997713` | `46.496248 -120.607770` | 10 | 10 | 36,599.751 | 915,482 | 88 / 53 / 45 | 2025-11-10–2026-10-07 | 41.03 / 47.61 / 59.11 | 1.52 / 1.52 / 2.12 |
| [Eastern Washington](eastern_washington/) | `48.076483 -121.241744` | `46.057370 -117.072163` | 10 | 10 | 71,091.291 | 1,777,724 | 138 / 69 / 44 | 2026-06-17–2026-10-10 | 25.02 / 29.75 / 38.97 | 16.38 / 20.79 / 26.24 |

The GAIA box encloses its [EPSG:5070 boundary](../../gwl-space-time-smooth/src/config/domain.py), rounded outward; it includes some area outside that boundary.
