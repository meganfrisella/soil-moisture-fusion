# NISAR SME2 coverage and quality

## SME2 coverage

Coordinates are WGS84 degrees, ordered **latitude longitude**.

Catalog queried 2026-10-13 UTC for the four small regions and 2026-10-12 UTC for the two large regions. Areas use EPSG:6933; current summaries count all native cells intersecting each WGS84 box, including partial edge cells. Selection requires strictly more than `min-numeric-percent` numeric coverage in at least one candidate. Combining precedes filtering; coverage means weight selected acquisition frames equally; recommended means finite moisture with a valid even candidate flag. Percentage and interval triples are **PMI / TSR / DSG**.

Filter is **`min-numeric-percent` (%)** and combine window is **`nisar-combine-window` (minutes)**; `0` means no combining. Defaults are 50% and 10 minutes. Granules count original source products; frames count selected acquisition groups; dates count distinct source acquisition-start dates in UTC.

| Region | `--top-left LAT LON` | `--bottom-right LAT LON` | Filter (%) | Combine window (min) | Area (km²) | Native cells | Granules / frames / UTC dates | Acquisition span (UTC) | Mean numeric (%) | Mean recommended (%) | Mean per-cell interval (days) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| Enumclaw | `47.271621 -122.137915` | `47.170149 -121.951705` | 80 | 10 | 159.109 | 4,095 | 47 / 44 / 36 | 2025-11-10–2026-10-07 | 73.64 / 82.65 / 98.25 | 22.45 / 14.97 / 20.97 | 10.92 / 10.23 / 9.46 |
| Odessa | `47.405850 -118.753749` | `47.260609 -118.622651` | 80 | 10 | 160.000 | 4,096 | 34 / 25 / 16 | 2026-06-19–2026-10-10 | 63.53 / 75.44 / 99.27 | 61.37 / 75.26 / 93.01 | 8.56 / 8.09 / 7.53 |
| Puyallup | `47.254429 -122.318983` | `47.109597 -122.187885` | 80 | 10 | 160.000 | 4,096 | 36 / 36 / 36 | 2025-11-10–2026-10-07 | 63.40 / 70.91 / 82.05 | 6.30 / 5.29 / 7.37 | 10.96 / 10.23 / 9.46 |
| Snoqualmie | `47.727191 -122.024896` | `47.581070 -121.893798` | 80 | 10 | 160.000 | 4,160 | 35 / 25 / 17 | 2026-06-21–2026-10-07 | 62.81 / 75.27 / 98.32 | 2.26 / 1.96 / 3.41 | 7.20 / 7.00 / 6.75 |
| [Western Washington (full GAIA domain)](../site/gaia_domain/index.html) | `48.321364 -122.997713` | `46.496248 -120.607770` | 10 | 10 | 36,599.751 | 915,482 | 88 / 53 / 45 | 2025-11-10–2026-10-07 | 41.03 / 47.61 / 59.11 | 1.52 / 1.52 / 2.12 | 8.16 / 7.64 / 7.28 |
| [Eastern Washington](../site/eastern_washington/index.html) | `48.076483 -121.241744` | `46.057370 -117.072163` | 10 | 10 | 71,091.291 | 1,777,724 | 138 / 69 / 44 | 2026-06-17–2026-10-10 | 25.02 / 29.75 / 38.97 | 16.38 / 20.79 / 26.24 | 5.96 / 5.72 / 5.44 |

**Mean per-cell interval:** for each candidate and native cell, collect distinct source acquisition-start UTC dates with any finite moisture, regardless of flags, from selected frames. Compute `(last date − first date) / (number of dates − 1)`, then average equally across cells with at least two dates. Same-day passes count once; source dates survive combining. Cells with fewer than two dates are excluded (`temporal_interval_cells` in each local region summary records the denominator); no eligible cells gives `null`. Gaps outside each cell’s first/last observation are excluded. These are observed mean intervals within the listed spans, not guaranteed revisit periods.

The GAIA box encloses its [EPSG:5070 boundary](../../gwl-space-time-smooth/src/config/domain.py), rounded outward; it includes some area outside that boundary.

## SME2 quality flags

Paths below are relative to `/science/LSAR/SME2/grids/`: **combined** = `retrievalQualityFlag`; **candidate** = `algorithmCandidates/{PMI,TSR,DSG}/retrievalQualityFlag`. Bits are zero-indexed; test bit `b` with `flag & (1 << b)` after excluding fill values.

| Bit | Mask | Combined flag | Candidate flag (PMI / TSR / DSG) |
| ---: | ---: | --- | --- |
| 0 | 1 | 0: recommended; 1: not recommended | 0: recommended; 1: not recommended |
| 1 | 2 | PMI applied (1) / not applied (0) | Retrieval attempted status |
| 2 | 4 | TSR applied (1) / not applied (0) | Retrieval success status |
| 3 | 8 | DSG applied (1) / not applied (0) | Soil-moisture range check: 0.02–0.60 m³/m³ |
| 4 | 16 | na | HH backscatter anomaly |
| 5 | 32 | na | HV backscatter anomaly |
| 6 | 64 | na | VV backscatter anomaly |
| 7 | 128 | na | VH backscatter anomaly |

Combined definitions follow the downloaded PROVISIONAL HDF5 `description` attribute (product specification version 1.0.2), which differs from the older [Rev C specification](../data/nisar_metadata/spec.pdf#page=37). Candidate bit labels follow that specification (pp. 23, 25, 29–30); candidate metadata describes 0 as clear and nonzero as an anomaly. Bits 1–2 are status checks, not the combined flag's algorithm-use bits.

`-9999` is fill, not a bit pattern. Current maps outline valid odd **candidate** flags; coverage counts finite moisture with a valid even candidate flag as recommended. Other candidate bits may still be set. Combined flags are not used in these maps or coverage counts.

## Eastern Washington regridding

Native, nearest-neighbor, bilinear, station-preparation and validation products are generated locally in `outputs/eastern_washington/` and are not committed. The retained [method and result record](eastern_washington_regridding.md), [exact compact scores and provenance](eastern_washington_results.json), and selected figures are under `docs/`. The former five-method regression comparisons are historical exploration, not the selected-method validation cohort.
