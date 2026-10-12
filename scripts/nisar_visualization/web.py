"""Assets-folder HTML renderer for native SME2 acquisition groups.

Six candidate fields use the same scales/counts/flags as the GIF. One PNG pixel
represents one native EPSG:6933 value cell; red borders use 4×4 display blocks.
Leaflet positions each native row by its geographic edges and clips rendering
to the exact WGS84 box. PNGs store display colors, not scientific observations.
Run through ``pixi run python scripts/visualize_nisar_sme2.py --help``;
viewing the generated map requires internet for Leaflet and basemap tiles.
"""

import hashlib
import io
from pathlib import Path
from urllib.parse import quote

import numpy as np
from PIL import Image

from maps.common import render_template
from nisar.geometry import INVERSE
from nisar.quality import ALGORITHMS, flag_masks, cell_counts
from .style import (DEFAULT_LIMITS, FIELDS, FIELD_LABELS, UNITS, MISSING_COLOR, ODD_COLOR,
                    color_maps)
from matplotlib.colors import to_hex, to_rgba

TEMPLATES = Path(__file__).resolve().parent


def view_definitions(limits=DEFAULT_LIMITS):
    """Define exactly six candidate fields and shared fixed display scales."""
    return [{'key': f'{algorithm}.{field}', 'algorithm': algorithm, 'field': field,
             'row': row, 'label': f'{algorithm} {FIELD_LABELS[row]}', 'units': UNITS,
             'minimum': limits[row][0], 'maximum': limits[row][1],
             'colors': [to_hex(c) for c in cmap(np.linspace(0, 1, 100))],
             'extend_min': row == 0 or limits[row][0] != 0}
            for row, (field, cmap) in enumerate(zip(FIELDS, color_maps())) for algorithm in ALGORITHMS]


def png_bytes(pixels):
    """Encode lossless RGBA display colors with explicit per-pixel alpha."""
    with Image.fromarray(pixels) as image:
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        return stream.getvalue()


def encode_values(values, mask, row, limits):
    """Missing in-box cells are gray; outside cells transparent; overflow matches endpoints."""
    lower, upper = limits[row]
    scaled = np.ma.masked_invalid((values - lower) / (upper - lower))
    rgba = color_maps()[row](scaled, bytes=True)
    rgba[~mask, 3] = 0
    return png_bytes(rgba)


def encode_outlines(flags, mask):
    """Draw only valid odd candidate flags, including flags on missing-valued cells."""
    odd, _ = flag_masks(flags)
    marked = odd & mask
    if not marked.any():
        return None
    scale = 4
    pixels = np.zeros((mask.shape[0] * scale, mask.shape[1] * scale, 4), dtype=np.uint8)
    blocks = pixels.reshape(mask.shape[0], scale, mask.shape[1], scale, 4).transpose(0, 2, 1, 3, 4)
    red = np.rint(np.array(to_rgba(ODD_COLOR)) * 255).astype(np.uint8)
    blocks[marked, 0, :] = red
    blocks[marked, -1, :] = red
    blocks[marked, :, 0] = red
    blocks[marked, :, -1] = red
    return png_bytes(pixels)


def render_html(view, layout, frames, output, title, limits=DEFAULT_LIMITS, provenance=None, tracks=()):
    """Publish HTML after all content-addressed sibling assets are written."""
    if not frames:
        raise ValueError('Cannot render HTML without qualifying granules')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = output.with_name(output.stem + '_assets')
    asset_dir.mkdir(exist_ok=True)
    assets = {}

    def save_asset(blob):
        identity = hashlib.sha256(blob).hexdigest()
        if identity not in assets:
            path = asset_dir / (identity + '.png')
            if not path.exists() or path.read_bytes() != blob:
                temporary = path.with_suffix('.png.tmp')
                temporary.write_bytes(blob)
                temporary.replace(path)
            assets[identity] = quote(path.relative_to(output.parent).as_posix())
        return identity

    views = view_definitions(limits)
    encoded_frames = []
    records = frames.records if hasattr(frames, 'records') else frames
    for number, (frame, record) in enumerate(zip(frames, records), 1):
        layers, outlines = {}, {}
        for algorithm in ALGORITHMS:
            blob = encode_outlines(frame['data'][algorithm]['retrievalQualityFlag'], layout['mask'])
            outlines[algorithm] = save_asset(blob) if blob else None
        for definition in views:
            arrays = frame['data'][definition['algorithm']]
            values, flags = arrays[definition['field']], arrays['retrievalQualityFlag']
            layers[definition['key']] = dict(
                cell_counts(values, flags, layout['mask']),
                values=save_asset(encode_values(values, layout['mask'], definition['row'], limits)),
                outlines=outlines[definition['algorithm']])
        metadata = frame['metadata']
        encoded_frames.append({'start': metadata['zeroDopplerStartTime'],
                               'end': metadata['zeroDopplerEndTime'],
                               'granules': metadata.get('source_granules', [
                                   dict(metadata, source_pixels=record.get('source_pixels', 0))]),
                               'layers': layers})
        print(f'Encoded HTML frame {number}/{len(frames)}: {metadata["zeroDopplerStartTime"]}', flush=True)
    manifest = dict(schema='nisar-sme2-box-v1', title=title, views=views, frames=encoded_frames,
                    assets=assets, grid=dict(geographic_edges(layout), crs='EPSG:6933',
                                            dx_m=layout['dx'], dy_m=layout['dy']),
                    bounds_wgs84=view.bounds, domain={'geometry': view.geometry},
                    domain_native_pixels=int(layout['mask'].sum()),
                    granule_count=sum(len(frame['granules']) for frame in encoded_frames),
                    provenance=dict(provenance or {}), tracks=list(tracks),
                    missing_color=MISSING_COLOR, odd_color=ODD_COLOR)
    html = render_template((TEMPLATES / 'map.html').read_text(), manifest,
                           (TEMPLATES / 'map.js').read_text())
    temporary = output.with_suffix('.html.tmp')
    temporary.write_text(html)
    temporary.replace(output)
    print(f'Wrote {output} and {asset_dir} ({len(assets)} unique PNG assets)', flush=True)


def geographic_edges(layout):
    """Return geographic native-cell edges for row-wise rendering without stretching."""
    x, y = layout['xx'][0], layout['yy'][:, 0]
    dx, dy = layout['dx'], layout['dy']
    xedges = np.r_[x - dx / 2, x[-1] + dx / 2]
    yedges = np.r_[y - dy / 2, y[-1] + dy / 2]
    lons, _ = INVERSE.transform(xedges, np.full_like(xedges, y.mean()))
    _, lats = INVERSE.transform(np.full_like(yedges, x.mean()), yedges)
    if not (np.all(np.diff(lons) > 0) and np.all(np.diff(lats) < 0)):
        raise ValueError('Expected eastward columns and southward rows')
    return {'longitude_edges': lons.tolist(), 'latitude_edges': lats.tolist(),
            'rows': len(y), 'cols': len(x)}
