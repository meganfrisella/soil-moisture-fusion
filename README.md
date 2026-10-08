# soil-moisture-fusion

## Goal

We want to integrate [NISAR SME2](https://nisar-docs.asf.alaska.edu/sme2/) soil moisture observations and their uncertainties into [GAIA's soil-state model](https://github.com/gaia-hazlab/gwl-space-time-smooth), and evaluate the added accuracy and uncertainty calibration.
Ultimately, we want to contribute methodology for multi-modal data fusion that generalizes to arbitrary temporal and spatial resolution, propagating uncertainty accordingly.  

## Milestones

0. Literature review.
    - See [Related work](#related-work) and [related_work.md](docs/related_work.md)
1. Identify data sources, observation period, observation area and target spatial and temporal resolutions for the pilot.
    - See data specs and stats at [data/README.md](data/README.md)
    - Target spatial / temporal resolution: 90m / 1 day
2. Produce gridded NISAR SME2 observations (surface soil-moisture estimate with uncertainty) for the pilot region at the target spatiotemporal resolution. 
    - Gridded data should be compatible with the soil-state model's data representation. 
    - Uncertainty estimation should combine SME2's provided uncertainty layers with compounding effects introduced by spatiotemporal gridding.
    - Must design validaton criteria to assess the uncertainty calibration.
3. Relate NISAR SME2 surface observations to the soil-state model's root zone target. Requires designing and validating an explicit depth conversion with uncertainty propagation. 
4. Compare the soil-state model with and without NISAR SME2, assessing estimation error and interval coverage. 

## Evaluation targets

| Target | Evaluation focus |
|---|---|
| SME2 retrieval uncertainty | Whether reported surface-moisture uncertainty describes retrieval errors at the original observation footprint and time. |
| SME2 spatiotemporal gridding uncertainty | Whether uncertainty after aggregation, resampling, interpolation or gap filling accounts for changes in spatial and temporal support, including correlated errors. |
| Surface-to-root-zone mapping uncertainty | Whether uncertainty propagated through the depth relationship accounts for mapping errors and uncertain parameters. |
| Impact of NISAR integration | Whether GAIA with NISAR improves estimation accuracy and uncertainty calibration relative to GAIA without NISAR. |

Across all four targets, assess interval coverage, interval width and probabilistic scores (e.g., CRPS) against withheld references, accounting for reference uncertainty and matching spatial, temporal and depth support. Check error dependence across inputs, locations and times; a finer grid or smaller reported uncertainty alone does not establish improvement.

## Environment setup

```
pixi shell
```

## Odessa data sources map

[Interactive map](https://meganfrisella.github.io/soil-moisture-fusion/) of seismometers, soil moisture stations, and NISAR footprints around Odessa, WA.
Map inputs live in `data/station_map.json`; the generated HTML and PNG live in `outputs/`.

[![Odessa station and NISAR footprint map](outputs/station_map.png)](https://meganfrisella.github.io/soil-moisture-fusion/)

## Related work

- Lal et al. (2024), [“Uncertainty estimates in the NISAR high-resolution soil moisture retrievals from multi-scale algorithm”](https://doi.org/10.1016/j.rse.2024.114288), *Remote Sensing of Environment*, 311, 114288. Derives analytical retrieval uncertainty from input and algorithm-parameter errors, evaluated using UAVSAR and SMAPVEX-12 measurements. Informs our SME2 retrieval-uncertainty evaluation.

- Pachepsky and Hill (2017), [“Scale and scaling in soils”](https://doi.org/10.1016/j.geoderma.2016.08.017), *Geoderma*, 287, 4–30. Reviews spatial and temporal scaling, changes in measurement support, and methods including data assimilation and temporal stability. Provides a conceptual basis for relating satellite footprints, seismic sensitivity volumes, and point soil-moisture measurements to a common output grid.

- Yu et al. (2025), [“Spatial Soil Moisture Prediction From In Situ Data Upscaled to Landsat Footprint: Assessing Area of Applicability of Machine Learning Models”](https://doi.org/10.1109/TGRS.2025.3565818), *IEEE TGRS*, 63. The study combines machine learning and spatiotemporal fusion to upscale in situ soil moisture to the Landsat footprint, showing that predictions within the models’ area of applicability have lower uncertainty. Provides an area-of-applicability assessment to identify where upscaled soil moisture predictions are more reliable.

- Kalaiselvi et al. (2026), [“Air quality prediction using multi-source remote sensing data integration with hybrid deep learning framework”](https://www.nature.com/articles/s41598-025-32466-0), *Scientific Reports*, 16, 2688. Fuses satellite imagery, meteorological data, and ground observations using a CNN–BiLSTM model with attention and predictive uncertainty estimates. Offers a framework for learning complementary spatial and temporal patterns across heterogeneous environmental observations while quantifying predictive uncertainty.

- Agata et al. (2025), [“Physics-informed deep learning quantifies propagated uncertainty in seismic structure and hypocenter determination”](https://www.nature.com/articles/s41598-024-84995-9), *Scientific Reports*, 15, 1846. Uses physics-informed neural network ensembles to estimate seismic velocity structure and propagate its uncertainty into earthquake hypocenter estimates, reducing bias and uncertainty underestimation. Provides an example of combining seismic data with physical constraints to quantify uncertainty in velocity estimates.

- Chao et al. (2026), [“A two-stage data fusion framework for multi-source precipitation estimates integrating gauge, radar, and satellite observations via deep learning and Bayesian model averaging”](https://doi.org/10.1016/j.jhydrol.2026.135764), *Journal of Hydrology*, 677, 135764. Combines 3D-CNN–ConvLSTM correction with Bayesian model averaging to fuse gauge, radar, and satellite precipitation into calibrated predictive distributions, evaluated at daily, hourly, and half-hourly resolutions. Evaluates probabilistic calibration and estimation performance across temporal resolutions and gauge densities.

- Zheng et al. (2026), [“A Bayesian INLA-SPDE approach to spatio-temporal point-grid fusion with change-of-support and misaligned covariates”](https://doi.org/10.1016/j.spasta.2026.100998), *Spatial Statistics*, 74, 100998. Uses a latent Gaussian field and source-specific observation operators to fuse point measurements and grid averages while accounting for differing spatial supports, temporal dependence, and measurement errors; demonstrates daily soil moisture mapping with uncertainty in Scotland. Offers a Bayesian framework for handling change of support and covariate misalignment with computationally efficient inference and uncertainty quantification.

- GAIA HazLab, [“GAIA Digital Twin of Soil”](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/), project documentation. Describes a coupled 90 m soil reanalysis for the Pacific Northwest that assimilates ground sensors and satellite observations to estimate water table depth, soil moisture, and near-surface stiffness. Provides the broader application context for this pilot, linking NISAR SME2 surface soil-moisture estimates to coupled soil states at resolutions required by downstream hazard models.

- Denolle Lab, [“codameter”](https://github.com/Denolle-Lab/codameter), research software. Quantifies uncertainty in seismic dv/v measurements and propagates it into physical interpretations. For this project's multimodal fusion, we assume codameter supplies the seismic dv/v input with measurement uncertainties and covariance, to be propagated alongside NISAR SME2 uncertainty into fused soil state.

- Bensen et al. (2007), [“Processing seismic ambient noise data to obtain reliable broad-band surface wave dispersion measurements”](https://doi.org/10.1111/j.1365-246X.2007.03374.x), *Geophysical Journal International*, 169(3), 1239–1260. Describes ambient-noise preprocessing, cross-correlation and temporal stacking, surface-wave dispersion measurement, and quality control, using temporal repeatability to estimate measurement uncertainty. Provides methodological background for this project's seismic processing and uncertainty assessment.
