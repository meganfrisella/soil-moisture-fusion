"""Export and record the approved R35 native six-panel Odessa reference PNG.

From repo root after pixi install --locked, run the native stage:
    pixi run python scripts/regrid_eastern_washington.py --stage native --output-dir outputs/eastern_washington/regridding
After opening/visually reviewing that exact PNG, record its manifest SHA-256:
    pixi run python scripts/regrid_eastern_washington.py --stage native --output-dir outputs/eastern_washington/regridding --inspect-native-figure SHA256

``export_native(run, template, crop, slices, display, selected)`` requires a
locked RunStore with completed native tasks, native schema template, Odessa
native layout/slices, strict prepare_grid display transforms and frozen ranking
record. It crops completed fields, transforms 200 m EPSG:6933 polygons to display
EPSG:5070 (fixed 30 m edge segments) and clips to the common Odessa boundary;
no field resampling. Fields/limits are m³/m³, timestamps UTC. Output: only
figures/odessa_native.png, 200 DPI, 3200 px wide; metadata/counts/caption in PNG
and existing manifest. Gray denotes missing independently in each panel.
``record_inspection(output_dir, digest)`` records review only for that exact PNG;
it performs no automatic visual-quality judgment. Other method renderers await
their sequential scientific implementations. No preview/sidecar/remote writes.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from ingest.downloads import publish_file
from nisar.quality import ALGORITHMS
from nisar_visualization.style import color_maps, DEFAULT_LIMITS, MISSING_COLOR, ODD_COLOR
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Polygon, Patch
from matplotlib.lines import Line2D

from regridding.geometry import transform_rectangle, CELL_SEGMENT_M
from regridding.inputs import ROOT, sha256_file
from regridding.schema import canonical_json
from regridding.storage import RunStore

DPI = 200
WIDTH_INCHES = 16


def panel_counts(values, mask, limits):
    """Count displayed positive-intersection cells, including independent missing/overflow."""
    finite = np.isfinite(values) & mask
    return dict(total=int(mask.sum()), valid=int(finite.sum()), missing=int((mask & ~finite).sum()),
                below_scale=int((finite & (values < limits[0])).sum()),
                above_scale=int((finite & (values > limits[1])).sum()))


def render_native(path, crop, display, data, selected, provenance):
    """Draw fixed-scale native polygons and return exact export metadata for the manifest."""
    boundary = display['domain_5070']
    xmin, ymin, xmax, ymax = boundary.bounds
    center = np.array([(xmin+xmax)/2, (ymin+ymax)/2])
    ratio = (ymax-ymin)/(xmax-xmin)
    height_pixels = math.ceil(max(8., min(18., 5.5+8.*ratio)) * DPI)
    size = (WIDTH_INCHES*DPI, height_pixels)
    polygons = []
    for r,c in zip(*np.nonzero(crop['mask'])):
        x, y = crop['xx'][r,c], crop['yy'][r,c]
        dx, dy = crop['dx'], abs(crop['dy'])
        polygon = transform_rectangle((x-dx/2, y-dy/2, x+dx/2, y+dy/2), display['forward'], CELL_SEGMENT_M)
        polygons.append((np.asarray(polygon.exterior.coords)-center)/1000)
    outline_boundary = (np.asarray(boundary.exterior.coords)-center)/1000
    cmaps = color_maps()
    fig, axes = plt.subplots(2, 3, figsize=(WIDTH_INCHES, height_pixels/DPI), dpi=DPI,
                             sharex=True, sharey=True)
    fig.subplots_adjust(left=.065, right=.88, bottom=.15, top=.735, wspace=.09, hspace=.31)
    counts = {}
    try:
        for row, field in enumerate(('soil_moisture', 'reported_uncertainty')):
            for col, candidate in enumerate(ALGORITHMS):
                ax = axes[row,col]
                values = data[candidate][field]
                stats = panel_counts(values, crop['mask'], DEFAULT_LIMITS[row])
                counts[f'{candidate}/{field}'] = stats
                mesh = PolyCollection(polygons, array=np.ma.masked_invalid(values[crop['mask']]),
                                      cmap=cmaps[row], edgecolors='none', antialiaseds=False)
                mesh.set_clim(*DEFAULT_LIMITS[row])
                ax.add_collection(mesh)
                clip = Polygon(outline_boundary, closed=True, transform=ax.transData)
                mesh.set_clip_path(clip)
                flags = data[candidate]['retrieval_quality_flag'][crop['mask']]
                flagged = (flags >= 0) & ((flags & 1) != 0)
                outlines = PolyCollection([p for p, use in zip(polygons, flagged) if use],
                                           facecolors='none', edgecolors=ODD_COLOR,
                                           linewidths=.42, antialiaseds=False)
                ax.add_collection(outlines)
                outlines.set_clip_path(clip)
                ax.set(xlim=((xmin-center[0])/1000, (xmax-center[0])/1000),
                       ylim=((ymin-center[1])/1000, (ymax-center[1])/1000), aspect='equal')
                ax.tick_params(labelsize=9)
                if row == 1:
                    ax.set_xlabel('Easting from center (km)', fontsize=10)
                if col == 0:
                    ax.set_ylabel('Northing from center (km)', fontsize=10)
                field_label = 'Soil moisture' if row == 0 else 'Reported uncertainty'
                ax.set_title(f'{candidate} · {field_label}\nFinite: {100*stats["valid"]/stats["total"]:.1f}%'
                             f'\nNative recommended moisture: {selected["recommended_percent"][candidate]:.1f}%',
                             fontsize=10, pad=10)
                if not stats['valid']:
                    ax.text(.5,.5,'Unavailable (0 valid cells)', transform=ax.transAxes,
                            ha='center', va='center', fontsize=10,
                            bbox=dict(facecolor='white', alpha=.85, edgecolor='none'))
            position = axes[row,-1].get_position()
            cax = fig.add_axes([.90,position.y0,.013,position.height])
            bar = fig.colorbar(mesh, cax=cax, extend='both' if row == 0 else 'max')
            bar.set_label(('Soil moisture' if row == 0 else 'Reported uncertainty')+' (m³/m³)', fontsize=10)
            bar.ax.tick_params(labelsize=9)
        meta = selected['metadata']
        def label(key):
            value = meta[key]
            return ', '.join(map(str, value)) if isinstance(value, list) else str(value)
        title = f'Odessa · Native nominal 200 m reference · {str(meta["zeroDopplerStartTime"])[:10]} UTC'
        start, end = str(meta['zeroDopplerStartTime']), str(meta['zeroDopplerEndTime'])
        summary = (f'UTC: {start} — {end}\n'
                   f'Track {label("trackNumber")} · Frames {label("frameNumber")} · Orbit {label("absoluteOrbitNumber")} · {label("orbitPassDirection")}\n'
                   f'PROVISIONAL · Product {label("productVersion")} · Specification {label("productSpecificationVersion")} · Release {label("compositeReleaseId")}\n'
                   f'{len(selected["source_filenames"])} source granules · Native recommended-coverage score: {100*selected["score"]:.1f}%')
        fig.text(.5,.975,title,ha='center',va='top',fontsize=17,weight='bold')
        fig.text(.5,.935,summary,ha='center',va='top',fontsize=10,linespacing=1.65)
        short_names = [name if len(name) <= 88 else name[:42]+'…'+name[-42:] for name in selected['source_filenames']]
        fig.text(.5,.835,'\n'.join(short_names),ha='center',va='top',fontsize=9,family='monospace')
        fig.legend(handles=[Patch(facecolor=MISSING_COLOR,label='Missing / unavailable value'),
                            Line2D([],[],color=ODD_COLOR,lw=2,label='Non-recommended native candidate footprint')],
                   loc='lower center',bbox_to_anchor=(.49,.101),ncol=2,frameon=False,fontsize=10)
        bounds = crop['bounds_wgs84']
        footer = (f'Odessa WGS84: W {bounds[0]:.6f}°, S {bounds[1]:.6f}°, E {bounds[2]:.6f}°, N {bounds[3]:.6f}°\n'
                  f'Display EPSG:5070 · Native EPSG:6933, nominal 200 m (posting {crop["dx"]:.6f} m) · Original cell values\n'
                  'Fixed scales: moisture 0–0.60; uncertainty 0–0.08 m³/m³ · Red outlines describe native flags\n'
                  'Reported uncertainty is a conditional σ proxy (A05); bias and representation/model error are unquantified.\n'
                  'High-coverage visual example, not accuracy evidence · Active assumptions A01–A09, A18')
        fig.text(.5,.087,footer,ha='center',va='top',fontsize=9,linespacing=1.5)
        caption = title+'\n'+summary+'\n'+footer
        details = dict(caption=caption, panel_counts=counts, dimensions_px=list(size), dpi=DPI,
                       abbreviated_source_ids=sum(a != b for a,b in zip(short_names,selected['source_filenames'])),
                       source_filenames=selected['source_filenames'], source_provenance=provenance,
                       acquisition_id=selected['acquisition_id'], selected_coverage=selected,
                       display_crs='EPSG:5070', source_crs='EPSG:6933', units='m3 m-3',
                       native_posting_m=[crop['dx'],crop['dy']],
                       fixed_limits=DEFAULT_LIMITS, assumptions=[f'A{i:02d}' for i in range(1,10)]+['A18'])
        # Catch accidental canvas overflow before publishing. Visual review still checks overlap/readability.
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for text in fig.texts:
            box = text.get_window_extent(renderer)
            if box.x0 < 0 or box.y0 < 0 or box.x1 > size[0] or box.y1 > size[1]:
                raise ValueError('Figure annotation exceeds fixed canvas; no silent text clipping')
        metadata = {'Title': title, 'Description': caption, 'Provenance': canonical_json(details)}
        def write(destination):
            fig.savefig(destination, format='png', dpi=DPI, bbox_inches=None, metadata=metadata)
        def validate(file):
            with Image.open(file) as picture:
                if picture.size != size or picture.info.get('Provenance') != metadata['Provenance']:
                    raise ValueError('Existing/exported PNG dimensions or provenance mismatch')
                picture.verify()
        publish_file(path, write, validate)
        details['sha256'] = sha256_file(path)
        details['path'] = 'figures/odessa_native.png'
        return details
    finally:
        plt.close(fig)


def export_native(run, template, crop, slices, display, selected):
    """Read completed values and publish only the frozen native figure; verify existing output."""
    existing = run.manifest.get('figures', {}).get('native')
    path = run.root/'figures/odessa_native.png'
    code = {name: sha256_file(ROOT/name) for name in
            ('src/regridding/figures.py', 'src/nisar_visualization/style.py')}
    if existing:
        if existing['sha256'] != sha256_file(path) or existing['figure_code_sha256'] != code:
            raise ValueError('Existing figure checksum/code mismatch; refusing overwrite')
        return
    data = {}
    for candidate in ALGORITHMS:
        ds = run.read_task('native', selected['acquisition'], candidate)
        data[candidate] = {name: ds[name].values[slices].copy() for name in
                           ('soil_moisture', 'reported_uncertainty', 'retrieval_quality_flag')}
        del ds
    sources = [s for s in template.attrs['sources'] if s['filename'] in selected['source_filenames']]
    details = render_native(path, crop, display, data, selected,
                            dict(sources=sources, figure_code_sha256=code,
                                 selection_sha256=run.identity['selection_sha256']))
    details.update(figure_code_sha256=code, inspection_status='pending')
    run.manifest.setdefault('figures', {})['native'] = details
    run._save()


def record_inspection(output_dir, digest):
    """Record an explicit review of the exact complete native PNG; never infer review."""
    root = Path(output_dir)
    identity = json.loads((root/'run_manifest.json').read_text())['identity']
    with RunStore(root, identity, resume=True) as run:
        figure = run.manifest['figures']['native']
        if digest != figure['sha256'] or digest != sha256_file(root/figure['path']):
            raise ValueError('Inspection digest does not match the recorded PNG')
        import zarr
        group = zarr.open_group(root/'native.zarr',mode='r',use_consolidated=False)
        if not np.all(group['completion_status'][:] == 2):
            raise ValueError('Native tasks are incomplete')
        figure.update(inspection_status='passed', inspected_sha256=digest,
                      inspected_at_utc=datetime.now(timezone.utc).isoformat())
        run.manifest['native_stage_status'] = 'complete'
        run._save()
        print('Native stage complete; inspected PNG SHA-256: '+digest, flush=True)
