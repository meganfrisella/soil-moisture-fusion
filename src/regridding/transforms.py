"""Check offline coordinate-operation availability before R02 geometry work.

After ``pixi install``, run from the repository root:
    PYTHONPATH=src:scripts pixi run python -c 'import json; from regridding.transforms import inspect_transformations; print(json.dumps(inspect_transformations((-121.241744, 46.057370, -117.072163, 48.076483)), indent=2))'

The first argument is (west, south, east, north), WGS84 longitude/latitude degrees.
Pass a second argument ``"data/proj"`` to verify and explicitly register the
approved local grid before inspection. This checks area-ranked candidates only;
``ready`` does not guarantee per-coordinate availability across the entire domain.
This callable module has no CLI, file outputs, downloads, or credential needs.
It disables PROJ network access and inspects required CRS pairs without applying
coordinate transformations. ``status == "unavailable"`` means stop: no lower
accuracy operation is selected. Available-operation accuracy is supplier-reported
in metres; it is not a numerical geometry tolerance or measured local accuracy.
This time-independent check does not read moisture, build a grid, or quantify UP.
"""

from pathlib import Path
import platform
import os
import warnings

import pyproj
from pyproj import database, datadir, network
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup

from nisar.geometry import BoundingBox
from regridding.inputs import sha256_file
from regridding.resources import configure_local_grid


CRS_PAIRS = ((4326, 6933), (6933, 5070), (5070, 6933))


def inspect_transformations(bounds_wgs84, cache_dir=None):
    """Return operation/grid diagnostics; never transform using a fallback.

    PROJ ranks candidate operations by coverage of the supplied area of interest
    and accuracy. Its ``best_available`` flag, rather than merely the presence of
    some usable transformer, determines candidate readiness. Actual transforms
    must still use only_best=True and errcheck=True: point-dependent operation
    selection can require a different grid. Missing grids are reported for
    unavailable candidate operations; not all such grids are necessarily needed.
    """
    bounds = BoundingBox(*bounds_wgs84)
    network.set_network_enabled(False)
    if cache_dir is not None:
        configure_local_grid(cache_dir)
    records = []
    for source, target in CRS_PAIRS:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            group = TransformerGroup(
                source, target, always_xy=True, allow_ballpark=False,
                area_of_interest=AreaOfInterest(*bounds.bounds))
        unavailable = []
        for operation in group.unavailable_operations:
            unavailable.append({
                "name": operation.name, "reported_accuracy_m": operation.accuracy,
                "grids": [{
                    "name": grid.short_name, "full_name": grid.full_name,
                    "available": grid.available, "url": grid.url,
                    "open_license": grid.open_license,
                    "direct_download": grid.direct_download,
                } for grid in operation.grids],
            })
        available = []
        for transformer in group.transformers:
            grids = {}
            for operation in transformer.operations:
                for grid in operation.grids:
                    path = Path(grid.full_name) if grid.full_name else None
                    grids[grid.short_name] = {
                        "name": grid.short_name, "full_name": grid.full_name,
                        "sha256": sha256_file(path) if path and path.is_file() else None,
                    }
            available.append({
                "description": transformer.description,
                "reported_accuracy_m": transformer.accuracy,
                "candidate_pipeline": transformer.definition,
                "local_grids": list(grids.values()),
            })
        records.append({
            "source_crs": f"EPSG:{source}", "target_crs": f"EPSG:{target}",
            "best_available": bool(group.best_available and group.transformers),
            "available_candidates": available, "unavailable_candidates": unavailable,
            "warnings": [str(item.message) for item in caught],
        })
    root = Path(__file__).resolve().parents[2]
    proj_database = next(Path(part) / "proj.db"
                         for part in datadir.get_data_dir().split(os.pathsep)
                         if (Path(part) / "proj.db").is_file())
    return {
        "status": "ready" if all(r["best_available"] for r in records) else "unavailable",
        "bounds_wgs84": dict(zip(("west", "south", "east", "north"), bounds.bounds)),
        "network_enabled": network.is_network_enabled(),
        "coordinate_transformations_executed": False,
        "operations": records,
        "environment": {
            "python": platform.python_version(), "pyproj": pyproj.__version__,
            "proj": pyproj.proj_version_str,
            "epsg_version": database.get_database_metadata("EPSG.VERSION"),
            "epsg_date": database.get_database_metadata("EPSG.DATE"),
            "proj_database_path": str(proj_database),
            "proj_database_sha256": sha256_file(proj_database),
            "proj_data_dir": datadir.get_data_dir(),
            "proj_user_data_dir": datadir.get_user_data_dir(create=False),
        },
        "code_sha256": {name: sha256_file(root / name) for name in (
            "src/regridding/transforms.py", "src/regridding/inputs.py",
            "src/regridding/resources.py",
            "src/nisar/geometry.py", "pixi.toml", "pixi.lock")},
        "limitations": [
            "Candidate pipelines are catalog descriptions, not executed transformations.",
            "Area-ranked availability does not prove per-coordinate transform availability.",
            "Missing candidate grids are not automatically authorized downloads.",
            "No grid geometry, source data, uncertainty, or method outputs computed.",
        ],
    }
