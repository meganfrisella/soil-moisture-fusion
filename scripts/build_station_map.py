#!/usr/bin/env python3
"""Build the Odessa station map from saved metadata or refresh public catalogs.

Run from the repository root with Python 3.10+:
    python3 scripts/build_station_map.py
        Build outputs/station_map.html from data/station_map.json (offline).
    python3 scripts/build_station_map.py --refresh
        Update metadata for the selected stations and overlapping SME2 footprints,
        save data/station_map.json, and rebuild outputs/station_map.html.
    python3 scripts/build_station_map.py --help
        Show usage and options.

Use --snapshot PATH and --output PATH to override those files. The HTML layout
is embedded in this script; no template directory or existing HTML is needed.
PNG export is separate. Opening the HTML requires internet for Leaflet and Esri.

No third-party Python packages are required. Refresh needs curl and internet;
HTTPS certificate verification is enabled. Coordinates are WGS84. No observation
files or Earthdata credentials are needed.
"""

import argparse
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CENTER = {"name": "Odessa, WA", "latitude": 47.3332, "longitude": -118.6882}
STATIONS = ("SAW", "EPH2", "WOLL", "OD2", "LMONT", "DAVN")
# Sites identified during the initial research; refresh updates these sites by ID.
SOIL_STATIONS = ("AgriMet.ODSW", "2021:WA:SCAN", "AWN.300201", "USCRN.04136")
FDSN = "https://service.earthscope.org/fdsnws/station/1/query"
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations"
USCRN = "https://www.ncei.noaa.gov/pub/data/uscrn/products/stations.tsv"
AWN = "https://weather.wsu.edu/api/v1"
AGRIMET_SITE = "https://www.usbr.gov/pn/agrimet/agrimetmap/odswda.html"
AGRIMET_PARAMS = "https://www.usbr.gov/pn/agrimet/aginfo/station_params.html"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
SME2_DOCS = "https://nisar-docs.asf.alaska.edu/sme2/"
NS = {"s": "http://www.fdsn.org/xml/station/1"}


def url(base, **params):
    return base + "?" + urlencode(params)


def fetch(address):
    """Fail visibly on HTTP/network errors; never substitute invented coverage."""
    result = subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error",
         "--connect-timeout", "15", "--max-time", "90", "--retry", "2", address],
        capture_output=True, check=False,
    )
    if result.returncode:
        # Do not echo URLs: AWN public-session tokens may be in their queries.
        raise RuntimeError(f"HTTPS request failed: {result.stderr.decode().strip()}")
    return result.stdout


def fetch_json(address):
    return json.loads(fetch(address))


def point(identifier, name, kind, latitude, longitude, rows, links):
    return {
        "id": identifier, "name": name, "kind": kind,
        "latitude": float(latitude), "longitude": float(longitude),
        "rows": rows, "links": links,
    }


def seismic_stations(xml, as_of):
    stations = []
    for station in ET.fromstring(xml).findall(".//s:Station", NS):
        code = station.get("code")
        if code not in STATIONS:
            continue
        channels = []
        for channel in station.findall("s:Channel", NS):
            name = channel.get("code", "")
            # Velocity and accelerometer channels only; omit housekeeping data.
            if len(name) != 3 or name[1] not in "HN" or name[2] not in "ENZ123":
                continue
            if channel.get("startDate", "")[:10] > as_of:
                continue
            if channel.get("endDate", "9999")[:10] <= as_of:
                continue
            channels.append({
                "code": name, "location": channel.get("locationCode", ""),
                "sample_rate_hz": float(channel.findtext("s:SampleRate", "0", NS)),
                "sensor": channel.findtext("s:Sensor/s:Description", "Not supplied", NS),
                "azimuth_deg": channel.findtext("s:Azimuth", "Not supplied", NS),
                "dip_deg": channel.findtext("s:Dip", "Not supplied", NS),
                "depth_m": channel.findtext("s:Depth", "Not supplied", NS),
            })
        if not channels:
            raise ValueError(f"No seismic channels returned for UW.{code}")
        families = sorted({c["code"][:2] for c in channels})
        types = []
        if any(f in families for f in ("HH", "BH", "LH")):
            types.append("Broadband velocity")
        if any(f[1] == "N" for f in families):
            types.append("Strong-motion acceleration")
        if "EH" in families or "SH" in families:
            types.append("Short-period velocity")
        item = point(
            f"UW.{code}", station.findtext("s:Site/s:Name", code, NS), "seismic",
            station.findtext("s:Latitude", namespaces=NS),
            station.findtext("s:Longitude", namespaces=NS),
            [["Network", "UW · Pacific Northwest Seismic Network"],
             ["Instrumentation", "; ".join(types)],
             ["Elevation", station.findtext("s:Elevation", namespaces=NS) + " m"],
             ["Station epoch starts", station.get("startDate", "Unknown")[:10]]],
            [["PNSN station metadata", f"https://pnsn.org/station/{code.lower()}"],
             ["EarthScope StationXML", url(FDSN, net="UW", sta=code, level="channel", format="xml", endafter=as_of)]],
        )
        item["channels"] = channels
        stations.append(item)
    missing = set(STATIONS) - {s["id"].split(".")[1] for s in stations}
    if missing:
        raise ValueError(f"Missing requested seismic stations: {sorted(missing)}")
    return sorted(stations, key=lambda s: STATIONS.index(s["id"].split(".")[1]))


def awdb_stations(records, as_of):
    stations = []
    for station in records:
        elements = [e for e in station.get("stationElements", [])
                    if e["elementCode"] == "SMS" and e.get("heightDepth") is not None
                    and e.get("beginDate", "")[:10] <= as_of
                    and e.get("endDate", "9999")[:10] > as_of]
        if not elements:
            continue
        depths = sorted({abs(e["heightDepth"]) for e in elements})
        stations.append(point(
            station["stationTriplet"], station["name"], "soil",
            station["latitude"], station["longitude"],
            [["Network", f"NRCS {station['networkCode']}"],
             ["Measurement", "Volumetric soil moisture (SMS), % water by volume"],
             ["Sensor depths", ", ".join(f"{d:g} in ({d * 2.54:g} cm)" for d in depths)],
             ["Soil record begins", min(e["beginDate"] for e in elements)[:10]],
             ["Reporting intervals", ", ".join(sorted({e["durationName"] for e in elements}))],
             ["Elevation", f"{station['elevation']:g} ft"],
             ["Status", "Active soil elements in AWDB; measurement completeness not assessed"]],
            [["NRCS station metadata", url("https://wcc.sc.egov.usda.gov/nwcc/site", sitenum=station["stationId"])],
             ["AWDB sensor inventory", url(AWDB, stationTriplets=station["stationTriplet"], elements="SMS:*", returnStationElements="true")]],
        ))
    return stations


def uscrn_stations(tsv):
    stations = []
    for station in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if station["NETWORK"] != "USCRN" or station["OPERATION"] != "Operational":
            continue
        lat, lon = float(station["LATITUDE"]), float(station["LONGITUDE"])
        stations.append(point(
            "USCRN." + station["WBAN"], station["LOCATION"] + " " + station["VECTOR"], "soil",
            lat, lon,
            [["Network", "NOAA U.S. Climate Reference Network"],
             ["Site", station["NAME"]],
             ["Measurement", "Volumetric soil moisture, m³/m³"],
             ["Nominal sensor depths", "5, 10, 20, 50, 100 cm (network design; individual depths may be missing)"],
             ["Sensor design", "Hydra Probe II; three replicate profiles where soil permits"],
             ["Commissioned", station["COMMISSIONING"][:10]],
             ["Elevation", station["ELEVATION"] + " ft"],
             ["Coordinate precision", "0.01° in NOAA station catalog"]],
            [["NOAA station metadata", url("https://www.ncei.noaa.gov/access/crn/station.htm", stationId=station["STATION_ID"])],
             ["Sensor/depth documentation", "https://www.ncei.noaa.gov/access/crn/measurements.html"],
             ["NOAA station catalog", USCRN]],
        ))
    return stations


def awn_stations(inventory, catalog):
    """Require explicit soil-moisture inventory flags, not soil-temperature flags."""
    metadata = {str(s["UNIT_ID"]): s for s in catalog["data"]}
    stations = []
    for sensor in inventory["data"]:
        if sensor.get("soil_mois_8_in") != "Y":
            continue
        station = metadata.get(str(sensor["unit_id"]))
        if not station or str(station.get("STATION_VISIBILITY")).lower() != "public":
            continue
        if not station.get("STATION_LATDEG") or not station.get("STATION_LNGDEG"):
            continue
        lat = float(station["STATION_LATDEG"])
        # AWN station catalog stores positive degrees west, unlike GeoJSON.
        lon = -abs(float(station["STATION_LNGDEG"]))
        stations.append(point(
            "AWN." + str(sensor["unit_id"]), station["STATION_NAME"], "soil", lat, lon,
            [["Network", "WSU AgWeatherNet · " + str(station["NETWORK_TYPE"])],
             ["Measurement", "Soil moisture probe listed in AWN inventory"],
             ["Sensor depth", "8 in (20.32 cm)"],
             ["Hardware", station.get("HARDWARE_TYPE") or "Not supplied"],
             ["Soil series", sensor.get("soil_series") or "Not supplied"],
             ["Installation", station.get("INSTALLATION_DATE") or "Not supplied"],
             ["Elevation", str(station.get("STATION_ELEVATION")) + " ft"],
             ["Inventory date", inventory.get("overview_date", "Unknown")],
             ["Status", "Sensor inventory confirmed; current readings not assessed"]],
            [["AWN station metadata", url("https://weather.wsu.edu/weather/stations", location=station["STATION_NAME"])],
             ["AWN sensor inventory", "https://weather.wsu.edu/awn-networks/stations"]],
        ))
    return stations


def agrimet_odessa(site_html, parameters_html):
    """Verify ODSW moisture channels from its station-specific USBR inventory."""
    block = re.search(
        r'<a\s+name=[\"\']?ODSW[\"\']?\s*>.*?(?=<a\s+name=|</pre>)',
        parameters_html, re.IGNORECASE | re.DOTALL,
    )
    if not block:
        raise ValueError("USBR inventory has no ODSW station section")
    sensors = re.findall(
        r'(XSM\d+)\s+SOIL MOISTURE\s*-\s*(\d+)\s+INCH DEPTH\s*\(PERCENT\)',
        block.group(), re.IGNORECASE,
    )
    if not sensors:
        raise ValueError("USBR ODSW has no verified soil-moisture sensor depths")
    latitude = re.search(r'Latitude:\s*([-\d.]+)\s*N', site_html, re.IGNORECASE)
    longitude = re.search(r'Longitude:\s*([-\d.]+)\s*W', site_html, re.IGNORECASE)
    installed = re.search(r'Installation Date:\s*([\d/]+)', site_html, re.IGNORECASE)
    if not latitude or not longitude:
        raise ValueError("USBR ODSW station coordinates are missing")
    lat, lon = float(latitude[1]), -abs(float(longitude[1]))
    depths = sorted({int(depth) for _, depth in sensors})
    return point(
        "AgriMet.ODSW", "Odessa AgriMet (ODSW)", "soil", lat, lon,
        [["Network", "Bureau of Reclamation · AgriMet"],
         ["Measurement", "Soil moisture (percent), as reported by USBR"],
         ["Sensor depths", ", ".join(f"{d} in ({d * 2.54:g} cm)" for d in depths)],
         ["Parameter codes", ", ".join(code.upper() for code, _ in sensors)],
         ["Installation", installed[1] if installed else "Not supplied"],
         ["Sensor model", "Not specified in the published station inventory"],
         ["Status", "Moisture sensors verified in USBR inventory; data completeness not assessed"]],
        [["USBR station metadata", AGRIMET_SITE],
         ["USBR sensor inventory", AGRIMET_PARAMS + "#ODSW"],
         ["USBR station data", "https://www.usbr.gov/pn/agrimet/Instant/odsw.html"]],
    )


def cmr_geometry(umm):
    geometry = umm["SpatialExtent"]["HorizontalSpatialDomain"]["Geometry"]
    polygons = []
    for polygon in geometry.get("GPolygons", []):
        rings = [polygon["Boundary"]] + polygon.get("ExclusiveZone", {}).get("Boundaries", [])
        coordinates = []
        for ring in rings:
            points = [[p["Longitude"], p["Latitude"]] for p in ring["Points"]]
            if points[0] != points[-1]:
                points.append(points[0])
            coordinates.append(points)
        polygons.append(coordinates)
    if not polygons:
        raise ValueError("SME2 granule has no polygon footprint; refusing a guessed track")
    return {"type": "MultiPolygon", "coordinates": polygons}


def nisar_tracks(items):
    """Summarize repeat acquisitions, retaining every distinct catalog footprint."""
    groups = {}
    for item in items:
        u = item["umm"]
        attributes = {a["Name"]: a["Values"] for a in u["AdditionalAttributes"]}
        if attributes.get("PRODUCT_TYPE") != ["SME2"]:
            raise ValueError("CMR returned a non-SME2 product")
        track = int(attributes["TRACK_NUMBER"][0])
        frame = int(attributes["FRAME_NUMBER"][0])
        direction = attributes["ASCENDING_DESCENDING"][0]
        key = (track, direction, frame)
        group = groups.setdefault(key, {"track": track, "frame": frame, "direction": direction,
                                        "granules": [], "footprints": []})
        shape = cmr_geometry(u)
        if shape not in group["footprints"]:
            group["footprints"].append(shape)
        group["granules"].append({
            "id": u["GranuleUR"], "concept_id": item["meta"]["concept-id"],
            "collection": u["CollectionReference"]["ShortName"],
            "start": u["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"],
            "end": u["TemporalExtent"]["RangeDateTime"]["EndingDateTime"],
            "polarization": attributes.get("FREQUENCY_A_POLARIZATION", []),
            "metadata_url": f"https://cmr.earthdata.nasa.gov/search/concepts/{item['meta']['concept-id']}.umm_json",
        })
    result = []
    for key in sorted(groups):
        group = groups[key]
        group["granules"].sort(key=lambda g: (g["start"], g["concept_id"]))
        # No convex hull/bounding rectangle approximation: keep original rings.
        group["geometry"] = {"type": "MultiPolygon", "coordinates": [
            polygon for shape in group.pop("footprints") for polygon in shape["coordinates"]
        ]}
        group["id"] = f"{group['track']:03d}_{group['direction'][0]}_{group['frame']:03d}"
        result.append(group)
    return result


def search_nisar(get_json=fetch_json):
    params = {"short_name": "NISAR_L3_SME2*", "options[short_name][pattern]": "true",
              "point": f"{CENTER['longitude']},{CENTER['latitude']}", "page_size": 500}
    items = []
    page = 1
    while True:
        data = get_json(url(CMR, **params, page_num=page))
        batch = data["items"]
        items.extend(batch)
        if len(items) >= data["hits"]:
            break
        if not batch:
            raise RuntimeError("CMR pagination ended before all granules were returned")
        page += 1
    return items, url(CMR, **params)


def refresh():
    now = datetime.now(timezone.utc)
    as_of = now.date().isoformat()
    sources = []

    def record(name, address):
        print(f"Fetching {name}…", file=sys.stderr)
        payload = fetch(address)
        sources.append({"name": name, "url": address})
        return payload

    seismic = seismic_stations(record("PNSN / EarthScope", url(
        FDSN, net="UW", sta=",".join(STATIONS), level="channel", format="xml", endafter=as_of)), as_of)
    # Refresh the selected NRCS station; other catalogs are filtered by ID below.
    soil = awdb_stations(json.loads(record("NRCS AWDB", url(
        AWDB, stationTriplets=",".join(s for s in SOIL_STATIONS if ":" in s), elements="SMS:*", returnStationElements="true", activeOnly="false"))), as_of)
    soil += uscrn_stations(record("NOAA USCRN", USCRN).decode())
    catalog = json.loads(record("AWN station catalog", AWN + "/network/stations"))
    token = fetch_json(AWN + "/network/details?action=get_token")["temp"]
    inventory = fetch_json(url(AWN + "/network/details", temp=token))
    if inventory.get("status") != 1 or catalog.get("status") != 1:
        raise RuntimeError("AWN inventory request failed")
    sources.append({"name": "AWN sensor inventory", "url": "https://weather.wsu.edu/awn-networks/stations"})
    soil += awn_stations(inventory, catalog)
    soil.append(agrimet_odessa(
        record("USBR Odessa AgriMet station", AGRIMET_SITE).decode(),
        record("USBR AgriMet sensor inventory", AGRIMET_PARAMS).decode(),
    ))
    selected_soil = {station["id"]: station for station in soil}
    missing = set(SOIL_STATIONS) - selected_soil.keys()
    if missing:
        raise ValueError(f"Missing selected soil-moisture stations: {sorted(missing)}")
    soil = [selected_soil[identifier] for identifier in SOIL_STATIONS]
    print("Fetching NASA CMR SME2 footprints…", file=sys.stderr)
    items, query = search_nisar()
    sources.append({"name": "NASA CMR · all SME2 collections, all dates", "url": query})
    return {"schema_version": 1, "retrieved_at": now.isoformat(), "center": CENTER, "seismic": seismic,
            "soil": soil, "tracks": nisar_tracks(items),
            "sources": sources,
            "notes": [
                "Selected soil stations are refreshed by ID from NRCS AWDB, NOAA USCRN, public AWN stations with an explicit 8-inch moisture-probe inventory flag, and USBR AgriMet with verified XSM moisture channels. This is not an exhaustive inventory of private/research sites.",
                "AWN soil temperature and soil water potential alone are not classified as volumetric soil moisture. Unlisted sensors and recent upgrades may be absent from the published inventory.",
                "SME2 footprints are catalog coverage, not ground tracks or valid-pixel masks. All distinct footprints are retained within each track/frame; repeat acquisitions are summarized.",
                "The CMR search intersects the Odessa center point. Beta/provisional product quality and pixel flags require review before scientific use.",
                "Station metadata does not guarantee continuous or current observations. USCRN depths describe the network design and can contain missing values.",
            ]}


def validate(data):
    if data.get("schema_version") != 1 or data["center"] != CENTER:
        raise ValueError("Snapshot schema or search area does not match this map")
    if {s["id"] for s in data["seismic"]} != {f"UW.{s}" for s in STATIONS}:
        raise ValueError("Snapshot must contain all requested seismic stations")
    for station in data["seismic"] + data["soil"]:
        lat, lon = station["latitude"], station["longitude"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"Invalid coordinates for {station['id']}")
    for track in data["tracks"]:
        if not track["granules"] or not track["geometry"]["coordinates"]:
            raise ValueError("Empty track metadata or footprint")


# Keep the map layout with the builder so a clean build needs only the snapshot.
MAP_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Odessa · Soil moisture & seismic observation map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <style>
    :root { color-scheme: light; font: 13px/1.45 system-ui, sans-serif; color: #263a36; background: #fff; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    main { display: grid; grid-template-columns: minmax(0, 1fr) 235px; }
    #map { height: 100vh; min-height: 500px; background: #e5ebdd; }
    #legend { padding: 15px; border-left: 1px solid #ddd; overflow: auto; max-height: 100vh; }
    .legend-row { display: flex; align-items: center; gap: 9px; margin: 0 0 10px; font-size: 12px; }
    .legend-row[data-track] { cursor: pointer; }
    .legend-row.active, .legend-row:focus-visible { font-weight: 800; background: #edf2ee; outline: 2px solid #c8d5cf; outline-offset: 3px; }
    .legend-row.active .swatch { border-top-width: 5px; }
    .legend-symbol { width: 24px; flex-shrink: 0; text-align: center; }
    .dot { display: inline-block; width: 10px; height: 10px; background: #147e78; border-radius: 50%; }
    .triangle { color: #253e76; font-size: 17px; line-height: 1; }
    .swatch { display: inline-block; width: 24px; border-top: 3px solid; }
    a { color: #126d70; text-underline-offset: 3px; }
    .eyebrow { font-size: 10px; letter-spacing: .1em; font-weight: 700; text-transform: uppercase; color: #5d756c; }
    .marker { background: transparent; border: none; }
    .seismic-symbol { display: block; color: #253e76; font-size: 23px; line-height: 23px; text-shadow: 0 0 2px white, 0 0 2px white; }
    .soil-symbol { display: block; width: 14px; height: 14px; margin: 5px; border-radius: 50%; background: #147e78; border: 2px solid white; box-shadow: 0 1px 4px #25443888; }
    .odessa-symbol { display: block; color: #873d30; font-size: 23px; line-height: 23px; text-shadow: 0 0 3px white; }
    .station-label { background: #fffffff0; border: 0; box-shadow: none; padding: 1px 4px; color: #243d39; font-size: 10px; font-weight: 600; }
    .marker.active > span { transform: scale(1.3); filter: drop-shadow(0 0 3px #fff); }
    .station-label.active { font-weight: 800; background: #fff5cc; box-shadow: 0 0 0 2px #9d813b; }
    .station-label::before { display: none; }
    #hover-card { position: fixed; top: 12px; right: 247px; z-index: 800; background: #fff; border: 1px solid #c8d5cf; border-radius: 6px; box-shadow: 0 6px 22px #18383030; width: 310px; max-width: calc(100% - 24px); max-height: calc(100vh - 24px); overflow: auto; padding: 17px; font: 12px/1.45 system-ui, sans-serif; }
    .close-card { float: right; border: 0; background: transparent; font-size: 21px; line-height: 1; color: #536761; cursor: pointer; padding: 0 0 6px 6px; }
    #hover-card[hidden] { display: none; }
    #hover-card h2 { margin: 2px 0 3px; font-size: 18px; line-height: 1.25; }
    #hover-card h3 { font-size: 13px; margin: 14px 0 4px; }
    #hover-card p { margin: 5px 0 10px; color: #536761; }
    #hover-card dl { display: grid; grid-template-columns: 95px 1fr; gap: 6px 9px; margin: 12px 0; }
    #hover-card dt { color: #667a71; }
    #hover-card dd { margin: 0; overflow-wrap: anywhere; }
    #hover-card nav { display: flex; flex-wrap: wrap; gap: 7px 12px; font-size: 11px; }
    #hover-card section + section { border-top: 1px solid #dce3db; padding-top: 13px; margin-top: 17px; }
    .channel { padding: 7px 0; border-top: 1px solid #edf0eb; font-size: 10px; }
    .channel strong { font-size: 11px; }
    #status { position: absolute; bottom: 32px; left: 12px; z-index: 700; max-width: 390px; background: white; padding: 10px; border-radius: 4px; }
    .map-wrap { position: relative; min-width: 0; }
    @media (max-width: 650px) {
      main { grid-template-columns: 1fr; }
      #map { height: 75vh; min-height: 400px; }
      #legend { max-height: none; border-left: none; display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
      #hover-card { top: 12px; right: 12px; max-height: 42vh; }
    }
  </style>
</head>
<body>
  <main>
    <div class="map-wrap">
      <div id="map" aria-label="Interactive station and satellite coverage map"></div>
      <article id="hover-card" hidden aria-live="polite" aria-label="Feature metadata"></article>
      <div id="status" hidden role="status"></div>
    </div>
    <aside id="legend" aria-label="Map legend"></aside>
  </main>
  <noscript>This interactive map requires JavaScript. Station metadata is also saved in data/station_map.json.</noscript>
  <script id="map-data" type="application/json">__MAP_DATA__</script>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    "use strict";
    const data = JSON.parse(document.getElementById("map-data").textContent);
    const escapeHtml = value => String(value ?? "Not supplied").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const link = ([label, address]) => `<a href="${escapeHtml(address)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    const rows = values => `<dl>${values.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>`;
    const coords = (lat, lon) => `${Number(lat).toFixed(5)}°, ${Number(lon).toFixed(5)}°`;
    const colors = ["#af671c", "#7d54a6", "#bc4b63", "#3185a4", "#6d8428"];
    const trackNumbers = [...new Set(data.tracks.map(t => t.track))];
    const trackColor = t => colors[trackNumbers.indexOf(t.track) % colors.length];
    const card = document.getElementById("hover-card");
    const status = document.getElementById("status");
    let cardKey = "";
    function showCard(key, html, mapX) {
      // Put station cards on the opposite side so they do not cover the hovered icon.
      const alignLeft = mapX !== undefined && mapX > document.getElementById("map").clientWidth / 2;
      card.style.left = alignLeft ? "12px" : "";
      card.style.right = alignLeft ? "auto" : "";
      if (key === cardKey) return;
      cardKey = key;
      card.innerHTML = '<button class="close-card" aria-label="Close metadata card">×</button>' + html;
      card.querySelector(".close-card").addEventListener("click", () => { card.hidden = true; cardKey = ""; });
      card.hidden = false;
      card.scrollTop = 0;
    }
    document.addEventListener("keydown", e => { if (e.key === "Escape") { card.hidden = true; cardKey = ""; } });
    function stationCard(station) {
      const channels = station.channels ? `<h3>Seismic components</h3>${station.channels.map(c =>
        `<div class="channel"><strong>${escapeHtml(c.location || "--")}.${escapeHtml(c.code)} · ${c.sample_rate_hz} Hz</strong><br>${escapeHtml(c.sensor)}<br>Azimuth ${escapeHtml(c.azimuth_deg)}° · dip ${escapeHtml(c.dip_deg)}° · depth ${escapeHtml(c.depth_m)} m</div>`).join("")}` : "";
      return `<div class="eyebrow">${station.kind === "seismic" ? "Seismometer" : "Soil moisture"}</div>
        <h2>${escapeHtml(station.id)}</h2><p>${escapeHtml(station.name)}</p>
        ${rows([["Coordinates", coords(station.latitude, station.longitude)], ...station.rows])}
        <nav>${station.links.map(link).join("")}</nav>${channels}`;
    }
    function trackCard(track) {
      const granules = track.granules;
      const vertices = track.geometry.coordinates.flat(2);
      const lats = vertices.map(p => p[1]), lons = vertices.map(p => p[0]);
      const latest = granules[granules.length - 1];
      const collections = [...new Set(granules.map(g => g.collection))];
      const polarizations = [...new Set(granules.flatMap(g => g.polarization))];
      return `<section><div class="eyebrow" style="color:${trackColor(track)}">NISAR · Level 3 SME2</div>
        <h2>Track ${String(track.track).padStart(3,"0")} / frame ${String(track.frame).padStart(3,"0")}</h2>
        ${rows([["Direction", track.direction],
          ["SW / NE bounds", coords(Math.min(...lats), Math.min(...lons)) + " / " + coords(Math.max(...lats), Math.max(...lons))],
          ["Grid spacing", "200 m · EASE-Grid 2.0"], ["Polarization A", polarizations.join(", ")],
          ["Catalog granules", granules.length + " (includes collection/reprocessing versions)"],
          ["Acquisition range", granules[0].start.slice(0,10) + " — " + latest.start.slice(0,10)],
          ["Collections", collections.join("; ")]])}
        <nav>${link(["Latest granule metadata", latest.metadata_url])}${link(["ASF track search", "https://search.asf.alaska.edu/#/?dataset=NISAR&sciProducts=SME2&path=" + track.track + "&frame=" + track.frame])}${link(["SME2 product metadata", "https://nisar-docs.asf.alaska.edu/sme2/"])}</nav></section>`;
    }
    // Ray casting in local lon/lat space, including polygon holes. This lets a
    // hover highlight every overlapping track, regardless of SVG drawing order.
    function inRing(point, ring) {
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > point[1]) !== (yj > point[1]) && point[0] < (xj-xi) * (point[1]-yi) / (yj-yi) + xi) inside = !inside;
      }
      return inside;
    }
    function contains(track, point) {
      return track.geometry.coordinates.some(poly => inRing(point, poly[0]) && !poly.slice(1).some(hole => inRing(point, hole)));
    }
    const legendRow = (symbol, label, attributes = "") => `<div class="legend-row" ${attributes}><span class="legend-symbol">${symbol}</span><span>${escapeHtml(label)}</span></div>`;
    document.getElementById("legend").innerHTML =
      legendRow('<span class="triangle">▲</span>', "Seismometer") +
      legendRow('<span class="dot"></span>', "Soil moisture") +
      legendRow('<span style="color:#873d30">✚</span>', "Odessa") +
      trackNumbers.map(track => legendRow(`<span class="swatch" style="border-color:${trackColor({track})}"></span>`,
        `NISAR ${String(track).padStart(3,"0")}`, `data-track="${track}" tabindex="0" role="button" aria-label="NISAR track ${String(track).padStart(3,"0")} metadata"`)).join("");
    if (!window.L) {
      status.hidden = false;
      status.textContent = "The map library could not load. Connect to the internet and reload to view Leaflet and Esri basemap tiles.";
    } else {
      const map = L.map("map", {zoomSnap: 0.25, scrollWheelZoom: true})
        .setView([data.center.latitude, data.center.longitude], 8);
      const tiles = L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: 'Tiles © <a href="https://www.esri.com/">Esri</a> — Esri, HERE, Garmin, Intermap, increment P, GEBCO, USGS, FAO, NPS, NRCAN, GeoBase, IGN, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China (Hong Kong), OpenStreetMap contributors, GIS User Community'
      }).addTo(map);
      tiles.on("tileerror", () => { status.hidden = false; status.textContent = "Some Esri basemap tiles could not load. Station and coverage metadata remain available."; });
      L.control.scale({imperial:false}).addTo(map);
      const trackLayers = new Map(trackNumbers.map(track => [track, []]));
      data.tracks.forEach(track => {
        const layer = L.geoJSON({type:"Feature", geometry:track.geometry}, {
          interactive: false, style: {color:trackColor(track), weight:1.4, opacity:.55, fillOpacity:.025, fillRule:"nonzero"}
        }).addTo(map);
        trackLayers.get(track.track).push(layer);
      });
      const stationBounds = L.latLngBounds([[data.center.latitude, data.center.longitude]]);
      [...data.seismic, ...data.soil].forEach(station => stationBounds.extend([station.latitude, station.longitude]));
      map.fitBounds(stationBounds.pad(.2), {padding:[40,40]});
      const trackRows = [...document.querySelectorAll("#legend [data-track]")];
      function highlightTracks(numbers) {
        const active = new Set(numbers);
        trackLayers.forEach((layers, number) => layers.forEach(layer => {
          layer.setStyle(active.has(number)
            ? {weight:3, opacity:1, fillOpacity:.14}
            : {weight:1.4, opacity:.55, fillOpacity:.025});
        }));
        trackRows.forEach(row => row.classList.toggle("active", active.has(Number(row.dataset.track))));
      }
      trackRows.forEach(row => {
        const activate = () => {
          const number = Number(row.dataset.track);
          highlightTracks([number]);
          showCard("track:" + number, data.tracks.filter(t => t.track === number).map(trackCard).join(""));
        };
        row.addEventListener("mouseenter", activate);
        row.addEventListener("focus", activate);
        row.addEventListener("click", activate);
        row.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
        });
        row.addEventListener("mouseleave", () => highlightTracks([]));
        row.addEventListener("blur", () => highlightTracks([]));
      });
      function highlightMarker(marker, active) {
        marker.getElement().classList.toggle("active", active);
        marker.getTooltip().getElement().classList.toggle("active", active);
        marker.setZIndexOffset(active ? 1000 : 0);
      }
      const markers = [];
      function addStation(station) {
        const marker = L.marker([station.latitude,station.longitude], {icon:L.divIcon({className:"marker", html:station.kind === "seismic" ? '<span class="seismic-symbol">▲</span>' : '<span class="soil-symbol"></span>', iconSize:[24,24], iconAnchor:[12,12]}), title:station.id + " · " + station.name, alt:station.id, keyboard:true}).addTo(map);
        marker.bindTooltip(station.kind === "seismic" ? station.id : station.name, {permanent:true, direction:"right", offset:[9,0], className:"station-label", interactive:false});
        const activate = () => { highlightTracks([]); highlightMarker(marker, true); showCard(station.id, stationCard(station), map.latLngToContainerPoint(marker.getLatLng()).x); };
        marker.on("mouseover", activate).on("click", activate).on("mouseout", () => highlightMarker(marker, false));
        marker.getElement().addEventListener("focus", activate);
        marker.getElement().addEventListener("blur", () => highlightMarker(marker, false));
        markers.push(marker);
      }
      data.soil.forEach(addStation);
      data.seismic.forEach(addStation);
      const odessa = L.marker([data.center.latitude,data.center.longitude], {icon:L.divIcon({className:"marker", html:'<span class="odessa-symbol">✚</span>', iconSize:[24,24], iconAnchor:[12,12]}), title:"Odessa reference point"}).addTo(map);
      odessa.bindTooltip("Odessa", {permanent:true, direction:"left", offset:[-10,0], className:"station-label"});
      const activateOdessa = () => { highlightTracks([]); highlightMarker(odessa, true); showCard("odessa", `<div class="eyebrow">Study reference</div><h2>Odessa, Washington</h2>${rows([["Coordinates",coords(data.center.latitude,data.center.longitude)],["Datum","WGS84"]])}<p>The SME2 point query uses this town-center reference.</p>`, map.latLngToContainerPoint(odessa.getLatLng()).x); };
      odessa.on("mouseover click", activateOdessa);
      odessa.on("mouseout", () => highlightMarker(odessa, false));
      odessa.getElement().addEventListener("focus", activateOdessa);
      odessa.getElement().addEventListener("blur", () => highlightMarker(odessa, false));
      // Map hover highlights coverage only. Track cards open from the legend,
      // and stay open while the pointer travels to their metadata links.
      map.on("mousemove", e => {
        const onMarker = e.originalEvent?.target?.closest(".leaflet-marker-icon");
        const hits = onMarker ? [] : data.tracks.filter(t => contains(t, [e.latlng.lng, e.latlng.lat]));
        highlightTracks(hits.map(t => t.track));
      });
      map.getContainer().addEventListener("mouseleave", () => highlightTracks([]));
      // Exposed for browser interaction checks.
      window.stationMap = {map, markers, data, contains, trackLayers};
    }
  </script>
</body>
</html>
"""


def render(data):
    validate(data)
    # Avoid closing the script element even if upstream metadata contains HTML.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    return MAP_HTML.replace("__MAP_DATA__", payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="Query public metadata and replace the snapshot only on success")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "station_map.json")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "station_map.html")
    args = parser.parse_args()
    try:
        data = refresh() if args.refresh else json.loads(args.snapshot.read_text())
        document = render(data)
        if args.refresh:
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.snapshot.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            temporary.replace(args.snapshot)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"Map build failed: {error}\nExisting snapshot is preserved if refresh failed.\n")
    print(f"Built {args.output}: {len(data['seismic'])} seismic stations, "
          f"{len(data['soil'])} soil stations, {len(data['tracks'])} SME2 track/frames. "
          f"Metadata snapshot: {data['retrieved_at']}")


if __name__ == "__main__":
    main()
