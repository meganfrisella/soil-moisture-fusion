"""Verify and pin the user-approved Washington datum operation for R02.

From repository root after pixi install --locked and the approved PROJ downloads:
    PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.geometry import prepare_grid; from regridding.washington import verify_required_grid; g=prepare_grid((-121.241744,46.057370,-117.072163,48.076483),"data/proj"); print(json.dumps(verify_required_grid(g),indent=2))'

``washington_operations(bounds_wgs84, cache_dir)`` requires the exact Washington
pipeline in the local PROJ catalog, matching its operation ID and grid checksum;
it returns fixed forward/inverse transformers and provenance. No best-candidate
ranking, location-dependent selection, network or fallback is used for execution.
``verify_required_grid(grid)`` checks every target center and fixed edge vertex,
in bounded batches, before native/nearest field execution. It checks finite
transforms, operation area of use for every positive-area contributing cell,
and reports round-trip residuals in EPSG:5070 metres as an engineering diagnostic.
Exterior bounding-grid padding remains zero-support; successful evaluation there
does not assert geographic validity outside the operation's stated area of use.
This is not R23 scientific geometry verification or geolocation calibration.
Only console output / the existing run manifest is used, with no extra artifacts.
"""

from pathlib import Path

import numpy as np
from pyproj import Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup
import shapely

from regridding.inputs import sha256_file
from regridding.resources import configure_local_grid, GRID_NAME, GRID_SPECS

FORWARD_PIPELINE = ('proj=pipeline step inv proj=cea lat_ts=30 lon_0=0 x_0=0 y_0=0 ellps=WGS84 '
                    'step inv proj=hgridshift grids=us_noaa_WO.tif '
                    'step proj=aea lat_0=23 lon_0=-96 lat_1=29.5 lat_2=45.5 x_0=0 y_0=0 ellps=GRS80')
INVERSE_PIPELINE = ('proj=pipeline step inv proj=aea lat_0=23 lon_0=-96 lat_1=29.5 lat_2=45.5 x_0=0 y_0=0 ellps=GRS80 '
                    'step proj=hgridshift grids=us_noaa_WO.tif '
                    'step proj=cea lat_ts=30 lon_0=0 x_0=0 y_0=0 ellps=WGS84')
POLICY = 'fixed-Washington-EPSG1737-WO-v1'


def contains_coordinates(lon, lat, bounds):
    """Test closed operation area-of-use bounds in geographic degrees, without buffering."""
    west,south,east,north = bounds
    return np.isfinite(lon) & np.isfinite(lat) & (lon>=west) & (lon<=east) & (lat>=south) & (lat<=north)


def washington_operations(bounds_wgs84, cache_dir):
    """Require the named operation, exact pipeline and verified local file in both directions."""
    directory = configure_local_grid(cache_dir)
    result = {}
    records = {}
    for key,source,target,pipeline in (('forward',6933,5070,FORWARD_PIPELINE),
                                       ('inverse',5070,6933,INVERSE_PIPELINE)):
        group = TransformerGroup(source,target,always_xy=True,allow_ballpark=False,
                                 area_of_interest=AreaOfInterest(*bounds_wgs84))
        candidates = [t for t in group.transformers if t.definition==pipeline]
        if len(candidates)!=1:
            raise ValueError(f'Exact Washington {key} pipeline absent or ambiguous; no fallback')
        candidate = candidates[0]
        datum = [op for op in candidate.operations
                 if op.to_json_dict().get('id',{}).get('code')==1737]
        if len(datum)!=1 or [g.short_name for g in datum[0].grids]!=[GRID_NAME]:
            raise ValueError('Washington datum-operation ID/grid changed')
        grid = datum[0].grids[0]
        if not grid.available or not grid.full_name or Path(grid.full_name).resolve()!=directory/GRID_NAME:
            raise ValueError('Washington operation did not resolve to the verified local grid')
        if sha256_file(Path(grid.full_name))!=GRID_SPECS[GRID_NAME][1]:
            raise ValueError('Resolved Washington grid checksum changed')
        area = candidate.area_of_use
        area_bounds = [area.west,area.south,area.east,area.north]
        west,south,east,north = bounds_wgs84
        if not contains_coordinates(np.array([west,east]),np.array([south,north]),area_bounds).all():
            raise ValueError('Study domain outside Washington operation area of use')
        result[key] = Transformer.from_pipeline(pipeline)
        if not result[key].has_inverse or result[key].is_network_enabled:
            raise ValueError('Require an invertible offline Washington pipeline')
        records[key] = dict(pipeline=pipeline,description=candidate.description,
                            reported_accuracy_m=candidate.accuracy,area_of_use_name=area.name,
                            area_of_use_bounds_wgs84=area_bounds,datum_operation=datum[0].to_json_dict(),
                            grid_path=str(directory/GRID_NAME),grid_sha256=GRID_SPECS[GRID_NAME][1])
    result['metadata'] = dict(policy=POLICY,operations=records,code_sha256=sha256_file(Path(__file__)),
                              network_enabled=False,selection='Explicit fixed pipeline; no point-dependent selection or fallback')
    return result


def verify_required_grid(grid):
    """Verify every required target vertex/center; reject out-of-area contributing cells."""
    from regridding.geometry import rectangle_vertices, TARGET_RES_M, CELL_SEGMENT_M
    policy = grid['metadata']['pinned_operation']
    if policy['policy']!=POLICY:
        raise ValueError('Verification requires the pinned Washington operation')
    bounds = policy['operations']['inverse']['area_of_use_bounds_wgs84']
    to_geographic = Transformer.from_crs(6933,4326,always_xy=True,allow_ballpark=False,only_best=True)
    to_nad83 = Transformer.from_crs(5070,4269,always_xy=True,allow_ballpark=False,only_best=True)
    height,width = grid['metadata']['shape_rows_columns']
    left,_,_,top = grid['metadata']['bounds_5070']
    side = TARGET_RES_M
    # Closing vertex is deliberately included, matching production geometry.
    offsets = np.vstack((rectangle_vertices((0,0,side,side),CELL_SEGMENT_M),[side/2,side/2]))
    count = 0
    outside_cells = 0
    outside_points = 0
    max_error = 0.
    geographic_bounds = np.array([np.inf,np.inf,-np.inf,-np.inf])
    for r0 in range(0,height,64):
        for c0 in range(0,width,1024):
            rr,cc = np.meshgrid(np.arange(r0,min(r0+64,height)),np.arange(c0,min(c0+1024,width)),indexing='ij')
            origin = np.column_stack((left+cc.ravel()*side,top-(rr.ravel()+1)*side))
            points = origin[:,None,:]+offsets[None,:,:]
            x,y = points[:,:,0].ravel(),points[:,:,1].ravel()
            nx,ny = grid['inverse'].transform(x,y,errcheck=True)
            rx,ry = grid['forward'].transform(nx,ny,errcheck=True)
            lon,lat = to_geographic.transform(nx,ny,errcheck=True)
            nad_lon,nad_lat = to_nad83.transform(x,y,errcheck=True)
            if not np.isfinite([nx,ny,rx,ry,lon,lat,nad_lon,nad_lat]).all():
                raise ValueError('Nonfinite Washington verification coordinate')
            inside = contains_coordinates(lon,lat,bounds) & contains_coordinates(nad_lon,nad_lat,bounds)
            outside = (~inside).reshape(points.shape[:2])
            suspect = outside.any(axis=1)
            if suspect.any():
                native_points = np.stack((nx,ny),axis=-1).reshape(points.shape)
                polygons = shapely.polygons(native_points[suspect,:-1,:])
                if not shapely.is_valid(polygons).all():
                    raise ValueError('Invalid padding polygon during operation verification')
                if np.any(shapely.area(shapely.intersection(polygons,grid['domain_6933'],grid_size=0))>0):
                    raise ValueError('A contributing target cell extends beyond Washington operation area of use')
                outside_cells += int(suspect.sum())
                outside_points += int(outside.sum())
            geographic_bounds[:2] = np.minimum(geographic_bounds[:2],[np.min(lon),np.min(lat)])
            geographic_bounds[2:] = np.maximum(geographic_bounds[2:],[np.max(lon),np.max(lat)])
            max_error = max(max_error,float(np.hypot(rx-x,ry-y).max()))
            count += len(x)
        if r0%256==0 or r0+64>=height:
            print(f'Washington operation verification: {min(r0+64,height)}/{height} rows',flush=True)
    return dict(status='passed',policy=POLICY,target_cells=height*width,checked_vertex_center_entries=count,
                failed_transforms=0,outside_area_of_use_cells=outside_cells,
                outside_area_of_use_vertex_center_entries=outside_points,
                outside_area_of_use_contributing_cells=0,geographic_bounds_wgs84=geographic_bounds.tolist(),
                max_roundtrip_residual_epsg5070_m=max_error,area_of_use_bounds_wgs84=bounds,
                limitations='Area of use checked on all fixed vertices/centers; not a continuous-edge bound or geolocation accuracy test. Exterior padding has zero domain intersection; no scientific outputs use it.')
