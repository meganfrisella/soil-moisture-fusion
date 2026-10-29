# NISAR–SMAP and NISAR algorithm comparisons

Compare NISAR SME2 PROVISIONAL V1 with SMAP SPL2SMP_E v006 (R19240), and NISAR
algorithms with each other, over Eastern Washington: 46.057370–48.076483°N,
121.241744–117.072163°W (WGS 84), 2026-06-17 02:53:18–2026-10-10 12:39:37 UTC.
Moisture and uncertainty are in m³/m³, nominal depth 0–5 cm. Inputs comprise
69 NISAR acquisitions and 188 SMAP half orbits selected over the enclosing UTC days;
see [data dependencies](data.md).

## Preparation and matching

- **Spatial support:** use the 836 complete SMAP 9 km EASE-Grid 2.0 cells
  (EPSG:6933) inside the box. Each contains exactly 45 × 45 native NISAR cells
  (200.179 m posting), averaged without resampling. Require at least 1,013 of
  2,025 valid pixels; retain their count and standard deviation. The mean represents
  the valid portion of the cell. Grid alignment is checked within 1 m
  (observed edge offset 0.033 m).
- **Screens:** all finite non-fill moisture values, or recommended values with
  retrieval-flag bit 0 clear. Apply NISAR screening before aggregation and SMAP
  screening after pass selection. Retain finite out-of-range SMAP values;
  flag fill is missing, not a bit pattern.
- **Algorithms:** compare PMI, TSR and DSG separately. NISAR mean first averages
  the three algorithms at native pixels where all pass the screen, then aggregates.
  Algorithm pairs use identical acquisitions.
- **Time:** choose the nearest SMAP per-cell `tb_time_utc` within ±3 hours of the
  NISAR acquisition midpoint; ties choose the earlier pass. A missing or rejected
  value on that pass leaves the pair missing, with no fallback. Provider-masked
  milliseconds retain whole-second precision. Change between observation times
  is not modelled.

## Uncertainty and metrics

Aggregate NISAR σ as the linear mean of finite, nonnegative native σ values on
screened pixels, requiring at least 1,013 values. For NISAR mean, each contributing
pixel requires all three σ values and uses their mean. This assumes fully correlated
errors. **Moisture and σ supports can differ:** treating σ as an upper bound for
the moisture mean assumes the available-σ subset represents the complete moisture
support, which is unproven when σ is missing. Bias and representation error are
not included.

Differences are first minus second. Maps require at least five matched pairs per
cell and show temporal mean bias, Pearson r, or mean NISAR σ. Summaries report
pair counts, pooled bias/RMSD (equal weight per cell-time pair), median temporal r
and unbiased RMSD. NISAR–SMAP summaries also report the fraction of pairs with
valid σ satisfying |NISAR − SMAP| ≤ σ. SMAP error is unavailable (its error field
is all fill); timing and support differences also contribute. The Gaussian 68%
reference is not a calibrated expectation here, and neither product is truth.

## Results

Results from 2026-10-27 cover the study window and EPSG:6933 cells specified above.
Bias, RMSD and σ are in m³/m³; pair counts are cell-time pairs, not independent
observations. “Median σ” is the median of mapped cells’ mean σ; coverage is pooled
only over pairs with valid NISAR σ. [Exact results and replay provenance](../../metadata/nisar_smap_comparison/results.json).

| Comparison | Screen | Pairs | Cells mapped | Bias | RMSD | Median temporal r | Median NISAR σ | Within ±1σ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PMI − SMAP | all | 8,472 | 832 | +0.075 | 0.124 | 0.46 | 0.027 | 6% |
| TSR − SMAP | all | 10,292 | 834 | −0.006 | 0.047 | 0.75 | 0.013 | 40% |
| DSG − SMAP | all | 13,200 | 834 | +0.080 | 0.106 | 0.65 | 0.041 | 28% |
| NISAR mean − SMAP | all | 8,472 | 832 | +0.046 | 0.071 | 0.74 | 0.027 | 12% |
| PMI − SMAP | recommended | 4,855 | 488 | +0.121 | 0.127 | 0.37 | 0.028 | 1% |
| TSR − SMAP | recommended | 5,921 | 498 | +0.003 | 0.021 | 0.71 | 0.012 | 47% |
| DSG − SMAP | recommended | 7,538 | 498 | +0.054 | 0.065 | 0.63 | 0.041 | 37% |
| NISAR mean − SMAP | recommended | 4,690 | 471 | +0.057 | 0.062 | 0.72 | 0.027 | 9% |
| PMI − TSR | all | 14,776 | 832 | +0.082 | 0.109 | 0.70 | – | – |
| PMI − DSG | all | 14,776 | 832 | −0.005 | 0.128 | 0.52 | – | – |
| TSR − DSG | all | 17,563 | 834 | −0.088 | 0.113 | 0.71 | – | – |
| PMI − TSR | recommended | 10,331 | 600 | +0.118 | 0.123 | 0.64 | – | – |
| PMI − DSG | recommended | 10,245 | 600 | +0.065 | 0.082 | 0.44 | – | – |
| TSR − DSG | recommended | 12,566 | 616 | −0.055 | 0.066 | 0.68 | – | – |


Matched SMAP offsets range from −2.26 to +2.40 h (median |Δt| 1.65 h).
Per-cell temporal correlations use 5–23 pairs and should be interpreted accordingly.

| Published figure | NISAR–SMAP | NISAR algorithm pairs |
| --- | --- | --- |
| Bias | [PNG](../figures/nisar_comparison/nisar_smap_bias_map.png) | [PNG](../figures/nisar_comparison/nisar_pairs_bias_map.png) |
| Temporal correlation | [PNG](../figures/nisar_comparison/nisar_smap_temporal_r_map.png) | [PNG](../figures/nisar_comparison/nisar_pairs_temporal_r_map.png) |
| Mean NISAR σ | [PNG](../figures/nisar_comparison/nisar_sigma_map.png) | — |

Figure columns show all values and recommended values, respectively.

## Reproduction

Run from the repository root after `pixi install`, using new output directories.
Missing SMAP originals require `EARTHDATA_USER`/`EARTHDATA_PASS` and NSIDC authorization.
The manifest freezes selection; omit it only to create a new selection.

```sh
pixi run python scripts/prepare_nisar_smap.py --cache-dir data/smap/SPL2SMP_E/006 --manifest outputs/nisar_smap/smap_l2e_manifest.json --run-dir outputs/eastern_washington/regridding_washington --output-dir outputs/nisar_smap_verified
pixi run python scripts/compare_soil_moisture.py --input-dir outputs/nisar_smap_verified --output-dir outputs/nisar_smap_verified/comparison
```

Preparation verifies cached originals and the native NISAR run, then writes
`smap_l2e_box.zarr` and `nisar_9km.zarr` with complete file-hash inventories.
Comparison verifies those stores, embedded provenance, selection and grid coordinates;
it computes in memory and writes five PNGs, `summary.csv` and `results.json` with
input/code/figure hashes. Original data and Zarr stores remain local; legacy stores
without inventories require regeneration. Tests: `tests/test_smap.py` and
`tests/test_comparison.py`.
