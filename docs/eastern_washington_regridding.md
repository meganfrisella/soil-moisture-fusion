# Eastern Washington SME2 regridding

## Scope

Regrid NISAR SME2 PMI, TSR and DSG soil moisture from its native 200 m EPSG:6933 grid to a 90 m EPSG:5070 grid by nearest neighbor and bilinear interpolation. The study window is **2026-06-17 02:53:18–2026-10-10 12:39:37 UTC**, with 69 acquisitions. Moisture and reported or propagated retrieval standard deviations are in **m³/m³**.

The pipeline is implemented in `src/nisar/qa`, `src/regridding` and `src/insitu`. Command-line scripts in `scripts/` select local inputs and output directories. Downloaded source files, prepared station tables and Zarr fields stay local; Git retains this record and the small figures below.

## Inputs and support

The [data-dependency table](eastern_washington_data.md) identifies the frozen local inputs and their source workflows.

The input selection is the checksum-pinned local SME2 PROVISIONAL archive in `data/nisar`, described by `outputs/eastern_washington/sme2_statistics.json` (selection SHA-256 `9db85b322d52e7e10000f607f41b08a16d609603044d8989f2666890db6c97b6`). File checksums, required version fields and combined-native diagnostics are verified before use. Finite non-fill moisture is eligible regardless of recommendation flags; each candidate retains its flag, reported uncertainty and source identity. Reported finite nonnegative uncertainty is treated as a **conditional retrieval-error standard deviation**.

The domain is the frozen Eastern Washington rectangle; top-left: (48.076483, -121.241744), bottom-right: (46.057370, -117.072163). Target centers must lie inside it and the positive-area cell support must intersect it. A fixed Washington horizontal-datum operation and checksum-pinned local PROJ grid transform between EPSG:6933 and EPSG:5070. Source and target grids are not nested.

## Methods

- **Nearest neighbor:** Each target center is assigned the Euclidean-nearest original native center. Exact edge ties choose east or south. No search for a farther valid source, extrapolation, or filling across holes. Copy the selected moisture and valid reported standard deviation. Store the native-source index so shared retrieval error can be traced.
- **Bilinear:** Use the four surrounding native centers, with raw tensor-product weights. Require all four centers in the native window and all four moisture values finite, including zero-weight neighbors. Require all four finite nonnegative standard deviations for uncertainty. Propagate independent-native-error variance as `Σ wᵢ²σᵢ²`, then take its square root. No edge extrapolation or renormalization occurs.

The independent-native-error covariance is a baseline assumption. Correlation among distinct native errors and interpolation or representation error are unquantified.

## Assumptions and decision record

Axx labels identify approximations, not verified properties. Rxx labels preserve user choices.

| Tag | Operative assumption or decision |
| --- | --- |
| A01, A02 | Native values approximate 200 m cell-average, nominal 0–5 cm moisture; the same-track, 10-minute combination is treated as a spatial snapshot without modeling within-window change. |
| A03 | A center-based estimate approximates the target 90 m cell average, including the in-domain portion of a boundary cell. |
| A04, A19, A20 | Finite non-fill eligibility is not a quality guarantee. Passing source metadata checks does not prove physical accuracy; range, flag and coverage diagnostics are investigative, not calibrated error probabilities. |
| A05, A06, A07 | Reported σ is conditionally treated as a retrieval-error standard deviation; distinct native errors are uncorrelated, while geometry, weights, masks and σ are fixed in propagation. These assumptions are unverified locally. |
| A08 | No native bias correction is made; systematic bias remains unquantified. |
| A10, A12 | Nearest assumes the selected source represents the target as a piecewise-constant field; bilinear assumes local smoothness and treats native cell means as center samples. |
| A17, A18 | A shallow point sensor is an approximate reference for a native or 90 m cell, with depth, time and area mismatch. Odessa's displayed acquisition was chosen for equal-weight mean recommended coverage across candidates, not accuracy. |
| R01 | Freeze the 138 original granules and 69 combined acquisitions under the pinned selection; verify local checksums and versions, stopping on missing or changed originals instead of silently replacing them. |
| R02 | Use GAIA's north-up 90 m EPSG:5070 lattice, EPSG:6933 area accounting, fixed boundary segmentation and checksum-pinned Washington coordinate operation. Keep positive-area intersecting target cells in geometry, but publish values only for centers covered by the domain. |
| R03, R34 | Use all finite non-fill native moisture, irrespective of recommendation flags or diagnostic range warnings, in fields and figures. Keep flag provenance; accept only finite nonnegative reported σ, preserving valid zeros and keeping moisture when σ is unavailable. |
| R04 | Restrict native contributors to positive-area domain intersections, with no halo or extrapolation. Compare methods on their common valid target-cell/acquisition support within each candidate; report method-specific availability separately. |
| R05, R06, R08, R09 | Square reported σ as the conditional variance proxy, use a diagonal native-error covariance baseline, and publish only propagated retrieval-error standard deviations. Keep shared-source target dependence; leave representation/model error, correlated native errors and calibrated predictive intervals unquantified. |
| R10 | Choose the Euclidean-nearest original native center after inverse transformation; exact ties choose east/south. An invalid selected source stays missing; retain source indices and unit weights. |
| R12 | Require all four surrounding centers and finite moisture, even for zero-weight neighbors; require four valid σ values for propagated σ. Use raw tensor-product weights with no renormalization, extrapolation or fallback. |
| R28, R29 | Audit the eleven cataloged shallow-station candidates, then validate only Lind #1, Cook Farm Field D, Lost Horse, Quartz Peak and Spokane 17 SSW; exclude Odessa and Touchet. Use nominal 5–5.08 cm channels, retain provider-suspect readings, exclude provider errors and exact zeros, preserve gaps/first duplicates, and mask every value in an exact constant run spanning at least 12 elapsed hours. This is a standard preparation rule; ambiguous Odessa calibration remains outside validation. |
| R30 | Match a unique nearest station label within inclusive ±30 minutes of both acquisition source-time bounds. Do not interpolate or search past a missing nearest value; sample the containing native or 90 m cell. No time ties or conflicting source-bound matches occurred in this cohort. |
| R31 | Report descriptive station agreement on common native/nearest/bilinear pairs, with equal weight per matched row and counts by station. Do not calculate confidence intervals or significance tests for the retained results. |
| R32, R33 | Keep verified, local float64 Zarr field/operator stores with lossless compression, one writer and explicit resume; execute all acquisitions/candidates serially with stage-specific preflight. Derived data stay local, while small figures and compact provenance are committed. |
| R35 | Select and inspect the same Odessa acquisition across original/native-display/nearest/bilinear six-panel PNGs, using fixed moisture/σ scales and native quality-flag outlines. Only these selected small figures are retained for the active methods. |
| R36, R37 | Validate each original's declared SME2 metadata and unpacked encoding before decoding; run non-destructive combined-native range, flag and coverage diagnostics as part of preparation. Warnings do not change R03 masks; unsupported or conflicting source declarations stop processing. |

## Results

### Regridded fields and figures

All **207 candidate/acquisition tasks per stage** completed in the local native, nearest and bilinear stores. Over the complete UTC window, the table counts valid **90 m target-cell/acquisition pairs**; these are availability counts, not independent observations or accuracy measures.

| Candidate | Nearest moisture | Nearest retrieval σ | Bilinear moisture | Bilinear retrieval σ |
| --- | ---: | ---: | ---: | ---: |
| PMI | 151,489,647 | 137,869,198 | 148,624,140 | 134,884,490 |
| TSR | 180,116,317 | 164,152,428 | 177,653,391 | 161,212,380 |
| DSG | 236,000,998 | 236,000,998 | 232,319,914 | 232,319,914 |

The target lattice has 8,784,001 cells intersecting the domain and 8,776,702 eligible centers per acquisition. The Odessa visualizations cover **2026-09-16 03:01:34–03:02:43 UTC**. Of 20,106 intersecting target cells in that view, 19,960 nearest and 19,805 bilinear cells have valid moisture for each candidate. Bilinear's larger missing margin follows its four-neighbor rule. The native, nearest and bilinear figures were inspected at their recorded SHA-256 digests:

- [Original EPSG:6933 cells](figures/eastern_washington_regrid/odessa_native_epsg6933.png), [native display](figures/eastern_washington_regrid/odessa_native.png), [nearest](figures/eastern_washington_regrid/odessa_nearest.png), [bilinear](figures/eastern_washington_regrid/odessa_bilinear.png).

### Shallow-station validation

Station coverage, provider decoding and missingness analysis are retained in `src/insitu`. Validation sensors were selected based on study-window availability, shallow record availability, and data quality. The five selected validation sensors are Lind #1, Cook Farm Field D, Lost Horse, Quartz Peak and Spokane 17 SSW. Nominal depths are 5 or 5.08 cm. The [five-sensor time series](figures/eastern_washington_regrid/shallow_sensors_validation.png) shows the prepared values and gaps across the full study window.

The preparation keeps the first source-order duplicate, inserts missing hourly rows with `NaN` moisture without interpolation, retains provider flags and raw values, converts documented percent to m³/m³, and masks exact zeros. Its standard rule also masks every observation, including the first, in an exact constant raw-value run spanning at least 12 elapsed hours. Abrupt jumps (>= 0.05 m³/m³) remain in the prepared values; a separate local precipitation report offers context, not a causal or exclusion rule.

For each acquisition, the temporal matcher chooses a unique nearest sensor label within an inclusive ±30 minutes of **both** source-time bounds. A missing nearest value is not replaced by a farther reading. At a station, the native value is sampled from the EPSG:6933 cell containing the point; nearest and bilinear values are sampled from the containing 90 m target cell. For each candidate, scores use only rows where the sensor and all three selected estimates are finite. RMSE uses estimate-minus-station error on those common pairs. Point sensors and cell averages have different spatial support.

The tables use the **2026-06-17 02:53:18–2026-10-10 12:39:37 UTC** study window. RMSE is in **m³/m³**. Pair counts refer to station/acquisition matches with finite sensor, native, nearest and bilinear moisture for that algorithm. Methods share the same pairs within each algorithm.

#### RMSE across all matched station/acquisition pairs

All eligible pairs from the five selected stations enter each algorithm's RMSE equally; this is not an average of the five station RMSEs. Stations with more matched pairs therefore contribute more.

| Algorithm | Common pairs | Native 200 m | Nearest 90 m | Bilinear 90 m |
| --- | ---: | ---: | ---: | ---: |
| PMI | 62 | 0.1414 | 0.1477 | 0.1401 |
| TSR | 75 | 0.0673 | 0.0672 | 0.0664 |
| DSG | 103 | 0.1961 | 0.1943 | 0.1987 |

#### RMSE by station

Every cell is ordered **PMI / TSR / DSG**. The first row gives common-pair counts for those algorithms at each station; RMSE is computed separately from each station's matched pairs.

|  | Lind #1 | Cook Farm Field D | Lost Horse | Quartz Peak | Spokane 17 SSW |
| --- | ---: | ---: | ---: | ---: | ---: |
| Common pairs | 4 / 6 / 10 | 21 / 25 / 33 | 4 / 4 / 4 | 11 / 14 / 22 | 22 / 26 / 34 |
| Native 200 m | 0.1145 / 0.0356 / 0.0879 | 0.1366 / 0.0468 / 0.0610 | 0.0586 / 0.1504 / 0.3662 | 0.0670 / 0.0788 / 0.3345 | 0.1825 / 0.0620 / 0.1497 |
| Nearest 90 m | 0.2077 / 0.0313 / 0.0436 | 0.1366 / 0.0468 / 0.0610 | 0.0406 / 0.1510 / 0.3626 | 0.0670 / 0.0788 / 0.3345 | 0.1825 / 0.0620 / 0.1497 |
| Bilinear 90 m | 0.1782 / 0.0325 / 0.0558 | 0.1341 / 0.0465 / 0.0562 | 0.0511 / 0.1512 / 0.3638 | 0.0630 / 0.0779 / 0.3317 | 0.1730 / 0.0600 / 0.1699 |

These descriptive scores establish agreement on the matched shallow sensors; they do not establish domain-wide accuracy, uncertainty calibration or fine-scale detail recovery. The [compact result and provenance record](eastern_washington_results.json) gives exact pooled and station scores, local input hashes, verified stage counts and committed figure hashes, without downloaded observations or gridded fields.

## Reproduction

Run commands from the repository root after `pixi install`. Each workflow accepts `--output-dir`; use new local directories for replay. Inspect each generated method figure and replace each `ACTUAL_*_SHA256` marker below with its file digest before proceeding to the next stage. Download scripts verify existing cache files and fetch only missing products. The shown paths contain local data and are intentionally ignored by Git.

```sh
pixi run python scripts/prepare_nisar_sme2.py --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage native --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage native --inspect-native-figure ACTUAL_NATIVE_SHA256 --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage nearest --resume --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage nearest --inspect-nearest-figure ACTUAL_NEAREST_SHA256 --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage bilinear --resume --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/regrid_eastern_washington.py --stage bilinear --inspect-bilinear-figure ACTUAL_BILINEAR_SHA256 --output-dir outputs/eastern_washington/regridding_replay

pixi run python scripts/validate_eastern_washington.py --inventory data/reference/eastern_washington/soil_moisture_stations.json --cache-dir data/insitu_coverage/2026-10-19 --run-dir outputs/eastern_washington/regridding_replay --proj-cache-dir data/proj --output-dir outputs/eastern_washington/insitu_validation/replay
```
