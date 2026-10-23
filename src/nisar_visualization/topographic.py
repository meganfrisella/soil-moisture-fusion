"""Topographic context for SME2 viewing windows, rendered in EPSG:6933 metres.

Map labels use WGS84 degrees. Esri imagery is cartographic context with varying
source dates; it is not an observation or a soil-moisture uncertainty product.
Run through ``pixi run python scripts/visualize_nisar_sme2.py --help``;
fetching the Esri basemap requires internet and curl.
"""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import textwrap
import time
from urllib.parse import urlencode

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
from PIL import Image
from pyproj import Geod

from nisar.geometry import FORWARD, INVERSE
from maps.common import BASEMAP_SERVICE as SERVICE, BOUNDARY_COLOR, latitude, longitude, bounds_label


def fetch(address):
    """Fetch public cartographic metadata or imagery with HTTPS verification."""
    try:
        return subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                               '--connect-timeout', '15', '--max-time', '120', '--retry', '2', address],
                              check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f'Public basemap request failed (curl status {error.returncode})') from error


def render_topographic(view, output, name, image_size=1600):
    """Save one PNG using the same exact projected viewing bounds as the GIF."""
    bounds = view.projected.bounds
    xmin, ymin, xmax, ymax = bounds
    width, height = xmax - xmin, ymax - ymin
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    west, south = INVERSE.transform(xmin, ymin)
    east, north = INVERSE.transform(xmax, ymax)
    scale = image_size / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    started = time.perf_counter()
    service_bytes = fetch(SERVICE + '?f=json')
    credits = json.loads(service_bytes)['copyrightText']
    address = SERVICE + '/export?' + urlencode({
        'bbox': ','.join(map(str, bounds)), 'bboxSR': 6933, 'imageSR': 6933,
        'size': ','.join(map(str, size)), 'dpi': 150, 'format': 'png32',
        'transparent': 'false', 'f': 'json'})
    export_bytes = fetch(address)
    exported = json.loads(export_bytes)
    if 'error' in exported:
        raise ValueError(exported['error'])
    extent = exported['extent']
    if extent['spatialReference'].get('latestWkid', extent['spatialReference'].get('wkid')) != 6933:
        raise ValueError('Basemap service returned a different CRS')
    actual = [extent[k] for k in ('xmin', 'ymin', 'xmax', 'ymax')]
    # The service may expand slightly to fit an integer image aspect ratio.
    # Place the image at its returned extent and clip axes to the requested box.
    if (actual[0] > xmin + .01 or actual[1] > ymin + .01
            or actual[2] < xmax - .01 or actual[3] < ymax - .01):
        raise ValueError('Basemap export does not contain the requested viewing box')
    blob = fetch(exported['href'])
    elapsed = time.perf_counter() - started
    retrieved = datetime.now(timezone.utc).isoformat()
    image = Image.open(io.BytesIO(blob)).convert('RGB')
    if image.size != size:
        raise ValueError('Unexpected basemap image dimensions')
    map_width = min(8.25, 10.0 * width / height)
    map_height = map_width * height / width
    figure_height = map_height + 3.1
    fig = plt.figure(figsize=(10, figure_height), dpi=180, facecolor='white')
    ax = fig.add_axes([(1 - map_width / 10) / 2, 1.45 / figure_height, map_width / 10, map_height / figure_height])
    ax.imshow(image, extent=[actual[0], actual[2], actual[1], actual[3]],
              origin='upper', interpolation='nearest')
    ax.set(xlim=(xmin, xmax), ylim=(ymin, ymax), aspect='equal')
    ax.set_xticks(np.linspace(xmin, xmax, max(2, min(5, int(map_width / 1.6)))))
    ax.set_yticks(np.linspace(ymin, ymax, 4))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: longitude(INVERSE.transform(x, cy)[0])))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, pos: latitude(INVERSE.transform(cx, y)[1])))
    ax.tick_params(labelsize=9, pad=5)
    ax.set_xlabel('Longitude · WGS84', fontsize=10)
    ax.set_ylabel('Latitude · WGS84', fontsize=10)
    for spine in ax.spines.values():
        spine.set_color(BOUNDARY_COLOR)
        spine.set_linewidth(1.5)
        spine.set_linestyle((0, (6, 3)))
    ax.annotate('N', xy=(.955, .96), xytext=(.955, .78), xycoords='axes fraction',
                ha='center', va='center', fontsize=12, weight='bold',
                bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': .9, 'pad': 3},
                arrowprops={'arrowstyle': '-|>', 'color': '#202b35', 'lw': 1.5})
    x0, y0 = xmin + .045 * width, ymin + .06 * height
    lon0, lat0 = INVERSE.transform(x0, y0)
    # Choose a scale-bar distance that fits this box, rather than a fixed 2 km.
    target_lon, target_lat = INVERSE.transform(x0 + width * .18, y0)
    geod = Geod(ellps='WGS84')
    _, _, target_distance = geod.inv(lon0, lat0, target_lon, target_lat)
    power = 10 ** np.floor(np.log10(target_distance))
    distance = max(v * power for v in (1, 2, 5) if v * power <= target_distance)
    lon1, lat1, _ = geod.fwd(lon0, lat0, 90, distance)
    x1, _ = FORWARD.transform(lon1, lat1)
    ax.plot([x0, x1], [y0, y0], color='white', linewidth=6, solid_capstyle='butt')
    ax.plot([x0, x1], [y0, y0], color='#202b35', linewidth=2, solid_capstyle='butt')
    ax.text((x0 + x1) / 2, y0 + .02 * height, f'{distance / 1000:g} km' if distance >= 1000 else f'{distance:g} m', ha='center', va='bottom', fontsize=9,
            bbox={'facecolor': 'white', 'edgecolor': 'none', 'alpha': .9, 'pad': 2})
    area = width * height / 1e6
    fig.text(.52, 1 - .22 / figure_height, name, ha='center', va='top',
             fontsize=21, weight='bold', color='#253543')
    fig.text(.52, 1 - .68 / figure_height,
             f'{area:.2f} km² in EPSG:6933 · topographic map · same extent as the SME2 animation',
             ha='center', fontsize=10, color='#4f5d66')
    fig.text(.52, .82 / figure_height,
             f'WGS84 bounds: {bounds_label(view)}',
             ha='center', fontsize=8.5, color='#4f5d66')
    fig.text(.105, .55 / figure_height, textwrap.fill('Esri World Topographic Map. ' + credits, width=134),
             ha='left', va='top', fontsize=6.5, color='#4f5d66')
    fig.text(.105, .10 / figure_height, f'Basemap retrieved {retrieved[:10]} UTC; source mapping dates vary.',
             ha='left', fontsize=6.5, color='#4f5d66')
    provenance = {'region': name, 'area_km2': area, 'crs': 'EPSG:6933',
                  'bounds': list(bounds), 'bounds_wgs84': [west, south, east, north],
                  'basemap_export_extent': actual, 'basemap_export_url': address,
                  'retrieved_at_utc': retrieved, 'basemap_native_export_size': list(size),
                  'download_bytes': len(blob) + len(service_bytes) + len(export_bytes),
                  'download_seconds': elapsed, 'copyright': credits}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.png.tmp')
    try:
        fig.savefig(temporary, format='png', dpi=180, bbox_inches='tight', metadata={
            'Title': name + ' — topographic viewing rectangle',
            'Description': json.dumps(provenance), 'Copyright': credits, 'Source': SERVICE})
        temporary.replace(output)
    finally:
        plt.close(fig)
        image.close()
        temporary.unlink(missing_ok=True)
    return provenance
