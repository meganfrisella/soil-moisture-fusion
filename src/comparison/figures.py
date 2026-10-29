"""Render NISAR/SMAP agreement on EPSG:6933 9 km cells; moisture and differences in m³/m³."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MultipleLocator
import matplotlib.pyplot as plt
import numpy as np
from pyproj import Transformer

from comparison.metrics import MIN_PAIRS_PER_CELL
from smap.ease2 import CELL_M

TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
DIVERGING = LinearSegmentedColormap.from_list(
    "red_gray_blue", ["#8f1f1f", "#e34948", "#f3b5b4", "#f0efec", "#b7d3f6", "#2a78d6", "#0d366b"])
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "blue", ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"])
for cmap in (DIVERGING, SEQUENTIAL):
    cmap.set_bad(alpha=0)
TOWNS = {"Spokane": (47.66, -117.43), "Yakima": (46.60, -120.51), "Tri-Cities": (46.23, -119.15),
         "Moses Lake": (47.13, -119.28), "Wenatchee": (47.42, -120.31), "Pullman": (46.73, -117.18),
         "Ritzville": (47.13, -118.38), "Walla Walla": (46.07, -118.34)}
SCREEN_TITLES = {"all": "all values", "recommended": "recommended only"}
DISPLAY = {"MEAN": "NISAR mean"}
WINDOW = "2026-06-17 – 2026-10-10 UTC"
SIGMA_HATCH = (f"fewer than {MIN_PAIRS_PER_CELL} matched pairs with valid NISAR σ "
               "(includes cells removed by the screen)")

plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": TEXT,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 8.5,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                     "savefig.facecolor": SURFACE})


def _edges(cells):
    """Longitude and latitude cell edges from EPSG:6933 centres."""
    x = np.append(cells.x.values - CELL_M / 2, cells.x.values[-1] + CELL_M / 2)
    y = np.append(cells.y.values + CELL_M / 2, cells.y.values[-1] - CELL_M / 2)
    to_geo = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True)
    lon, _ = to_geo.transform(x, np.full_like(x, y.mean()))
    _, lat = to_geo.transform(np.full_like(y, x.mean()), y)
    return lon, lat


def _name(label, nisar_only=False):
    first, second = (p.strip() for p in label.split(" - "))
    if nisar_only:
        return DISPLAY.get(first, f"NISAR {first}")
    return f"{DISPLAY.get(first, first)} − {second}"


def _panel(ax, lon, lat, values, cmap, norm, title):
    ax.add_patch(Rectangle((lon[0], lat[-1]), lon[-1] - lon[0], lat[0] - lat[-1],
                           facecolor=SURFACE, edgecolor=GRID, hatch="////", linewidth=0, zorder=0))
    mesh = ax.pcolormesh(lon, lat, np.ma.masked_invalid(values), cmap=cmap, norm=norm, zorder=1)
    for town, (la, lo) in TOWNS.items():
        ax.plot(lo, la, "o", ms=2.5, color=TEXT, zorder=3)
        ax.annotate(town, (lo, la), xytext=(2, 2), textcoords="offset points", fontsize=6,
                    color=TEXT, zorder=3)
    ax.set_xlim(lon[0], lon[-1])
    ax.set_ylim(lat[-1], lat[0])
    ax.set_aspect(1 / np.cos(np.deg2rad(lat.mean())))
    ax.set_title(title, loc="left", color=TEXT)
    ax.tick_params(labelsize=6)
    ax.xaxis.set_major_locator(MultipleLocator(1.0))
    ax.yaxis.set_major_locator(MultipleLocator(0.5))
    return mesh


def _grid(cells, labels, columns, heading, out, name, nisar_only=False, hatch_label=None):
    """Columns = comparisons; rows = specs (field, screen, colour scale, subtitle).

    Each column spec is a dict with field, screen, count (pair-count field), cmap,
    norm, stat(values) and bar (colorbar label); all columns share one colour scale.
    An optional note(label, screen) adds a third subtitle line.
    """
    lon, lat = _edges(cells)
    fig, axes = plt.subplots(len(columns), len(labels),
                             figsize=(4.4 * len(labels), 2.75 * len(columns) + 1.3),
                             constrained_layout=True, squeeze=False)
    for i, label in enumerate(labels):
        for j, spec in enumerate(columns):
            values = cells[spec["field"]].sel(comparison=label, screen=spec["screen"]).values
            n = cells[spec["count"]].sel(comparison=label, screen=spec["screen"]).values
            mapped = np.isfinite(values)
            detail = (f"{spec['stat'](values)} · {int(mapped.sum())} cells, "
                      f"{int(n[mapped].min())}–{int(n[mapped].max())} pairs"
                      if mapped.any() else "no cells mapped")
            note = spec["note"](label, spec["screen"]) if spec.get("note") else None
            title = (f"{_name(label, nisar_only)} · {SCREEN_TITLES[spec['screen']]}\n{detail}"
                     + (f"\n{note}" if note else ""))
            mesh = _panel(axes[j, i], lon, lat, values, spec["cmap"], spec["norm"], title)
    bar = fig.colorbar(mesh, ax=axes, orientation="horizontal", shrink=0.6, aspect=45,
                       label=columns[0]["bar"])
    bar.outline.set_visible(False)
    fig.legend(handles=[Patch(facecolor=SURFACE, edgecolor=MUTED, hatch="////",
                              label=hatch_label or f"fewer than {MIN_PAIRS_PER_CELL} matched "
                                    "pairs (includes cells removed by the screen)")],
               loc="outside lower left", fontsize=7, frameon=False)
    fig.suptitle(f"{heading}\n9 km EASE-Grid 2.0 cells · {WINDOW}", color=TEXT, x=0.01,
                 ha="left", fontsize=10)
    fig.savefig(out / name, dpi=200)
    plt.close(fig)
    return name


def render_all(cells, summary, out):
    """Write bias and temporal-r maps per group, and the NISAR sigma map.

    summary maps (comparison, screen) to pooled 1-sigma coverage, reported in the
    NISAR-SMAP bias subtitles as the share of differences within NISAR's stated sigma.
    The sigma-map subtitles repeat the same pair-level coverage.
    """
    out = Path(out)
    names = []
    screens = [str(s) for s in cells.screen.values]
    groups = {"nisar_smap": "NISAR SME2 − SMAP SPL2SMP_E", "nisar_pairs": "NISAR algorithm pairs"}

    def coverage_note(label, screen):
        pooled = summary.get((label, screen))
        return (f"{100 * pooled:.0f}% of differences within NISAR's stated ±1σ"
                if pooled is not None else None)

    for group, title in groups.items():
        labels = [str(c) for c in cells.comparison.values[cells.group.values == group]]
        bias = cells.bias.sel(comparison=labels).values
        vmax = max(0.05, np.ceil(np.nanpercentile(np.abs(bias), 98) / 0.05) * 0.05)
        bias_spec = dict(field="bias", count="n_pairs", cmap=DIVERGING, norm=Normalize(-vmax, vmax),
                         stat=lambda v: f"mean {np.nanmean(v):+.3f} m³/m³", note=coverage_note,
                         bar="Mean difference, first − second (m³/m³); blue = first product wetter")
        columns = [{**bias_spec, "screen": screen} for screen in screens]
        heading = f"{title}: mean bias per cell"
        names.append(_grid(cells, labels, columns, heading, out, f"{group}_bias_map.png"))
        r_spec = dict(field="r_temporal", count="n_pairs", cmap=DIVERGING, norm=Normalize(-1, 1),
                      stat=lambda v: f"median r {np.nanmedian(v):.2f}",
                      bar="Pearson r over matched times (unitless); blue = products rise and fall together")
        names.append(_grid(cells, labels, [{**r_spec, "screen": s} for s in screens],
                           f"{title}: temporal correlation per cell", out,
                           f"{group}_temporal_r_map.png"))
    labels = ["PMI - SMAP", "TSR - SMAP", "DSG - SMAP", "MEAN - SMAP"]
    sigma_spec = dict(field="sigma_mean", count="n_sigma_pairs", cmap=SEQUENTIAL,
                      norm=Normalize(0, 0.06),
                      stat=lambda v: f"median {np.nanmedian(v):.3f} m³/m³",
                      note=coverage_note,
                      bar="σ (m³/m³): linear mean of 200 m σ, i.e. the 9 km uncertainty if pixel "
                          "errors are fully correlated (upper bound); SMAP reports no σ")
    names.append(_grid(cells, labels, [{**sigma_spec, "screen": s} for s in screens],
                       "NISAR stated retrieval σ over each cell's pairs matched with SMAP", out,
                       "nisar_sigma_map.png", nisar_only=True, hatch_label=SIGMA_HATCH))
    return names
