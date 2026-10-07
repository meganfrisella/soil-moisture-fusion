# Literature review

Summaries of the works listed in the README. Each entry covers the research question, data, code, methodology, evaluation, and results.

**Access status:** Full text was available for all six papers, including the supplied [Chao PDF](chao.pdf); GAIA is project documentation. “No repository identified” means none was identified in the reviewed article, not that no code exists.

## Yu et al. (2025)

[“Spatial Soil Moisture Prediction From In Situ Data Upscaled to Landsat Footprint: Assessing Area of Applicability of Machine Learning Models”](https://doi.org/10.1109/TGRS.2025.3565818), *IEEE TGRS*, 63. [Full text](https://smartdigiag.com/downloads/journal/malone2025_4.pdf).

### Research question

Can a clustered monitoring network support daily soil moisture maps beyond its station locations, and how can the reliable geographic extent of a trained model be identified? The study addresses both point-to-pixel mismatch and uncertainty when extrapolating into unfamiliar landscapes.

### Data sources

Twenty-eight [OzNet](https://www.oznet.org.au/) stations provide near-surface soil moisture for 2016–2021 in the Yanco agricultural region, Australia. The main domain is 100 × 100 km; an extended 300 × 300 km domain tests broader applicability.

Predictors include MODIS/Landsat albedo, vegetation indices, land-surface temperature and evapotranspiration; ANUClimate meteorology; elevation; and Australian soil properties. Independent references comprise [PRISM field campaigns](https://prism.monash.edu/index.html), CosmOz, OzFlux, and SMAP retrievals. The [experimental archive](https://doi.org/10.25919/dhxv-nz94) provides supporting data.

### Code repository

[OzNet_AOA](https://github.com/yuyi13/OzNet_AOA), with a [Zenodo archive](https://doi.org/10.5281/zenodo.15290777), contains R code for an executable AOA demonstration and scripts documenting fusion, prediction, validation, and figures. The demonstration trains XGBoost, computes dissimilarity, and maps applicability. The repository describes the broader experimental and figure scripts as requiring additional data and preparation.

### Technical methodology

ESTARFM fuses MODIS/Landsat reflectance predictors; ubESTARFM adds local bias correction for temperature. These supply daily predictors at approximately 100 m resolution. Random forests and XGBoost then learn relationships between station moisture and environmental covariates.

The area of applicability (AOA) uses distances between standardized predictor combinations, weighted by model feature importance. A dissimilarity index compares prediction locations with training conditions; an outlier-adjusted threshold defines the applicability boundary. This is a diagnostic of extrapolation risk, with interpretation tied to the validation design.

### Evaluation techniques

Fourfold spatial validation holds out seven stations per fold during 2016–2019. Cross-cluster validation transfers between two station clusters. Testing during 2020–2021 adds temporal separation. Independent observations assess spatial patterns, time series, bias, and unbiased error; evaluation also compares conditions inside and outside the AOA.

### Results

RF/XGBoost AOAs cover 43.1%/41.5% of the original domain. RF correlations are 0.62–0.64 against field campaigns, 0.84–0.91 against monitoring networks, and 0.87 against SMAP during cross-validation. Predictions within the AOA have lower errors. The main contribution is a mapped boundary on where model performance can reasonably transfer; applicability remains dependent on the training network and represented environmental conditions.

## Kalaiselvi et al. (2026)

[“Air quality prediction using multi-source remote sensing data integration with hybrid deep learning framework”](https://www.nature.com/articles/s41598-025-32466-0), *Scientific Reports*, 16, 2688.

### Research question

Can a common architecture combine satellite imagery, weather, and monitoring stations to improve daily PM₂.₅, PM₁₀, NO₂, and O₃ predictions, including transfer between cities and estimates of predictive uncertainty?

### Data sources

The study covers Delhi, Los Angeles, and Beijing during 2019–2023. Satellite inputs are [Sentinel-5P](https://developers.google.com/earth-engine/datasets/catalog/sentinel-5p), [MODIS MAIAC aerosol optical depth](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MCD19A2_GRANULES), and [Landsat-8 surface reflectance](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC08_C02_T1_L2), with [ERA5](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_DAILY) meteorology. Ground observations come from [CPCB](https://app.cpcbccr.com/ccr/), [SCAQMD](https://www.aqmd.gov/home/air-quality/air-quality-data-studies/historical-data-by-year), and [Beijing's monitoring center](https://www.bjmemc.com.cn/).

These sources contribute different information: atmospheric composition, aerosol loading, surface conditions, weather, and locally measured pollutant concentrations. Their original spatial supports differ substantially.

### Code repository

No repository identified in the article. The described implementation uses TensorFlow/Keras and NVIDIA V100 hardware; public input datasets alone do not reproduce the complete preprocessing and training workflow.

### Technical methodology

MAST-Net aligns inputs to daily 1 km grids through interpolation and aggregation, with quality filtering and gap filling. Parallel CNN branches extract spatial features, a bidirectional LSTM represents temporal dependencies, and multi-head attention combines information across sources. Feature selection uses mutual information and gradient-based importance.

Predictive uncertainty is estimated through stochastic dropout, independently trained model ensembles, and quantile regression. These approaches respectively vary active network connections, fitted models, and predicted distributional quantiles. A common output grid should be understood separately from the native resolving power of each input.

### Evaluation techniques

The paper reports an 80/10/10 training/validation/test split, fivefold cross-validation, component ablations, and transfer between cities with and without local fine-tuning. Baselines include CNN–LSTM, CNN–LSTM with attention, random forests, support-vector regression, and linear regression. Metrics include RMSE, MAE, and R².

The split description does not establish explicit spatial or temporal blocking, leaving the independence of nearby samples unclear.

### Results

[Table 3](https://www.nature.com/articles/s41598-025-32466-0/tables/3) reports PM₂.₅ RMSE of 8.2 µg/m³ versus 11.6 for CNN–LSTM, and NO₂ RMSE of 6.7 versus 9.8. MAST-Net R² ranges from 0.87 for O₃ to 0.94 for NO₂. The text reports cross-city R² of 0.79–0.87 and smaller transfer losses after fine-tuning. The findings support the value of multimodal and temporal modeling, while reproducibility and validation independence need closer examination before interpreting generalization claims.

## Agata et al. (2025)

[“Physics-informed deep learning quantifies propagated uncertainty in seismic structure and hypocenter determination”](https://www.nature.com/articles/s41598-024-84995-9), *Scientific Reports*, 15, 1846.

### Research question

How can uncertainty in a seismic velocity model be represented as a distribution and carried into subsequent inversions? The central issue is that an apparently precise downstream estimate can be misleading when it assumes a single, perfectly known velocity structure.

### Data sources

The study uses 14,146 manually picked first-arrival times from the KI03 active-source seismic survey near the Nankai Trough, Japan. TK5 reflection data constrain subsurface interfaces. Nine DONET stations supply P-wave arrivals from the 2016 Mw 5.9 earthquake.

Survey records are available through the [JAMSTEC Seismic Survey Database](https://www.jamstec.go.jp/obsmcs_db/e/), and bathymetry through [DARWIN](https://www.godac.jamstec.go.jp/darwin/en/). Collaborators supplied the processed arrival picks, so access to raw recordings and access to the exact inversion inputs are distinct reproducibility requirements.

### Code repository

No repository identified in the article. The methods describe network training and inference, but a directly linked, complete implementation was not located.

### Technical methodology

Separate neural networks represent continuous seismic velocity and travel-time fields. The eikonal equation constrains wavefront propagation, connecting the estimated velocity field to observed travel times. Function-space particle variational inference produces an ensemble of 256 velocity models representing posterior uncertainty.

The downstream inversion averages likelihood contributions over this ensemble. In statistical terms, uncertainty in the velocity field is marginalized: several plausible structures contribute to the distribution of the inferred quantity. Pretrained travel-time networks make these repeated forward calculations tractable. The demonstration uses 128 particles for source location and also propagates velocity uncertainty into reflector depths.

### Evaluation techniques

The authors compare inversions that include or omit velocity uncertainty, compare recovered structures with earlier tomography, and assess neural travel times against fast marching calculations. Evaluation examines posterior spread, changes in inferred depth, and the probability of association with candidate fault interfaces.

These comparisons establish the consequences of the uncertainty assumptions; they do not supply an independently known true earthquake location.

### Results

Including propagation gives a source depth of 10.81 km with a standard deviation of 0.68 km and changes estimated plate-boundary association probability from 8% to 35%. The downstream ensemble inversion takes approximately 11 minutes on 128 CPU cores after network training.

The study demonstrates practical uncertainty propagation through seismic inversion, but assumes a two-dimensional velocity profile extended uniformly along strike. Picking-error assumptions, forward-model approximation, and unrepresented three-dimensional structure limit how completely the ensemble describes uncertainty.

## Chao et al. (2026)

[“A two-stage data fusion framework for multi-source precipitation estimates integrating gauge, radar, and satellite observations via deep learning and Bayesian model averaging”](https://doi.org/10.1016/j.jhydrol.2026.135764), *Journal of Hydrology*, 677, 135764. [Full text](chao.pdf).

### Research question

Can separating deterministic spatiotemporal correction from probabilistic calibration improve precipitation estimates, prediction intervals, and performance under sparse gauge coverage? The study tests a modular fusion framework across daily, hourly, and half-hourly resolutions.

### Data sources

The Tunxi River basin in China supplies 45 gauges: 18 from hydrological yearbooks and 27 from the Xin’anjiang experimental watershed. Only the latter support half-hourly evaluation. Huangshan radar provides reflectivity-derived precipitation; satellite inputs are [bias-corrected CMORPH](https://www.ncei.noaa.gov/products/climate-data-records/precipitation-cmorph) and [GPM IMERG V07 Final](https://disc.gsfc.nasa.gov/datasets/GPM_3IMERGDF_07/summary).

May–September 2019–2020 supports training and BMA calibration; 2022 supports evaluation. Gauge and radar records are institutionally restricted. Satellite products are public and already contain upstream gauge adjustments.

### Code repository

[hou-1915/code](https://github.com/hou-1915/code) provides the core Python implementation of 3D-CNN–ConvLSTM correction, model selection, and expectation–maximization-based Bayesian model averaging. Users must prepare NumPy inputs; the restricted local observations prevent complete reproduction from public inputs alone.

### Technical methodology

Radar processing includes quality filtering, a reflectivity–rainfall relation, temporal accumulation, and gauge-based bias correction. Gridded products are aligned, normalized using training-period statistics, and sampled into spatial/temporal cubes around gauges.

The first stage combines 3D convolution with ConvLSTM to correct precipitation using spatial patterns and temporal evolution. Its loss equally weights correlation and Kling–Gupta efficiency, balancing association with mean and variability agreement.

The second stage combines corrected members through a Gaussian-mixture BMA distribution. Box–Cox transformation addresses skewness; expectation–maximization estimates member weights and variances. BMA is calibrated on training-period predictions and then applied to 2022. Weights are global within each temporal-scale/network-density setting.

### Evaluation techniques

Baselines include CNN, LSTM, Transformer, standalone BMA, and quantile LightGBM. Hourly fivefold spatial validation excludes approximately nine stations per fold. Further experiments remove sources, vary gauge counts from 18 to 45, and examine a heavy-rainfall event.

Metrics include correlation, RMSE, bias, KGE, critical success index, false alarm ratio, CRPS, and 95% interval coverage, width, and coverage error. Loss weighting is selected using 2022, limiting its independence as a test year. Upstream gauge adjustment also prevents assuming complete independence between satellite predictors and gauge references.

### Results

Daily correlation reaches 0.994, RMSE 1.685 mm/day, and KGE 0.972; radar alone gives 0.942, 5.231 mm/day, and 0.750. Hourly spatial validation gives correlation 0.894 and RMSE 0.658 mm/hour.

DBMPF improves interval calibration and event detection, with diminishing gains as gauge density increases. Radar contributes the dominant information; satellites provide complementary coverage. Uncertainty grows with rainfall intensity, and performance remains constrained for very light and extreme rainfall. The paper does not directly test benefits in a rainfall–runoff model.

## Zheng et al. (2026)

[“A Bayesian INLA-SPDE approach to spatio-temporal point-grid fusion with change-of-support and misaligned covariates”](https://doi.org/10.1016/j.spasta.2026.100998), *Spatial Statistics*, 74, 100998. [Published full text](https://eprints.gla.ac.uk/386764/1/386764.pdf).

### Research question

How can observations of the same process on point and grid supports be fused when covariates are also spatially misaligned? The objective is joint prediction with uncertainty that includes observation error, latent spatial/temporal variation, and uncertain covariate values.

### Data sources

The Elliot Water catchment, Scotland, has [SEPA](https://www2.sepa.org.uk/sensornet) measurements from 22 soil stations and 10 rain gauges. Soil temperature is colocated with soil moisture; rainfall is spatially misaligned. Daily 1 km [Copernicus soil-water index](https://land.copernicus.eu/global/products/ssm) contributes gridded information, and [Open Elevation](https://open-elevation.com/) supplies a fixed covariate.

The illustrated next-day prediction uses May 6–15, 2022. In-situ volumetric water content and satellite soil-water index are standardized separately, giving a common analysis scale despite their different original definitions.

### Code repository

[STDF-COSP](https://github.com/weiyue-zheng/STDF-COSP) supplies R simulation code and data links. The documented workflow generates latent fields and observations, fits point-only, grid-only, and joint models, and evaluates withheld spatial locations. Dependencies include INLA, rSPDE, and inlabru. The README's example is configured for three time points.

### Technical methodology

A Bayesian hierarchy represents continuous spatial fields using Matérn-SPDE priors on triangular meshes, with AR(1) temporal dependence. Source-specific operators evaluate fields at points or average them over grid cells; source-specific errors allow different observation precision.

Misaligned covariates are themselves latent spatiotemporal fields, estimated jointly with the response. This carries uncertainty from incomplete covariate observations into prediction. INLA approximates posterior marginals efficiently using the sparse Gaussian Markov representation.

The statistical meaning of a cell average is retained through its observation operator. Producing predictions on a finer mesh therefore does not turn a coarse observation into multiple independent fine-scale measurements.

### Evaluation techniques

One hundred simulation replicates compare point-only, grid-only, and joint models while varying available history—3, 7, or 10 time points—and covariate availability. Tests include unobserved locations and next-day prediction. Parameter RMSE and predictive errors assess estimation; application comparisons add 95% interval coverage, mean width, and interval score.

A simplified baseline treats rainfall as a fixed covariate instead of a jointly inferred field.

### Results

Fusion generally improves simulated prediction, although improvements vary across parameters. In the application, the joint model achieves 0.90 coverage for nominal 95% intervals versus 0.40 for the fixed-rainfall baseline. Interval score improves from 9.36 to 3.91, while interval width increases from 0.68 to 2.42 and RMSE worsens from 0.62 to 0.84.

These standardized-scale results demonstrate a tradeoff between point accuracy and calibrated uncertainty. The sparse network, short application window, and prior sensitivity constrain generalization beyond the evaluated setting.

## GAIA HazLab — Digital Twin of Soil

[GAIA Digital Twin of Soil](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/), evolving project documentation.

### Research question

How can observations with different footprints, depths, and sampling rates constrain coupled water-table depth, soil moisture, and near-surface stiffness? The intended product is a regional soil reanalysis for flood, landslide, and liquefaction applications, with explicit uncertainty and spatial-support accounting.

### Data sources

The [input catalog](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/01-input-data.html) describes NWIS wells, USGS streamflow, SNOTEL moisture/snow measurements, and UW/CC seismic observations. Terrain, SOLUS100 soils, and geology supply static information; precipitation and meteorological products drive temporal changes.

The documentation distinguishes assimilated observations, model forcing, evaluation data, and planned streams. Wells require screening for the aquifer interval they measure: a confined head measurement cannot automatically be treated as the water-table surface. Seismic dv/v represents a sampled subsurface volume whose support depends on station geometry and coda sensitivity.

### Code repository

[gwl-space-time-smooth](https://github.com/gaia-hazlab/gwl-space-time-smooth) contains data retrieval, physical models, assimilation, diagnostics, and documentation. It is an evolving implementation; the [assimilation chapter](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/04-assimilation.html) explicitly distinguishes the current snapshot update from a planned cycling filter with groundwater storage as the primary state.

### Technical methodology

A 90 m structural description combines terrain and soil properties with dynamic water-budget and hydromechanical models. A Gaussian prior on state anomalies supplies spatial covariance.

Observation operators map the common state to each measurement's support: points, satellite pixel averages, seismic sensitivity volumes, or basin-integrated quantities. A linear-Gaussian update combines prior and observation precision and returns posterior covariance. The current reduced groundwater update operates on water-table-head anomalies.

Temporal mismatch is represented with exponentially decaying correlation. Older observations have reduced sensitivity to the present state and additional evolution uncertainty. Distinct correlation times allow soil moisture and groundwater to respond differently. Covariance ranges and observation-error values are treated as provisional calibration parameters.

### Evaluation techniques

The [evaluation chapter](https://gaia-hazlab.github.io/gwl-space-time-smooth/twin/05-state-evaluation.html) separates independent tests, shared-forcing comparisons, and synthetic demonstrations. Observations are compared at their native support. Diagnostics include posterior variance reduction, information gain, averaging-kernel width, and degrees of freedom for signal. These distinguish reduced uncertainty from the ability to localize a spatial feature.

### Results

The repository produces evolving 90 m fields and uncertainty budgets. It reports domain-mean moisture correlation around 0.94 against another field sharing its forcing, explicitly interpreted as a consistency check. Sensitivity and observability diagnostics illustrate the complementary contributions of ground and seismic networks.

The 90 m output spacing is a representation choice; actual resolving power depends on observation footprints and priors. A fully cycling storage-based assimilation system remains a stated development target, so the documentation should be read as a combination of implemented methods, demonstrations, and proposed extensions.

## Bensen et al. (2007)

[“Processing seismic ambient noise data to obtain reliable broad-band surface wave dispersion measurements”](https://doi.org/10.1111/j.1365-246X.2007.03374.x), *Geophysical Journal International*, 169(3), 1239–1260. [Full text](https://academic.oup.com/gji/article/169/3/1239/626431).

### Research question

Which processing choices recover reliable broadband surface-wave dispersion from ambient seismic noise, and how can measurement uncertainty be estimated automatically? The paper develops a complete workflow in which data selection and repeatability assessment accompany waveform processing.

### Data sources

Examples use continuous broadband records from North America, Europe, and New Zealand, acquired through [IRIS DMC](https://ds.iris.edu/ds/nodes/dmc/), [ORFEUS](https://www.orfeus-eu.org/data/), and [GeoNet](https://www.geonet.org.nz/data/types/seismic_waveforms). Records span daily segments and longer stacks, including year-long station-pair correlations. The examples cover different noise environments and station geometries, allowing processing choices to be assessed across observational settings.

### Code repository

No repository identified in the article. The paper provides an algorithmic processing sequence and selection criteria; reproducing a particular example also requires its station/channel choices, dates, frequency bands, and instrument responses.

### Technical methodology

Single-station preparation removes instrument response, mean and trend, then applies bandpass filtering. Running-absolute-mean normalization downweights transient high amplitudes; spectral whitening reduces spectral imbalance. Temporal weights can be tuned to the earthquake frequency band.

Daily station-pair cross-correlations are stacked into longer records. Frequency–time analysis extracts group and phase dispersion; phase-matched filtering helps isolate coherent arrivals.

Conceptually, stacking combines repeated estimates of an interstation response, while dispersion analysis examines how propagation varies with frequency. Because normalization changes waveform amplitudes nonlinearly, processing order and parameter choices form part of the measurement definition.

### Evaluation techniques

The authors compare alternative normalization schemes, spectral signal-to-noise ratios, earthquake waveforms, and measurements along similar paths. Seasonal three-month stacks test stability under changing ambient-noise illumination; variation among acceptable dispersion curves estimates measurement error.

This validation asks whether a result survives changes in observation conditions. Repeatability provides evidence about measurement stability, while comparisons with other paths and earthquake records supply complementary checks.

### Results

Running-absolute-mean normalization provides effective earthquake suppression with adaptable weighting. Reliable group-speed measurements generally require SNR above 10 and interstation spacing of at least three wavelengths. Seasonal variability supports empirical error estimates; SNR-based proxies help when records are too short for temporal subsetting.

The main outcome is a reproducible measurement and quality-control strategy rather than a single predictive score. Thresholds depend on noise conditions and available record length. At low SNR, inferred errors rise nonlinearly, emphasizing the need to assess the stability of dispersion measurements as well as correlation amplitude.
