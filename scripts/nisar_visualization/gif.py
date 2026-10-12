"""Six-panel SME2 GIF rendering on native EPSG:6933 cells.

Each frame is one qualifying acquisition group. Fixed moisture/uncertainty limits use
m³/m³; under/over-range colors match the corresponding color-scale endpoints.
Odd candidate flags alone receive red borders. Combined flags are not read.
Run through ``pixi run python scripts/visualize_nisar_sme2.py --help``.
"""

import json
import os
from pathlib import Path
import tempfile
import time

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'soil-fusion-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
from PIL import Image, GifImagePlugin

from nisar.geometry import INVERSE
from nisar.quality import ALGORITHMS, flag_masks, cell_counts
from maps.common import latitude, longitude
from .style import DEFAULT_LIMITS, FIELDS, MISSING_COLOR, ODD_COLOR, color_maps


def gif_palette(cmaps):
    """Use Pillow's shared palette so numerical and flag colors stay stable over time."""
    colors = [to_rgb(c) for c in [MISSING_COLOR, ODD_COLOR, 'white', 'black']]
    colors += [tuple([v] * 3) for v in np.linspace(0, 1, 32)]
    for cmap in cmaps:
        colors += [tuple(rgba[:3]) for rgba in cmap(np.linspace(0, 1, 100))]
    palette = np.zeros((256, 3), dtype=np.uint8)
    palette[:len(colors)] = np.rint(np.array(colors) * 255).astype(np.uint8)
    image = Image.new('P', (1, 1))
    image.putpalette(palette.ravel().tolist())
    return image


def metadata_label(value, width=0):
    """Format scalar or grouped source metadata without hiding differing identities."""
    values = value if isinstance(value, list) else [value]
    return ', '.join(str(item).zfill(width) for item in values)


def render_gif(layout, frames, output, name, limits=DEFAULT_LIMITS, duration_ms=1000, provenance=None):
    """Draw native cells, annotate each acquisition, and save one constant-cadence GIF."""
    started = time.perf_counter()
    provenance = dict(provenance or {})
    output = Path(output)
    total_cells = int(layout['mask'].sum())
    if not frames:
        raise ValueError(f"No eligible granules for {name}'s viewing rectangle")
    cmaps = color_maps()
    palette = gif_palette(cmaps)
    centre_x, centre_y = layout['centre_xy']
    xmin, ymin, xmax, ymax = layout['square'].bounds
    xlim = ((xmin - centre_x) / 1000, (xmax - centre_x) / 1000)
    ylim = ((ymin - centre_y) / 1000, (ymax - centre_y) / 1000)
    area_km2 = layout['square'].area / 1e6
    west, south = INVERSE.transform(xmin, ymin)
    east, north = INVERSE.transform(xmax, ymax)
    x = (layout['xx'][0] - centre_x) / 1000
    y = (layout['yy'][:, 0] - centre_y) / 1000
    dx, dy = layout['dx'] / 1000, layout['dy'] / 1000
    xedges = np.r_[x - dx / 2, x[-1] + dx / 2]
    yedges = np.r_[y - dy / 2, y[-1] + dy / 2]
    corners = pixel_corners(layout)
    ratio = (ymax - ymin) / (xmax - xmin)
    figure_height = max(8., min(18., 5.5 + 8. * ratio))
    fig, axes = plt.subplots(2, 3, figsize=(16, figure_height), dpi=100, sharex=True, sharey=True)
    fig.subplots_adjust(left=.065, right=.88, bottom=.11, top=.745, wspace=.09, hspace=.31)
    meshes, outlines = {}, {}
    for row in range(2):
        for col, algorithm in enumerate(ALGORITHMS):
            ax = axes[row, col]
            mesh = ax.pcolormesh(xedges, yedges, np.full(layout['mask'].shape, np.nan),
                                 cmap=cmaps[row], vmin=limits[row][0], vmax=limits[row][1], shading='flat',
                                 antialiased=False)
            outline = PolyCollection([], facecolors='none', linewidths=.42, antialiaseds=False)
            ax.add_collection(outline)
            ax.set(xlim=xlim, ylim=ylim, aspect='equal')
            ax.tick_params(labelsize=9)
            if row == 1:
                ax.set_xlabel('Easting from centre (km)', fontsize=10)
            if col == 0:
                ax.set_ylabel('Northing from centre (km)', fontsize=10)
            meshes[row, algorithm], outlines[row, algorithm] = mesh, outline
        position = axes[row, -1].get_position()
        cax = fig.add_axes([.90, position.y0, .013, position.height])
        bar = fig.colorbar(mesh, cax=cax, extend='both' if limits[row][0] != 0 or row == 0 else 'max')
        bar.set_label(('Soil moisture' if row == 0 else 'Reported uncertainty') + ' (m³/m³)', fontsize=10)
        bar.ax.tick_params(labelsize=9)
    title = fig.text(.5, .965, '', ha='center', va='top', fontsize=17, weight='bold')
    metadata_text = fig.text(.5, .927, '', ha='center', va='top', fontsize=10, linespacing=1.65)
    filename_text = fig.text(.5, .808, '', ha='center', va='top', fontsize=9, family='monospace')
    fig.legend(handles=[Patch(facecolor=MISSING_COLOR, label='Missing value / algorithm unavailable'),
                        Line2D([], [], color=ODD_COLOR, lw=2, label='Odd candidate flag')],
               loc='lower center', bbox_to_anchor=(.49, .035), ncol=2, frameon=False, fontsize=10)
    fig.text(.5, .017, f'{area_km2:.2f} km² in EPSG:6933 • WGS84 {latitude(south)}–{latitude(north)}, {longitude(west)}–{longitude(east)} • '
             f'{duration_ms / 1000:g} s/frame; acquisition gaps not to scale • fixed color scales',
             ha='center', fontsize=9)
    records = frames.records if hasattr(frames, 'records') else frames
    frame_metadata = [dict(r['metadata'], numeric_moisture_cells=r['numeric_moisture_cells'])
                      for r in records]
    comment = dict(provenance, region=name,
                   centre_wgs84_lon_lat=list(layout['centre_lon_lat']), area_km2=area_km2,
                   bounds_wgs84=[west, south, east, north], bounds_epsg6933_m=[xmin, ymin, xmax, ymax],
                   flag_rule='Red borders iff the displayed algorithm candidate retrievalQualityFlag is valid and odd; no other flags; combined flag not visualized',
                   cell_count_rule='Total: native cells intersecting viewing rectangle; numeric: finite displayed field; recommended: numeric and valid even candidate flag',
                   crs='EPSG:6933', frame_duration_ms=duration_ms,
                   moisture_limits=list(limits[0]), uncertainty_limits=list(limits[1]),
                   selection_rule=provenance.get('selection_rule', 'Every qualifying acquisition group, ordered by acquisition time and filename'),
                   frames=frame_metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.gif.tmp')
    destination = temporary.open('wb')
    try:
        for number, frame in enumerate(frames, 1):
            metadata = frame['metadata']
            timestamp = metadata['zeroDopplerStartTime'].rstrip('Z')
            end = metadata['zeroDopplerEndTime'].rstrip('Z')
            source_count = len(metadata.get('source_granules', [metadata]))
            title.set_text(f"{name} · NISAR SME2 · {timestamp[:10]} UTC · {number}/{len(frames)}")
            metadata_text.set_text(
                f"Acquisition {timestamp.replace('T', ' ')}–{end[11:] if end[:10] == timestamp[:10] else end} UTC\n"
                f"Track {metadata_label(metadata['trackNumber'], 3)} / frames {metadata_label(metadata['frameNumber'], 3)}    |    "
                f"{metadata_label(metadata['orbitPassDirection'])}    |    Orbit {metadata_label(metadata['absoluteOrbitNumber'])}\n"
                f"Product {metadata_label(metadata['productVersion'])}    |    Specification {metadata_label(metadata['productSpecificationVersion'])}    |    "
                f"Release {metadata_label(metadata['compositeReleaseId'])}    |    PROVISIONAL\n"
                f'{source_count} source granule(s) in this acquisition group')
            filename_text.set_text(metadata['granule'])
            for col, algorithm in enumerate(ALGORITHMS):
                arrays = frame['data'][algorithm]
                odd, _ = flag_masks(arrays['retrievalQualityFlag'])
                marked = odd & layout['mask']
                for row, field in enumerate(FIELDS):
                    values = arrays[field]
                    meshes[row, algorithm].set_array(np.ma.masked_invalid(values))
                    outlines[row, algorithm].set_verts(corners[marked])
                    outlines[row, algorithm].set_edgecolors(ODD_COLOR)
                    kind = 'moisture' if row == 0 else 'uncertainty'
                    counts = cell_counts(values, arrays['retrievalQualityFlag'], layout['mask'])
                    count, recommended = counts['numeric_cells'], counts['recommended_cells']
                    status = (f'{count/total_cells:.2%} numeric cells \n'
                              f'{recommended/total_cells:.2%} recommended cells')
                    axes[row, col].set_title(f'{algorithm} {kind}\n{status}', fontsize=10, pad=6)
            fig.canvas.draw()
            rgb = Image.fromarray(np.asarray(fig.canvas.buffer_rgba()).copy()).convert('RGB')
            quantized = rgb.quantize(palette=palette, dither=Image.Dither.NONE)
            if number == 1:
                header, _ = GifImagePlugin.getheader(quantized, info={
                    'loop': 0, 'comment': json.dumps(comment).encode('utf-8'), 'optimize': False})
                for chunk in header:
                    destination.write(chunk)
            # Encode each complete frame immediately; memory does not grow with
            # the number of acquisitions, and identical frames are not merged.
            for chunk in GifImagePlugin.getdata(quantized, duration=duration_ms, disposal=2):
                destination.write(chunk)
            quantized.close()
            rgb.close()
        destination.write(b';')
        destination.close()
        temporary.replace(output)
        print(f'{output}: {len(frames)} frames; render/encode {time.perf_counter() - started:.1f} s; '
              f'{output.stat().st_size / 1024**2:.2f} MiB', flush=True)
    finally:
        plt.close(fig)
        destination.close()
        temporary.unlink(missing_ok=True)
        palette.close()


def pixel_corners(layout):
    """Native cell polygons in EPSG:6933 kilometres relative to the viewing centre."""
    cx, cy = layout['centre_xy']
    x, y = (layout['xx'] - cx) / 1000, (layout['yy'] - cy) / 1000
    hx, hy = abs(layout['dx']) / 2000, abs(layout['dy']) / 2000
    return np.stack([np.stack([x-hx, y-hy], axis=-1), np.stack([x+hx, y-hy], axis=-1),
                     np.stack([x+hx, y+hy], axis=-1), np.stack([x-hx, y+hy], axis=-1)], axis=-2)
