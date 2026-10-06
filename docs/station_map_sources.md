# Odessa map: sources and method

Build with `python3 scripts/build_station_map.py`; add `--refresh` to query the
public catalogs. The script requires Python 3.10+ and, for refresh, `curl`.
The checked-in `data/station_map.json` contains the retrieval timestamp, query
URLs, normalized station metadata, original footprint coordinates, and a full
list of matching SME2 granule identifiers and metadata links. Default builds
use this snapshot without network requests. The HTML layout is embedded in the
builder; no template directory or existing HTML file is required. Opening the
HTML still requires internet access for Leaflet and Esri tiles.

The generated map is `outputs/station_map.html`, with its PNG at `outputs/station_map.png`;
`docs/` holds this methodology document, and `data/` holds the metadata inputs.

## Search area and station evidence

The reference point is **47.3332° N, 118.6882° W**, an approximate Odessa town
center. Coordinates use WGS84. The initial research identified soil stations
within 100 km of Odessa. That criterion was used only to identify sites;
the script refreshes the four selected soil stations by ID and encodes no
distance calculation or radius. The seismic station list is explicitly requested.

- **PNSN:** UW.SAW, UW.EPH2, UW.WOLL, UW.OD2, UW.LMONT, and UW.DAVN. Coordinates,
  elevation, sensor descriptions, sample rates, and component orientation come
  from [EarthScope FDSN StationXML](https://service.earthscope.org/fdsnws/station/1/).
  Only velocity/acceleration channels active at the snapshot date are shown;
  housekeeping channels are excluded. Cards link to each PNSN station page.
- **NRCS:** the initial search covered Washington AWDB networks for `SMS:*`
  elements; refresh queries the selected station's active soil elements.
  Depths come from station-specific
  metadata, converted from inches to centimeters. The initial result is
  [Lind #1, 2021:WA:SCAN](https://wcc.sc.egov.usda.gov/nwcc/site?sitenum=2021),
  approximately 37.3 km away, at 2, 4, 8, 20, and 40 inches.
- **NOAA:** the initial search of the [USCRN station catalog](https://www.ncei.noaa.gov/pub/data/uscrn/products/stations.tsv)
  identified the operational site
  [Spokane 17 SSW, WBAN 04136](https://www.ncei.noaa.gov/access/crn/station.htm?stationId=1467),
  approximately 88.4 km away. NOAA's catalog coordinates have 0.01° precision.
  The [network design](https://www.ncei.noaa.gov/access/crn/measurements.html)
  uses Hydra Probe II sensors at 5, 10, 20, 50, and 100 cm. These are nominal
  depths, not a claim of complete data: the
  [2026 daily file](https://www.ncei.noaa.gov/pub/data/uscrn/products/daily01/2026/CRND0103-2026-WA_Spokane_17_SSW.txt)
  had valid 5/10/20/100 cm values but missing 50 cm moisture on October 5.
- **WSU:** join the [AWN sensor inventory](https://weather.wsu.edu/awn-networks/stations)
  with its public station catalog for more precise coordinates. Require an
  explicit `soil_mois_8_in = Y` flag; normalize degrees west to negative
  longitude. This confirms [LaCrosse](https://weather.wsu.edu/weather/stations?location=LaCrosse),
  approximately 82.8 km away, with an 8-inch probe. The public inventory endpoint
  uses a temporary, anonymously issued token; tokens are not saved in the map.
- **USBR:** the requested [Odessa AgriMet station (ODSW)](https://www.usbr.gov/pn/agrimet/agrimetmap/odswda.html)
  is at 47.30888° N, 118.87861° W, approximately 14.6 km from Odessa.
  Its [station-specific instrumentation inventory](https://www.usbr.gov/pn/agrimet/aginfo/station_params.html#ODSW)
  confirms soil moisture in percent at 2, 8, 20, and 40 inches
  (5.08, 20.32, 50.8, and 101.6 cm), with parameter codes XSM2, XSM8,
  XSM20, and XSM40. These are moisture sensors, distinct from the separately
  listed soil temperature probes. The site was installed April 24, 1984;
  the inventory does not establish the moisture sensors' installation dates
  or model. Refresh parses the ODSW section and fails if moisture evidence
  or coordinates are missing.

This is a reproducible search of these published inventories, not a complete
survey of private or research sensors. AgriMet/weather stations without explicit
moisture-sensor evidence are excluded. AWN soil water potential (kPa) and soil
temperature are distinct variables; neither alone qualifies as volumetric soil
moisture here. The inventory may lag sensor upgrades. Sensor model or measurement
units are left unspecified when station metadata does not establish them.

## NISAR SME2 footprints

[NASA CMR](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html) is
queried for `NISAR_L3_SME2*`, every collection and date, with a point intersection
at Odessa. Pagination retrieves the complete result set. The initial snapshot
contains 61 catalog granules across tracks **005 ascending, 071 descending,
077 ascending, and 143 descending**; track 077 has two intersecting frames.

All distinct catalog polygons are retained, grouped by track/direction/frame.
The legend groups these footprints into four tracks. Hovering over a track in
the legend highlights all its frames and opens a card with each frame's date
span, collections, polarization, geographic bounds, and metadata links.
Hovering over footprints on the map highlights the intersecting tracks without
opening cards. Repeated outlines are acquisition footprints, not extra frames.
Granule counts include versions from multiple collections and are not counts of
unique acquisition dates. Original boundaries are used, not inferred orbit
lines or bounding boxes. The map fits the selected station coordinates with
padding; zoom out to inspect full footprints.

[SME2](https://nisar-docs.asf.alaska.edu/sme2/) is a Level 3 product with 200 m
grid spacing. Catalog overlap does not establish valid retrievals at Odessa:
water, urban, frozen/snow-covered, and other flagged pixels can be missing or
unreliable. Beta/provisional calibration and quality flags need evaluation
before fusion. An empty successful catalog search is displayed as zero results;
an unsuccessful refresh fails without silently replacing the saved snapshot.

## Display and validation

The basemap is [Esri World Topographic Map](https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer),
with provider attribution visible in the map. Stations and footprints are always
drawn; there are no layer toggles. The legend labels the station icon types,
Odessa reference point, and NISAR track numbers. Station hover highlights the
icon and label and opens its metadata card. Track cards open from legend hover,
tap, or keyboard focus; cards stay open for accessing links.

Run offline checks with `python3 -m unittest discover -s tests -v`.
The standalone HTML embeds the snapshot, so it works when opened directly from
disk as well as from a local HTTP server. GitHub sanitizes active HTML in README
files; the README therefore shows the PNG and links to the standalone HTML.
