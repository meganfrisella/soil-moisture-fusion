# Literature review

## Lal et al. (2024)

[“Uncertainty estimates in the NISAR high-resolution soil moisture retrievals from multi-scale algorithm”](https://doi.org/10.1016/j.rse.2024.114288), *Remote Sensing of Environment*, 311, 114288. [Publisher abstract](https://www.sciencedirect.com/science/article/pii/S0034425724003067); [manuscript sections](https://www.sciencedirect.com/science/article/am/pii/S0034425724003067).

- **Research question:** Spatially and temporally varying uncertainty in NISAR multiscale retrievals
- **Data sources:** UAVSAR/SMAPVEX-12, Carman, Manitoba, June–July 2012; ERA5-Land moisture; campaign ground measurements
- **Method:**
  - Analytical variance propagation: coarse moisture, SAR backscatter, retrieval parameters
  - Sensitivity experiments; assumed independence between SAR and coarse-input errors
- **Evaluation:** Crop-wise predicted uncertainty versus retrieval unbiased RMSE
- **Key result:** Coarse-input error a major contributor; predicted uncertainty generally above observed error
- **Code:** No repository identified in reviewed material
- **Takeaways:** Separate native retrieval error from gridding/depth-conversion error; track shared input errors
- **Limitations:** Prelaunch agricultural demonstration; no local calibration or validation of every SME2 candidate; independence assumption needs testing

## Pachepsky and Hill (2017)

[“Scale and scaling in soils”](https://doi.org/10.1016/j.geoderma.2016.08.017), *Geoderma*, 287, 4–30. [Publisher abstract and section excerpts](https://www.sciencedirect.com/science/article/pii/S0016706116303640); [USDA publication record and summary](https://www.ars.usda.gov/research/publications/publication/?seqNo115=325208).

- **Research question:** Transfer of soil information across spatial and temporal scales
- **Data sources:** Published soil studies; no new observational dataset
- **Method:** Dimensional analysis, scaling laws, changes in measurement support; assimilation, temporal stability, hydraulic-parameter scaling
- **Evaluation:** Conceptual synthesis; individual validation protocols unavailable in reviewed excerpts
- **Key result:** Framework for relating observations and processes across scales
- **Code:** No repository identified in accessible material
- **Takeaways:** Define support for satellite cells, point sensors, and model states; distinguish grid spacing from resolving power
- **Limitations:** Abstract/excerpts reviewed; no operational SME2 algorithm or calibrated uncertainty model

## Yu et al. (2025)

[“Spatial Soil Moisture Prediction From In Situ Data Upscaled to Landsat Footprint: Assessing Area of Applicability of Machine Learning Models”](https://doi.org/10.1109/TGRS.2025.3565818), *IEEE TGRS*, 63. [Full text](https://smartdigiag.com/downloads/journal/malone2025_4.pdf).

- **Research question:** Daily moisture prediction beyond station locations; geographic limits of model applicability
- **Data sources:**
  - [OzNet](https://www.oznet.org.au/), Yanco, Australia, 2016–2021; MODIS/Landsat predictors, ANUClimate, terrain and soils
  - [PRISM](https://prism.monash.edu/index.html), CosmOz, OzFlux, SMAP references; [experimental archive](https://doi.org/10.25919/dhxv-nz94)
- **Method:** ESTARFM/ubESTARFM predictor fusion; RF/XGBoost moisture prediction; feature-weighted dissimilarity defining area of applicability (AOA)
- **Evaluation:** Spatial and cross-cluster holdouts during 2016–2019; temporal testing during 2020–2021; independent reference comparisons
- **Key result:** Lower errors inside the AOA; applicability limited to represented environmental conditions
- **Code:** [OzNet_AOA](https://github.com/yuyi13/OzNet_AOA), [Zenodo](https://doi.org/10.5281/zenodo.15290777); executable AOA example; broader workflow requires additional inputs
- **Takeaways:** Close daily mapping analogue; spatial/temporal holdouts and extrapolation diagnostics
- **Limitations:** Ground-training and covariate dependence; AOA neither a predictive interval nor proof of SME2 subpixel detail

## Kalaiselvi et al. (2026)

[“Air quality prediction using multi-source remote sensing data integration with hybrid deep learning framework”](https://www.nature.com/articles/s41598-025-32466-0), *Scientific Reports*, 16, 2688.

- **Research question:** Multisource daily air-quality prediction with transferability and uncertainty
- **Data sources:** Delhi, Los Angeles, Beijing, 2019–2023; Sentinel-5P, MODIS, Landsat-8, ERA5, regulatory stations
- **Method:**
  - Common-grid interpolation and temporal aggregation; CNN–BiLSTM with attention
  - Dropout, model ensembles, quantile regression for predictive uncertainty
- **Evaluation:** Train/validation/test split, cross-validation, ablations, cross-city transfer; RMSE, MAE, R²
- **Key result:** Reported improvements over tested baselines and benefits from local fine-tuning
- **Code:** No repository identified in article
- **Takeaways:** Architectural example for complementary spatial/temporal predictors and uncertainty estimation
- **Limitations:** Air-quality application; unclear spatial/temporal blocking; common-grid resampling does not resolve support mismatch or validate soil-moisture uncertainty

## Agata et al. (2025)

[“Physics-informed deep learning quantifies propagated uncertainty in seismic structure and hypocenter determination”](https://www.nature.com/articles/s41598-024-84995-9), *Scientific Reports*, 15, 1846.

- **Research question:** Propagation of uncertain seismic velocity structure into downstream inversions
- **Data sources:** KI03/TK5 surveys near Nankai Trough; DONET arrivals from the 2016 earthquake; [JAMSTEC](https://www.jamstec.go.jp/obsmcs_db/e/), [DARWIN](https://www.godac.jamstec.go.jp/darwin/en/)
- **Method:**
  - Eikonal-constrained neural fields; particle variational inference for velocity ensembles
  - Likelihood marginalization over velocity uncertainty for hypocenter/reflector estimation
- **Evaluation:** With/without propagation; earlier tomography; fast-marching travel times
- **Key result:** Propagation changes inferred source depth and plate-boundary association
- **Code:** No repository identified; exact processed arrival picks supplied by collaborators
- **Takeaways:** Carry upstream parameter/field uncertainty into downstream soil-state inference
- **Limitations:** Seismic inversion rather than moisture fusion; simplified geometry and error assumptions; no independently known hypocenter truth

## Chao et al. (2026)

[“A two-stage data fusion framework for multi-source precipitation estimates integrating gauge, radar, and satellite observations via deep learning and Bayesian model averaging”](https://doi.org/10.1016/j.jhydrol.2026.135764), *Journal of Hydrology*, 677, 135764. [Publisher abstract](https://www.sciencedirect.com/science/article/abs/pii/S0022169426008619).

- **Research question:** Separate deterministic correction and probabilistic calibration for precipitation fusion
- **Data sources:** Tunxi basin, China; gauges, Huangshan radar, CMORPH, IMERG; May–September 2019–2020 training/calibration, 2022 evaluation
- **Method:** 3D-CNN–ConvLSTM correction; Box–Cox transformation and Gaussian-mixture Bayesian model averaging (BMA)
- **Evaluation:** Model baselines, spatial holdouts, source ablations, gauge-density experiments; point errors, event detection, CRPS, interval coverage/width
- **Key result:** Better point estimates and interval calibration; diminishing gains with denser gauge coverage
- **Code:** [hou-1915/code](https://github.com/hou-1915/code); core workflow available, local gauge/radar inputs restricted
- **Takeaways:** Evaluate accuracy and calibration separately across temporal scales and observation densities
- **Limitations:** Precipitation-specific model; test-year loss selection and upstream gauge adjustment weaken independence; BMA weights global within each setting

## Zheng et al. (2026)

[“A Bayesian INLA-SPDE approach to spatio-temporal point-grid fusion with change-of-support and misaligned covariates”](https://doi.org/10.1016/j.spasta.2026.100998), *Spatial Statistics*, 74, 100998. [Published full text](https://eprints.gla.ac.uk/386764/1/386764.pdf).

- **Research question:** Point–grid fusion with differing supports and uncertain, misaligned covariates
- **Data sources:** Elliot Water, Scotland; SEPA moisture/temperature and rain gauges; daily Copernicus soil-water index, elevation; May 6–15, 2022 application
- **Method:**
  - Matérn-SPDE latent fields, AR(1) time dependence, INLA inference
  - Point/cell-average observation operators; source-specific errors; jointly inferred covariate fields
- **Evaluation:** Synthetic missing-data/holdout experiments; point-only, grid-only, joint models; next-day prediction and interval calibration
- **Key result:** Better interval calibration than fixed-rainfall baseline, at the cost of wider intervals and higher point error
- **Code:** [STDF-COSP](https://github.com/weiyue-zheng/STDF-COSP); R simulations with INLA, rSPDE, inlabru
- **Takeaways:** Constrain the fine field at native SME2 support; propagate covariate uncertainty
- **Limitations:** Short, standardized-scale application; stationary/separable dependence and prior sensitivity; no validated local volumetric or depth conversion

## Peng et al. (2017)

[“A review of spatial downscaling of satellite remotely sensed soil moisture”](https://doi.org/10.1002/2016RG000543), *Reviews of Geophysics*, 55, 341–366.

- **Research question:** Assumptions and tradeoffs of satellite soil-moisture downscaling
- **Data sources:** Published microwave, optical/thermal, terrain, and model studies
- **Method:** Taxonomy of satellite fusion, geoinformation-based, and model-based approaches; statistical and evaporation-based methods
- **Evaluation:** Synthesis of published validations with differing inputs and reference supports
- **Key result:** Potential spatial gains; no defensible universal ranking across unlike experiments
- **Code:** Methodological review; no implementation repository identified
- **Takeaways:** Choose justified predictors and scaling assumptions; distinguish inferred structure from resampling
- **Limitations:** Mostly much coarser inputs; no established gain for SME2's 200 m native spacing → 90 m target spacing

## Jones (1999)

[“First- and Second-Order Conservative Remapping Schemes for Grids in Spherical Coordinates”](https://doi.org/10.1175/1520-0493%281999%29127%3C2204%3AFASOCR%3E2.0.CO%3B2), *Monthly Weather Review*, 127, 2204–2210.

- **Research question:** Transfer between grids while preserving area integrals
- **Data sources:** Analytical fields on alternative spherical grids
- **Method:** Cell-overlap integration; piecewise-constant first-order and gradient-based second-order reconstruction
- **Evaluation:** Remapping accuracy on test fields
- **Key result:** More accurate second-order reconstruction while retaining conservation
- **Code:** Availability not verified; publisher-indexed methodology reviewed
- **Takeaways:** Area-overlap baseline from native EPSG:6933 cells to the target grid
- **Limitations:** No new subpixel information or temporal dynamics; water-storage conservation also requires layer thickness; masking and uncertainty propagation require explicit treatment

## Zhang et al. (2023)

[“Temporal Gap-Filling of 12-Hourly SMAP Soil Moisture Over the CONUS Using Water Balance Budgeting”](https://doi.org/10.1029/2023WR034457), *Water Resources Research*.

- **Research question:** Reconstruct moisture between satellite acquisitions using rainfall and drying behavior
- **Data sources:** SMAP, GPM IMERG, ground moisture, NLDAS precipitation; CONUS, April 2015–March 2021
- **Method:** Precipitation-driven water balance; hydrologic losses calibrated from observed drying periods; 12-hour output intervals
- **Evaluation:** Ground comparisons and rainfall-response tests versus linear interpolation and three-dimensional smoothing
- **Key result:** Better capture of rainfall-induced peaks, particularly during sparse observation periods
- **Code:** No repository identified in reviewed article
- **Takeaways:** Candidate daily reconstruction; event-response evaluation
- **Limitations:** Precipitation quality and sufficient drying observations essential; SME2 cadence may hinder calibration; parameter/forcing uncertainty and spatial detail unresolved

## Huang et al. (2022)

[“A Novel Fusion Method for Generating Surface Soil Moisture Data With High Accuracy, High Spatial Resolution, and High Spatio-Temporal Continuity”](https://doi.org/10.1029/2021WR030827), *Water Resources Research*.

- **Research question:** Daily, spatially complete soil moisture at field scale
- **Data sources:** Landsat, MODIS, SMAP Level-4, ERA5, HOAL ground network, Austria
- **Method:** GRASPS: ESTARFM predictor fusion → deep belief network → pixel-class bias correction; 30 m daily output grid
- **Evaluation:** April–October 2018 training; 2017 ground validation; comparisons with model-based products
- **Key result:** Improved ground agreement through fusion and bias correction
- **Code:** No repository identified in reviewed article
- **Takeaways:** Close analogue for combining spatial downscaling and daily estimation
- **Limitations:** Extensive predictors and ground training; local transfer, native-SME2 consistency, and propagated uncertainty unestablished

## Preimesberger et al. (2025)

[“ESA CCI Soil Moisture GAPFILLED: an independent global gap-free satellite climate data record with uncertainty estimates”](https://doi.org/10.5194/essd-17-4305-2025), *Earth System Science Data*, 17, 4305–4329.

- **Research question:** Satellite moisture gap filling with explicit uncertainty
- **Data sources:** Global ESA CCI, January 1991–December 2023, 0.25° grid; ground references, GLDAS, temperature and vegetation information
- **Method:** Discrete-cosine penalized smoothing (DCT-PLS) across space/time; separate frozen-period interpolation; combined retrieval/gap-filling uncertainty
- **Evaluation:** Ground comparisons; restoration of artificially masked model data
- **Key result:** Broad agreement retained; weaker performance under sparse coverage and complex conditions
- **Code:** [pytesmo](https://github.com/TUW-GEO/pytesmo), DCT-PLS implementation; [dataset](https://doi.org/10.48436/hcm6n-t4m35)
- **Takeaways:** Statistical baseline; distinguish original observations, predictions, and gap-filling uncertainty
- **Limitations:** Coarse-grid demonstration; smoothing can suppress extremes; ancillary temperature/vegetation still used for classification/uncertainty; local calibration needed

## Sabaghy et al. (2020)

[“Comprehensive analysis of alternative downscaled soil moisture products”](https://doi.org/10.1016/j.rse.2019.111586), *Remote Sensing of Environment*, 239, 111586.

- **Research question:** Relative downscaling performance against common reference data
- **Data sources:** SMAPEx-4/5 airborne measurements and OzNet stations, Yanco, Australia
- **Method:** Radar-, optical-, radiometer-, and oversampling-based products; coarse-input baselines
- **Evaluation:** Spatial patterns and temporal agreement against airborne maps and ground time series
- **Key result:** Rankings depend on reference and dimension evaluated; station-only validation insufficient for spatial gains
- **Code:** No repository identified in reviewed abstract/excerpts
- **Takeaways:** Common holdouts/baselines; evaluate spatial detail separately from temporal skill
- **Limitations:** Coarser products and different landscape; validating 90 m detail requires suitable spatial reference observations

## Liu, Xin, and Tian (2026)

[“A novel fusion framework for generating 30 m daily seamless soil moisture with reliable performance”](https://doi.org/10.1016/j.jhydrol.2026.135791), *Journal of Hydrology*, 677, 135791. This is an additional candidate for methodological comparison.

- **Research question:** Fine-grid daily moisture without ground training labels
- **Data sources:** MODIS, Harmonized Landsat–Sentinel, SMAP/Sentinel moisture, terrain, soils; Naqu/Maqu ground validation, Tibetan Plateau
- **Method:** MSRNet predictor super-resolution; SMSNet trained at an intermediate scale and applied to 30 m daily inputs
- **Evaluation:** Ground comparison and Naqu-to-Maqu transfer
- **Key result:** Reported improvement over input moisture without ground observations for training
- **Code:** No repository identified in reviewed abstract/excerpts
- **Takeaways:** Additional candidate where local training observations are insufficient
- **Limitations:** Cross-scale/landscape transfer needs testing; calibrated uncertainty and native-SME2 consistency unestablished; fine output spacing not independent fine-scale observations

## GAIA HazLab — Digital Twin of Soil

[GAIA Digital Twin of Soil](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/), evolving project documentation.

- **Research question:** Fusion across footprints, depths, and cadences for coupled soil-state estimation
- **Data sources:** NWIS wells, USGS streamflow, SNOTEL, seismic records; terrain, soils, geology, meteorological forcing; [input catalog](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/01-input-data.html)
- **Method:**
  - Terrain/soil structure, water-budget and hydromechanical models; Gaussian spatial priors
  - Point, pixel, volume, and basin observation operators; age-dependent sensitivity and evolution uncertainty
  - Current reduced update: snapshot water-table-head anomaly; storage-based cycling filter remains a target
- **Evaluation:** Native-support comparisons; uncertainty reduction, averaging kernels, observability; independent tests distinguished from model cross-checks and synthetic demonstrations
- **Key result:** Framework for heterogeneous assimilation; output spacing and variance reduction insufficient to establish resolving power
- **Code:** [gwl-space-time-smooth](https://github.com/gaia-hazlab/gwl-space-time-smooth); local [assimilation](../../../gwl-space-time-smooth/docs/twin/04-assimilation.qmd) and [evaluation](../../../gwl-space-time-smooth/docs/twin/05-state-evaluation.qmd) chapters checked
- **Takeaways:** Destination for SME2 constraints in the Pacific Northwest; propagate gridding and surface-to-root-zone uncertainty
- **Limitations:** Provisional covariance/error assumptions; planned versus implemented components; no independent proof of 90 m daily skill

## Denolle Lab — codameter

[codameter](https://github.com/Denolle-Lab/codameter), research software for measuring and interpreting relative seismic velocity change with propagated uncertainties. The repository identifies its companion framework manuscript as in preparation.

- **Research question:** Measurement uncertainty in seismic relative velocity change (dv/v)
- **Data sources:** Correlations/dv/v series, measurement errors, forcing/site information; synthetic examples and [Clements–Denolle data](https://doi.org/10.5281/zenodo.6413275)
- **Method:**
  - Within-configuration uncertainty plus between-configuration processing variability
  - Covariance for overlapping stacks/shared references; inversion of pairwise relative measurements
- **Evaluation:** [Seeded synthetic recovery](https://github.com/Denolle-Lab/codameter/blob/master/src/codameter/golden.py) with imposed velocity changes, noise, and decorrelation
- **Key result:** Ensemble means, standard deviations, and covariance for downstream inference
- **Code:** [codameter](https://github.com/Denolle-Lab/codameter), [measurement uncertainty module](https://github.com/Denolle-Lab/codameter/blob/master/src/codameter/uq_measurement.py)
- **Takeaways:** Assumed supplier of dv/v and covariance; propagate alongside SME2 errors into soil states
- **Limitations:** Research software, companion manuscript in preparation in reviewed documentation; calibration depends on ensemble/error assumptions; no local validation established here

## Bensen et al. (2007)

[“Processing seismic ambient noise data to obtain reliable broad-band surface wave dispersion measurements”](https://doi.org/10.1111/j.1365-246X.2007.03374.x), *Geophysical Journal International*, 169(3), 1239–1260. [Full text](https://academic.oup.com/gji/article/169/3/1239/626431).

- **Research question:** Reliable ambient-noise surface-wave dispersion and measurement uncertainty
- **Data sources:** Broadband records from North America, Europe, New Zealand; [IRIS](https://ds.iris.edu/ds/nodes/dmc/), [ORFEUS](https://www.orfeus-eu.org/data/), [GeoNet](https://www.geonet.org.nz/data/types/seismic_waveforms)
- **Method:** Instrument correction, temporal normalization, spectral whitening; daily correlation and stacking; frequency–time/phase-matched dispersion analysis
- **Evaluation:** Alternative processing, signal-to-noise, earthquake/path comparisons, seasonal repeatability
- **Key result:** Practical quality-control workflow; seasonal variability as an empirical uncertainty estimate
- **Code:** No repository identified in article
- **Takeaways:** Seismic preprocessing, stack selection, and repeatability-based error assessment
- **Limitations:** Dispersion rather than dv/v–moisture inference; thresholds depend on noise/path conditions; repeatability does not capture all systematic error
