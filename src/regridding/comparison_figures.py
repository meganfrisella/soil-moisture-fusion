"""Render the original-grid reference and sequential nearest/bilinear comparisons.

From repository root, after the completed native stage and pixi install --locked:
    pixi run python scripts/regrid_eastern_washington.py --stage native --original-grid-figure --resume --output-dir outputs/eastern_washington/regridding
    pixi run python scripts/regrid_eastern_washington.py --stage nearest --resume --output-dir outputs/eastern_washington/regridding
    pixi run python scripts/regrid_eastern_washington.py --stage bilinear --resume --output-dir outputs/eastern_washington/regridding_washington

``export_comparison(run, mode, projection_cache)`` reads verified completed Zarr
tasks inside a locked RunStore; mode is native_epsg6933, nearest or bilinear. The frozen
Odessa acquisition/extent/scales are reused. Original-grid polygons use stored
EPSG:6933 metre centers and actual posting directly, without transformation or
resampling. Nearest uses EPSG:5070 target squares and transformed original flag
footprints. Values and conditional retrieval sigma are m³/m³, times UTC.
PNG captions, source IDs, counts and code hashes reside in PNG and run manifest;
no sidecars. ``inspect_comparison(output_dir, mode, digest)`` records an actual
visual review of that exact PNG; it does not assess visual quality automatically.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import shapely
from PIL import Image
import zarr

from ingest.downloads import publish_file
from nisar.quality import ALGORITHMS
from nisar_visualization.style import color_maps, DEFAULT_LIMITS, MISSING_COLOR, ODD_COLOR
from regridding.figures import DPI, WIDTH_INCHES, panel_counts, plt, PolyCollection, Polygon, Patch, Line2D
from regridding.geometry import prepare_grid, transform_rectangle, CELL_SEGMENT_M
from regridding.inputs import ROOT, sha256_file
from regridding.native_stage import ODESSA_BOUNDS
from regridding.schema import canonical_json
from regridding.storage import RunStore


def crop_native(group, domain):
    """Select original rectangles with positive-area Odessa intersection, without resampling."""
    x, y = group['x'][:], group['y'][:]
    dx, dy = group.attrs['posting_m']
    xmin, ymin, xmax, ymax = domain.bounds
    cols = np.flatnonzero((x+dx/2 > xmin) & (x-dx/2 < xmax))
    rows = np.flatnonzero((y-dy/2 > ymin) & (y+dy/2 < ymax))
    if not len(rows) or not len(cols):
        raise ValueError('Frozen Odessa crop has no native cells')
    slices = slice(rows[0], rows[-1]+1), slice(cols[0], cols[-1]+1)
    polygons = [shapely.box(xx-dx/2, yy+dy/2, xx+dx/2, yy-dy/2)
                for yy in y[slices[0]] for xx in x[slices[1]]]
    return slices, polygons


def render_comparison(path, mode, boundary, polygons, mask, flags_polygons, flags, data, selected, size, provenance):
    """Draw six panels with independent missing masks and original-source flag outlines."""
    original = mode == 'native_epsg6933'
    crs = 'EPSG:6933' if original else 'EPSG:5070'
    sigma_name = 'reported_uncertainty' if original else 'retrieval_error_std'
    xmin, ymin, xmax, ymax = boundary.bounds
    center = np.array([(xmin+xmax)/2, (ymin+ymax)/2])
    vertices = [(np.asarray(p.exterior.coords)-center)/1000 for p in polygons]
    flag_vertices = [(np.asarray(p.exterior.coords)-center)/1000 for p in flags_polygons]
    outline = (np.asarray(boundary.exterior.coords)-center)/1000
    cmaps = color_maps()
    fig, axes = plt.subplots(2,3,figsize=(WIDTH_INCHES,size[1]/DPI),dpi=DPI,sharex=True,sharey=True)
    fig.subplots_adjust(left=.065,right=.88,bottom=.15,top=.735,wspace=.09,hspace=.31)
    counts = {}
    try:
        for row, field in enumerate(('soil_moisture',sigma_name)):
            field_label = 'Soil moisture' if row == 0 else ('Reported uncertainty' if original else 'Retrieval-error standard deviation')
            for col, candidate in enumerate(ALGORITHMS):
                ax = axes[row,col]
                values = data[candidate][field].ravel()
                stats = panel_counts(values,mask,DEFAULT_LIMITS[row])
                counts[f'{candidate}/{field}'] = stats
                mesh = PolyCollection([p for p,m in zip(vertices,mask) if m],
                                      array=np.ma.masked_invalid(values[mask]),cmap=cmaps[row],
                                      edgecolors='none',antialiaseds=False)
                mesh.set_clim(*DEFAULT_LIMITS[row])
                ax.add_collection(mesh)
                clip = Polygon(outline,closed=True,transform=ax.transData)
                mesh.set_clip_path(clip)
                flagged = (flags[candidate] >= 0) & ((flags[candidate] & 1) != 0)
                lines = PolyCollection([p for p,f in zip(flag_vertices,flagged) if f],
                                       facecolors='none',edgecolors=ODD_COLOR,linewidths=.42,antialiaseds=False)
                ax.add_collection(lines)
                lines.set_clip_path(clip)
                ax.set(xlim=((xmin-center[0])/1000,(xmax-center[0])/1000),
                       ylim=((ymin-center[1])/1000,(ymax-center[1])/1000),aspect='equal')
                ax.tick_params(labelsize=9)
                if row == 1:
                    ax.set_xlabel('Easting from center (km)',fontsize=10)
                if col == 0:
                    ax.set_ylabel('Northing from center (km)',fontsize=10)
                ax.set_title(f'{candidate} · {field_label}\nFinite: {100*stats["valid"]/stats["total"]:.1f}%'
                             f'\nNative recommended moisture: {selected["recommended_percent"][candidate]:.1f}%',fontsize=10,pad=10)
                if not stats['valid']:
                    ax.text(.5,.5,'Unavailable (0 valid cells)',transform=ax.transAxes,ha='center',va='center',fontsize=10)
            position = axes[row,-1].get_position()
            cax = fig.add_axes([.90,position.y0,.013,position.height])
            bar = fig.colorbar(mesh,cax=cax,extend='both' if row == 0 else 'max')
            bar.set_label(field_label+' (m³/m³)',fontsize=10)
            bar.ax.tick_params(labelsize=9)
        meta = selected['metadata']
        def label(key):
            value = meta[key]
            return ', '.join(map(str,value)) if isinstance(value,list) else str(value)
        method = ('Original native grid · EPSG:6933' if original else
                  'Bilinear interpolation · 90 m EPSG:5070' if mode == 'bilinear' else
                  'Nearest neighbor · 90 m EPSG:5070')
        title = f'Odessa · {method} · {str(meta["zeroDopplerStartTime"])[:10]} UTC'
        summary = (f'UTC: {meta["zeroDopplerStartTime"]} — {meta["zeroDopplerEndTime"]}\n'
                   f'Track {label("trackNumber")} · Frames {label("frameNumber")} · Orbit {label("absoluteOrbitNumber")} · {label("orbitPassDirection")}\n'
                   f'PROVISIONAL · Product {label("productVersion")} · Specification {label("productSpecificationVersion")} · Release {label("compositeReleaseId")}\n'
                   f'{len(selected["source_filenames"])} source granules · Native recommended-coverage score: {100*selected["score"]:.1f}%')
        short = [n if len(n)<=88 else n[:42]+'…'+n[-42:] for n in selected['source_filenames']]
        fig.text(.5,.975,title,ha='center',va='top',fontsize=17,weight='bold')
        fig.text(.5,.935,summary,ha='center',va='top',fontsize=10,linespacing=1.65)
        fig.text(.5,.835,'\n'.join(short),ha='center',va='top',fontsize=9,family='monospace')
        fig.legend(handles=[Patch(facecolor=MISSING_COLOR,label='Missing / unavailable value'),
                            Line2D([],[],color=ODD_COLOR,lw=2,label='Non-recommended native candidate footprint')],
                   loc='lower center',bbox_to_anchor=(.49,.101),ncol=2,frameon=False,fontsize=10)
        west,south,east,north = ODESSA_BOUNDS
        support = 'Original native cells and values; no coordinate transformation or resampling' if original else 'Inverse-center nearest mapping; uncertainty copied from the same native source'
        assumptions = [f'A{i:02d}' for i in range(1,10)] + ([] if original else ['A10']) + ['A18']
        if mode == 'bilinear':
            support = 'Bilinear; all four neighbors required; σ from weighted native variances'
            assumptions = [f'A{i:02d}' for i in range(1,10)] + ['A12', 'A18']
        footer = (f'Odessa WGS84: W {west:.6f}°, S {south:.6f}°, E {east:.6f}°, N {north:.6f}°\n'
                  f'Display {crs} · Native EPSG:6933, nominal 200 m · {support}\n'
                  'Fixed scales: moisture 0–0.60; uncertainty 0–0.08 m³/m³ · Red outlines describe native flags\n'
                  'Retrieval uncertainty is conditional on the σ proxy (A05); bias and representation/model error are unquantified.\n'
                  'High-coverage visual example, not accuracy evidence · Active assumptions '+', '.join(assumptions))
        fig.text(.5,.087,footer,ha='center',va='top',fontsize=9,linespacing=1.5)
        details = dict(caption=title+'\n'+summary+'\n'+footer,panel_counts=counts,dimensions_px=list(size),dpi=DPI,
                       display_crs=crs,source_crs='EPSG:6933',units='m3 m-3',assumptions=assumptions,
                       acquisition_id=selected['acquisition_id'],source_filenames=selected['source_filenames'],
                       selected_coverage=selected,source_provenance=provenance,fixed_limits=DEFAULT_LIMITS,
                       abbreviated_source_ids=sum(a!=b for a,b in zip(short,selected['source_filenames'])))
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for annotation in fig.texts:
            bounds = annotation.get_window_extent(renderer)
            if bounds.x0<0 or bounds.y0<0 or bounds.x1>size[0] or bounds.y1>size[1]:
                raise ValueError('Annotation exceeds fixed canvas')
        metadata = dict(Title=title,Description=details['caption'],Provenance=canonical_json(details))
        def write(destination):
            fig.savefig(destination,format='png',dpi=DPI,bbox_inches=None,metadata=metadata)
        def validate(destination):
            with Image.open(destination) as picture:
                if picture.size!=tuple(size) or picture.info.get('Provenance')!=metadata['Provenance']:
                    raise ValueError('PNG dimensions/provenance mismatch')
                picture.verify()
        publish_file(path,write,validate)
        details.update(path=f'figures/odessa_{mode}.png',sha256=sha256_file(path),inspection_status='pending')
        return details
    finally:
        plt.close(fig)


def export_comparison(run, mode, projection_cache):
    """Publish from completed fields, preserving the frozen acquisition and native figure."""
    if mode not in ('native_epsg6933','nearest','bilinear'):
        raise ValueError('Unsupported comparison figure')
    if run.manifest.get('native_stage_status') != 'complete':
        raise ValueError('Complete and inspect native reference first')
    if mode == 'bilinear' and run.manifest.get('nearest_stage_status') != 'complete':
        raise ValueError('Complete and inspect nearest before bilinear')
    code = {name:sha256_file(ROOT/name) for name in
            ('src/regridding/comparison_figures.py','src/regridding/figures.py',
             'src/regridding/geometry.py','src/nisar_visualization/style.py')}
    existing = run.manifest.get('figures',{}).get(mode)
    if existing:
        if existing['figure_code_sha256'] != code or sha256_file(run.root/existing['path']) != existing['sha256']:
            raise ValueError('Existing comparison figure/code changed; refusing overwrite')
        return existing
    native = zarr.open_group(run.root/'native.zarr',mode='r',use_consolidated=False)
    if not np.all(native['completion_status'][:]==2):
        raise ValueError('Native tasks incomplete')
    selected = run.manifest['visual_selection']
    frozen = run.manifest['display_geometry']
    display = {'domain_6933':shapely.box(*frozen['native_domain_bounds_6933'])}
    if mode in ('nearest', 'bilinear'):
        display = prepare_grid(ODESSA_BOUNDS,projection_cache)
        for key in ('bounds_5070','native_domain_bounds_6933','last_executed_operations','settings','geometry_code_sha256'):
            if canonical_json(display['metadata'][key]) != canonical_json(frozen[key]):
                raise ValueError(f'Frozen display geometry changed: {key}')
    slices, original_polygons = crop_native(native,display['domain_6933'])
    a = selected['acquisition']
    flags = {candidate:native['retrieval_quality_flag'][a,c,*slices].ravel() for c,candidate in enumerate(ALGORITHMS)}
    if mode == 'native_epsg6933':
        group,polygons,flag_polygons,boundary = native,original_polygons,original_polygons,display['domain_6933']
        mask = np.ones(len(polygons),dtype=bool)
        sigma = 'reported_uncertainty'
    else:
        group = zarr.open_group(run.root/f'{mode}.zarr',mode='r',use_consolidated=False)
        if not np.all(group['completion_status'][:]==2):
            raise ValueError(f'{mode} tasks incomplete')
        boundary = display['domain_5070']
        slices,polygons = crop_native(group,boundary)
        mask = shapely.area(shapely.intersection(polygons,boundary,grid_size=0))>0
        flag_polygons = [transform_rectangle(p.bounds,display['forward'],CELL_SEGMENT_M) for p in original_polygons]
        sigma = 'retrieval_error_std'
    data = {candidate:{field:group[field][a,c,*slices] for field in ('soil_moisture',sigma)}
            for c,candidate in enumerate(ALGORITHMS)}
    provenance = dict(figure_code_sha256=code,selection_sha256=run.identity['selection_sha256'],
                      sources=[s for s in run.identity['sources'] if s['filename'] in selected['source_filenames']],
                      native_posting_m=native.attrs['posting_m'],display_center_units='km',
                      command='--stage native --original-grid-figure --resume' if mode=='native_epsg6933' else f'--stage {mode} --resume')
    details = render_comparison(run.root/f'figures/odessa_{mode}.png',mode,boundary,polygons,mask,
                                flag_polygons,flags,data,selected,run.manifest['figures']['native']['dimensions_px'],provenance)
    details['figure_code_sha256'] = code
    run.manifest.setdefault('figures',{})[mode] = details
    run._save()
    print(json.dumps(details,indent=2),flush=True)
    return details


def inspect_comparison(output_dir, mode, digest):
    """Record an explicit visual review; method completion additionally requires every task."""
    if mode not in ('native_epsg6933', 'nearest', 'bilinear'):
        raise ValueError('Unsupported comparison figure')
    root = Path(output_dir)
    identity = json.loads((root/'run_manifest.json').read_text())['identity']
    with RunStore(root,identity,resume=True) as run:
        figure = run.manifest['figures'][mode]
        if digest!=figure['sha256'] or digest!=sha256_file(root/figure['path']):
            raise ValueError('Inspection digest does not match recorded PNG')
        method = 'native' if mode=='native_epsg6933' else mode
        group = zarr.open_group(root/f'{method}.zarr',mode='r',use_consolidated=False)
        if not np.all(group['completion_status'][:]==2):
            raise ValueError('Stage tasks incomplete')
        figure.update(inspection_status='passed',inspected_sha256=digest,
                      inspected_at_utc=datetime.now(timezone.utc).isoformat())
        if mode in ('nearest', 'bilinear'):
            run.manifest[f'{mode}_stage_status']='complete'
        run._save()
        print(f'{mode}: inspected {digest}',flush=True)
