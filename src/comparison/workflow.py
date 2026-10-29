"""Compare existing prepared NISAR/SMAP stores; emit five figures and compact results without derived Zarr stores."""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from comparison.metrics import MAX_OFFSET_HOURS, MIN_PAIRS_PER_CELL, compare, match_smap
from comparison.figures import render_all
from comparison.provenance import verify_store, run_record, sha256_file, write_json
from smap.l2e import recommended
from smap.ease2 import cell_centers

ALGORITHMS = ("PMI", "TSR", "DSG")
ALGORITHM_PAIRS = (("PMI", "TSR"), ("PMI", "DSG"), ("TSR", "DSG"))
SCREENS = ("all", "recommended")
CELL_FIELDS = ("n_pairs", "bias", "r_temporal", "ubrmsd_temporal",
               "n_sigma_pairs", "sigma_mean", "coverage")


def run(input_dir, output_dir, command):
    """Verify prepared stores, compare unchanged methods and publish into a new directory."""
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    records = {name: verify_store(input_dir / (name + ".zarr"), input_dir / (name + ".json"))
               for name in ("smap_l2e_box", "nisar_9km")}
    if sha256_file(input_dir / "smap_l2e_manifest.json") != records["smap_l2e_box"]["manifest_sha256"]:
        raise ValueError("SMAP selection manifest changed after preparation")
    output_dir.mkdir(parents=True, exist_ok=False)
    with xr.open_zarr(input_dir / "smap_l2e_box.zarr", consolidated=False) as source:
        smap = source.load()
    with xr.open_zarr(input_dir / "nisar_9km.zarr", consolidated=False) as source:
        nisar = source.load()
    if not (np.array_equal(smap.row, nisar.row) and np.array_equal(smap.col, nisar.col)):
        raise ValueError("SMAP and NISAR stores are on different cells")

    expected_y, expected_x = cell_centers(smap.row.values, smap.col.values)
    for product in (smap, nisar):
        if not (np.array_equal(product.y.values, expected_y)
                and np.array_equal(product.x.values, expected_x)):
            raise ValueError("Prepared coordinates disagree with EPSG:6933 grid labels")
        if product.soil_moisture.attrs.get("units") != "m3 m-3":
            raise ValueError("Unexpected prepared moisture units")

    times = nisar.acquisition_mid.values
    moisture, offset, granule = match_smap(times, smap.tb_time_utc.values, smap.swath_cell.values,
                                           smap.soil_moisture.values)
    good = recommended(smap.soil_moisture.values, smap.retrieval_qual_flag.values)
    matched_good = np.zeros(granule.shape, bool)
    t, r, c = np.nonzero(granule >= 0)
    matched_good[t, r, c] = good[granule[t, r, c], r, c]
    smap_fields = {"all": moisture, "recommended": np.where(matched_good, moisture, np.nan)}

    def field(name, screen):
        return nisar.soil_moisture.sel(candidate=name, screen=screen).values

    def sigma(name, screen):
        return nisar.sigma.sel(candidate=name, screen=screen).values

    comparisons = []  # (label, group, screen, a, b, sigma_a)
    for screen in SCREENS:
        for name in (*ALGORITHMS, "MEAN"):
            comparisons.append((f"{name} - SMAP", "nisar_smap", screen, field(name, screen),
                                smap_fields[screen], sigma(name, screen)))
        for a, b in ALGORITHM_PAIRS:
            comparisons.append((f"{a} - {b}", "nisar_pairs", screen, field(a, screen),
                                field(b, screen), None))

    labels = list(dict.fromkeys(c[0] for c in comparisons))
    shape = (len(labels), len(SCREENS), nisar.sizes["row"], nisar.sizes["col"])
    cell = {name: np.full(shape, np.nan) for name in CELL_FIELDS}
    summaries = []
    for label, group, screen, a, b, sigma_a in comparisons:
        per_cell, summary = compare(a, b, sigma_a)
        for name, values in per_cell.items():
            cell[name][labels.index(label), SCREENS.index(screen)] = values
        summaries.append({"comparison": label, "group": group, "screen": screen, **summary})
    summary = pd.DataFrame(summaries)
    cells = xr.Dataset({name: (("comparison", "screen", "row", "col"), values)
                        for name, values in cell.items()},
                       coords={"comparison": labels, "screen": list(SCREENS),
                               "group": ("comparison", [next(c[1] for c in comparisons if c[0] == k)
                                                        for k in labels]),
                               "row": nisar.row.values, "col": nisar.col.values,
                               "y": nisar.y, "x": nisar.x})
    cells.coverage.attrs["description"] = "share of matched pairs with |NISAR - SMAP| <= sigma_NISAR"
    for name in ("bias", "ubrmsd_temporal", "sigma_mean"):
        cells[name].attrs["units"] = "m3 m-3"

    out = output_dir
    summary.to_csv(out / "summary.csv", index=False)
    coverage = {(r["comparison"], r["screen"]): r.get("coverage_1sigma") for r in summaries
                if r.get("coverage_1sigma") is not None}
    figures = render_all(cells, coverage, out)

    used = np.isfinite(offset) & np.isfinite(moisture)
    record = {"run": run_record(command),
              "inputs": {name: sha256_file(input_dir / name)
                         for name in ("smap_l2e_box.json", "nisar_9km.json")},
              "rules": {"max_offset_hours": MAX_OFFSET_HOURS,
                        "smap_selection": "time-nearest covering pass; earlier on ties; "
                                          "missing if not usable under the screen, no farther fallback",
                        "screens": {"all": "only fill/NaN excluded",
                                    "recommended": "NISAR candidate flag bit 0 clear at 200 m; "
                                                   "SMAP retrieval_qual_flag bit 0 clear"},
                        "nisar_mean": "200 m mean of PMI, TSR, DSG where all three pass the screen",
                        "nisar_time": "acquisition midpoint, UTC",
                        "min_pairs_per_cell": MIN_PAIRS_PER_CELL,
                        "difference": "first - second (NISAR - SMAP; first - second algorithm)",
                        "pooled": "equal weight per matched cell-time pair",
                        "uncertainty_check": {"nisar_sigma": "linear mean of reported 200 m sigma "
                                                             "(fully correlated pixel errors); NISAR "
                                                             "mean uses the mean of three sigma",
                                              "coverage": "share of matched pairs with valid sigma "
                                                          "where |NISAR - SMAP| <= sigma_NISAR",
                                              "smap_error": "not included (SPL2SMP_E "
                                                            "soil_moisture_error is all fill)"}},
              "smap_offsets_hours": {"used_pairs": int(used.sum()),
                                     "min": float(offset[used].min()) if used.any() else None,
                                     "max": float(offset[used].max()) if used.any() else None,
                                     "median_abs": float(np.median(np.abs(offset[used]))) if used.any() else None},
              "summary": summaries,
              "figures": {name: sha256_file(out / name) for name in figures}}
    write_json(out / "results.json", record)
    print(summary.to_string(index=False, float_format=lambda v: f"{v:.3f}"), flush=True)
    return record
