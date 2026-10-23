"""Plot prepared shallow station series together, with flat periods and zeros missing.

Run through ``scripts/validate_eastern_washington.py`` after preparation.

The pipeline excludes Odessa (ODSW) and Touchet (824:WA:SNTL) from the
five-sensor validation figure and uses a new output directory.

One axis spans the full frozen Eastern Washington UTC study period. Only stations
with returned shallow observations are included; uses prepared m3/m3 values exactly,
without bridging NaNs, smoothing or rejecting sharp jumps. Odessa raw percentages
are divided by 100 for m3/m3, as selected by the user; calibration is unverified. Preserves
prior outputs and refuses overwrites. Retains PNG and its provenance/counts JSON.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from insitu.coverage import digest
from nisar_visualization.style import MISSING_COLOR
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def run(preparation_dir, output_dir, exclude_stations=()):
    """Verify identities/policies, plot finite segments and document included stations."""
    source, output = Path(preparation_dir), Path(output_dir)
    manifest_hash = digest(source/'preparation.json')
    record = json.loads((source/'preparation.json').read_text())
    excluded = set(exclude_stations)
    if excluded - {station['station_id'] for station in record['stations']}:
        raise ValueError('Unknown excluded station ID')
    if digest(source/'prepared.csv') != record['outputs']['prepared.csv']:
        raise ValueError('CSV differs from preparation manifest')
    if record.get('zero_policy') != 'exact zero becomes NaN' or record.get('sharp_jump_policy') != 'retained':
        raise ValueError('Require the approved zero/jump policies')
    table = pd.read_csv(source/'prepared.csv')
    table['time_utc'] = pd.to_datetime(table.time_utc, utc=True)
    paths = [output/'shallow_sensors_combined.png', output/'shallow_sensors_combined.json']
    if any(p.exists() for p in paths):
        raise FileExistsError('Use new output paths')
    fig, ax = plt.subplots(figsize=(15, 6.8))
    colors = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#333333', '#56B4E9']
    included = []
    for station in record['stations']:
        if station['station_id'] in excluded:
            continue
        rows = table[table.station_id == station['station_id']].sort_values('time_utc')
        if not rows.raw_value.notna().any():
            continue
        values = rows.soil_moisture_m3_m3.to_numpy(dtype=float)
        if np.any(values == 0) or np.isfinite(values[rows.constant_period_index.notna().to_numpy()]).any():
            raise ValueError('Zero/constant values unexpectedly present')
        if station['station_id'] == 'ODSW' and station['conversion_factor'] != .01:
            raise ValueError('Odessa must use the user-selected percent conversion')
        label = 'Odessa' if station['station_id']=='ODSW' else station['name']
        ax.plot(rows.time_utc, values, label=label, color=colors[len(included)],
                linewidth=1.1, marker='.', markersize=1.5)
        included.append(dict(station_id=station['station_id'], name=station['name'],
                             finite_plotted=int(np.isfinite(values).sum()), missing=int(np.isnan(values).sum()),
                             first_finite_utc=rows.loc[np.isfinite(values),'time_utc'].min().isoformat() if np.isfinite(values).any() else None,
                             last_finite_utc=rows.loc[np.isfinite(values),'time_utc'].max().isoformat() if np.isfinite(values).any() else None,
                             minimum_m3_m3=float(np.nanmin(values)) if np.isfinite(values).any() else None,
                             maximum_m3_m3=float(np.nanmax(values)) if np.isfinite(values).any() else None))
    start, end = [datetime.fromisoformat(record['study_window_utc'][k]) for k in ('start', 'end')]
    ax.set_xlim(start, end)
    ax.set_ylim(bottom=0)
    ax.set_ylabel('Soil moisture (m³/m³)', fontsize=12)
    ax.set_xlabel('2026 • UTC', fontsize=11)
    ax.set_xticks([start, *[datetime(2026,m,1,tzinfo=timezone.utc) for m in (7,8,9,10)], end])
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d', tz=timezone.utc))
    ax.grid(alpha=.2)
    ax.set_title('Eastern Washington shallow soil moisture', loc='left', fontsize=17, weight='bold', pad=16)
    ax.legend(loc='upper left', ncol=4, frameon=False, bbox_to_anchor=(0,1.0), fontsize=10)
    odessa_note = (' Odessa percentages divided by 100.'
                   if any(s['station_id'] == 'ODSW' for s in included) else '')
    fig.text(.5,.025,'June 17 02:53:18 – October 10 12:39:37 UTC, 2026 • depths 5–5.08 cm • station coordinates EPSG:4326\n'
             'Gaps = missing observations, exact zeros or ≥12-hour constant periods; sharp jumps retained.' + odessa_note,
             ha='center', fontsize=9)
    fig.subplots_adjust(left=.07,right=.98,top=.88,bottom=.18)
    output.mkdir(parents=True,exist_ok=True)
    fig.savefig(paths[0],dpi=200,facecolor='white')
    plt.close(fig)
    if digest(source/'preparation.json') != manifest_hash or digest(source/'prepared.csv') != record['outputs']['prepared.csv']:
        raise ValueError('Inputs changed while plotting')
    result = dict(study_window_utc=record['study_window_utc'], moisture_units='m3/m3', coordinate_crs='EPSG:4326',
                  stations=included, excluded_station_ids=sorted(excluded), preparation_sha256=manifest_hash, prepared_csv_sha256=record['outputs']['prepared.csv'],
                  code_sha256=digest(__file__), pixi_lock_sha256=digest(ROOT/'pixi.lock'), figure_sha256=digest(paths[0]))
    paths[1].write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
