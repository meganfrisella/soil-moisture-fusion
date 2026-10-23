"""R10 east/south-tie nearest mapping and fixed-subdivision target geometry.

Run synthetic checks after pixi install --locked, from repository root:
    pixi run python -m unittest discover -s tests -p 'test_regridding_nearest.py'

``nearest_indices(x, y, native_layout, domain, eligible)`` maps inverse-transformed
EPSG:6933 metre coordinates to flattened native row-major indices (-1 if absent).
It uses floor from northwest outer corners, including west/north edges and
excluding east/south edges. No near-tie tolerance, halo, nearest-valid search or
clamping. Actual native HDF5 posting is retained, nominally 200 m.
``apply_nearest(moisture, sigma, indices)`` returns 90 m target fields in m³/m³;
sigma is copied only where moisture and native sigma are valid. Native zeros
remain valid. A shared source gives perfectly shared retrieval-error dependence;
unit coefficients plus source identities reconstruct W diag(sigma²) W.T.

``build_target_geometry(grid, native_layout)`` takes prepare_grid output and
returns static arrays/operator on the frozen EPSG:5070 lattice. It vectorizes the
existing fixed 30 m edge subdivisions, using 64-row by at-most-1024-column
batches to bound polygon memory within R33's approved maximum. No adaptive
refinement, dropped slivers or geometry repair. Clipped areas are EPSG:6933 m².
Routine batch-versus-scalar checks do not replace R23's scientific verification.
"""

import numpy as np
import shapely

from regridding.geometry import TARGET_RES_M, CELL_SEGMENT_M, rectangle_vertices
from regridding.run_plan import SPATIAL_CHUNKS


def nearest_indices(x, y, layout, domain, eligible):
    """Select the containing native cell with exact east/south midpoint tie handling."""
    x, y, eligible = np.asarray(x), np.asarray(y), np.asarray(eligible, dtype=bool)
    if x.shape != y.shape or x.shape != eligible.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Require matching finite native coordinates and eligibility')
    dx, dy = layout['dx'], layout['dy']
    left = layout['xo'] + layout['c0'] * dx - dx/2
    top = layout['yo'] + layout['r0'] * dy - dy/2
    height, width = layout['r1']-layout['r0'], layout['c1']-layout['c0']
    # Right-sided edge lookup implements floor's half-open convention without
    # rounding a coordinate immediately below a boundary onto the boundary.
    x_edges = left + np.arange(width+1)*dx
    y_edges = top + np.arange(height+1)*dy
    rows = np.searchsorted(-y_edges,-y,side='right')-1
    cols = np.searchsorted(x_edges,x,side='right')-1
    valid = eligible & (rows >= 0) & (cols >= 0) & (rows < height) & (cols < width)
    valid &= shapely.covers(domain, shapely.points(x, y))
    result = np.full(x.shape, -1, dtype='int64')
    r, c = rows[valid], cols[valid]
    # Explicit footprint and positive-area support tests, never a fallback to another source.
    cell_left, cell_right = x_edges[c], x_edges[c+1]
    cell_top, cell_bottom = y_edges[r], y_edges[r+1]
    xmin,ymin,xmax,ymax = domain.bounds
    contained = ((x[valid] >= cell_left) & (x[valid] < cell_right)
                 & (y[valid] <= cell_top) & (y[valid] > cell_bottom))
    intersects = ((cell_right > xmin) & (cell_left < xmax)
                  & (cell_top > ymin) & (cell_bottom < ymax))
    result[valid] = np.where(contained & intersects, r*width+c, -1)
    return result


def apply_nearest(moisture, sigma, indices):
    """Copy original values/standard deviations without searching across missing sources."""
    moisture, sigma, indices = np.asarray(moisture), np.asarray(sigma), np.asarray(indices)
    if moisture.shape != sigma.shape or indices.dtype.kind not in 'iu':
        raise ValueError('Require aligned native fields and integer source indices')
    if np.any(indices < -1) or np.any(indices >= moisture.size):
        raise ValueError('Invalid native source index')
    valid = indices >= 0
    source = indices[valid]
    out = np.full(indices.shape, np.nan, dtype='float64')
    uncertainty = np.full(indices.shape, np.nan, dtype='float64')
    out[valid] = moisture.ravel()[source]
    uncertainty[valid] = sigma.ravel()[source]
    mv = np.isfinite(out)
    uv = mv & np.isfinite(uncertainty) & (uncertainty >= 0)
    out[~mv] = np.nan
    uncertainty[~uv] = np.nan
    return dict(soil_moisture=out, retrieval_error_std=uncertainty,
                moisture_valid=mv, uncertainty_valid=uv)


def geometry_batch(grid, rows, columns):
    """Vectorize target_cells' exact fixed-edge construction for explicit matching indices."""
    rows, columns = np.asarray(rows), np.asarray(columns)
    height,width = grid['metadata']['shape_rows_columns']
    if rows.ndim!=1 or rows.shape!=columns.shape or rows.dtype.kind not in 'iu' or columns.dtype.kind not in 'iu':
        raise ValueError('Require matching one-dimensional integer indices')
    if np.any((rows<0)|(rows>=height)|(columns<0)|(columns>=width)):
        raise ValueError('Cell outside full-domain lattice')
    left, _, _, top = grid['metadata']['bounds_5070']
    side = TARGET_RES_M
    origin = np.column_stack((left+columns*side, top-(rows+1)*side))
    vertices = origin[:,None,:] + rectangle_vertices((0,0,side,side), CELL_SEGMENT_M)[None,:,:]
    xx, yy = grid['inverse'].transform(vertices[:,:,0].ravel(), vertices[:,:,1].ravel(), errcheck=True)
    coordinates = np.stack((xx, yy), axis=-1).reshape(vertices.shape)
    if not np.isfinite(coordinates).all():
        raise ValueError('Nonfinite transformed target edges')
    polygons = shapely.polygons(coordinates)
    if not np.all(shapely.is_valid(polygons)) or np.any(shapely.area(polygons) <= 0):
        raise ValueError('Invalid transformed target polygons; no repair')
    clipped = shapely.intersection(polygons, grid['domain_6933'], grid_size=0)
    area = shapely.area(clipped)
    partial = (area > 0) & (shapely.area(shapely.difference(polygons, grid['domain_6933'], grid_size=0)) > 0)
    cx, cy = grid['inverse'].transform(origin[:,0]+side/2, origin[:,1]+side/2, errcheck=True)
    if not np.isfinite(cx).all() or not np.isfinite(cy).all():
        raise ValueError('Nonfinite inverse target centers')
    centers = (area > 0) & shapely.covers(grid['domain_6933'], shapely.points(cx,cy))
    return area, centers, partial, np.asarray(cx), np.asarray(cy)


def build_target_geometry(grid, native_layout):
    """Construct complete static target geometry and fixed nearest operator in bounded batches."""
    height, width = grid['metadata']['shape_rows_columns']
    area = np.empty((height,width), dtype='float64')
    centers, partial = np.empty((height,width), dtype=bool), np.empty((height,width), dtype=bool)
    indices = np.full((height,width), -1, dtype='int64')
    operations = set()
    for r0 in range(0,height,64):
        r1 = min(r0+64,height)
        for c0 in range(0,width,SPATIAL_CHUNKS[1]):
            c1 = min(c0+SPATIAL_CHUNKS[1],width)
            rr,cc = np.meshgrid(np.arange(r0,r1),np.arange(c0,c1),indexing='ij')
            a,e,p,cx,cy = geometry_batch(grid,rr.ravel(),cc.ravel())
            shape = rr.shape
            area[r0:r1,c0:c1] = a.reshape(shape)
            centers[r0:r1,c0:c1] = e.reshape(shape)
            partial[r0:r1,c0:c1] = p.reshape(shape)
            indices[r0:r1,c0:c1] = nearest_indices(cx,cy,native_layout,grid['domain_6933'],e).reshape(shape)
            operations.add(grid['inverse'].get_last_used_operation().definition)
        if r0 % 512 == 0 or r1 == height:
            print(f'Target geometry: {r1}/{height} rows',flush=True)
    left,_,_,top = grid['metadata']['bounds_5070']
    return dict(epsg=5070,x=left+(np.arange(width)+.5)*TARGET_RES_M,
                y=top-(np.arange(height)+.5)*TARGET_RES_M, intersection_area_m2=area,
                center_eligible=centers, partial_cell=partial), indices, sorted(operations)


def diagnose_transform_batch(projection_cache, row_start=0, column_start=0):
    """Reproduce historical automatic-selection failure in a 64×1024 diagnostic batch.

    From repository root (no fields, downloads, or artifacts):
        PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.nearest import diagnose_transform_batch; print(json.dumps(diagnose_transform_batch("data/proj"),indent=2))'

    Uses errcheck=False ONLY to locate nonfinite diagnostic coordinates, then
    retries the first failure with strict errcheck=True. A pure inverse Albers
    projection locates the diagnostic point in NAD83 longitude/latitude, degrees;
    this is not a WGS84 datum fallback and is never consumed by field processing.
    """
    from pyproj import Transformer
    from pyproj.aoi import AreaOfInterest
    from pyproj.transformer import TransformerGroup
    from regridding.geometry import prepare_grid
    grid = prepare_grid((-121.241744,46.057370,-117.072163,48.076483),projection_cache)
    # Explicit historical policy only for this read-only diagnostic; production
    # uses the now-approved fixed Washington operation from prepare_grid.
    grid['inverse'] = Transformer.from_crs(5070,6933,always_xy=True,allow_ballpark=False,only_best=True,
                                          area_of_interest=AreaOfInterest(-121.241744,46.057370,-117.072163,48.076483))
    left,_,_,top = grid['metadata']['bounds_5070']
    height,width = grid['metadata']['shape_rows_columns']
    rows,cols = np.meshgrid(np.arange(row_start,min(row_start+64,height)),
                           np.arange(column_start,min(column_start+1024,width)),indexing='ij')
    origin = np.column_stack((left+cols.ravel()*90,top-(rows.ravel()+1)*90))
    vertices = origin[:,None,:]+rectangle_vertices((0,0,90,90),30)[None,:,:]
    x,y = vertices[:,:,0].ravel(),vertices[:,:,1].ravel()
    xx,yy = grid['inverse'].transform(x,y,errcheck=False)
    failures = np.flatnonzero(~np.isfinite(xx)|~np.isfinite(yy))
    result = dict(batch_origin=[row_start,column_start],vertices=len(x),failed_vertices=len(failures),examples=[],
                  diagnostic_inverse_policy='historical automatic selection; not the production pinned operation')
    geographic = Transformer.from_crs(5070,4269,always_xy=True,allow_ballpark=False,only_best=True)
    for index in failures[:3]:
        point = [float(x[index]),float(y[index])]
        entry = dict(target_epsg5070_m=point,nad83_lon_lat_degrees=list(geographic.transform(*point,errcheck=True)))
        try:
            entry['strict_individual_result_epsg6933_m']=list(grid['inverse'].transform(*point,errcheck=True))
        except Exception as error:
            entry['strict_error']=str(error)
        result['examples'].append(entry)
    result['grid_metadata']=grid['metadata']
    if len(failures):
        index = failures[0]
        candidates = TransformerGroup(5070,6933,always_xy=True,allow_ballpark=False,
                                      area_of_interest=AreaOfInterest(-121.241744,46.057370,-117.072163,48.076483))
        result['candidate_diagnostics']=[]
        for transformer in candidates.transformers:
            entry = dict(description=transformer.description,pipeline=transformer.definition,
                         reported_accuracy_m=transformer.accuracy)
            try:
                entry['first_failed_point_epsg6933_m']=list(transformer.transform(x[index],y[index],errcheck=True))
            except Exception as error:
                entry['error']=str(error)
            result['candidate_diagnostics'].append(entry)
    return result
