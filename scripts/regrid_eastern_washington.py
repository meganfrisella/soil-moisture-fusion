#!/usr/bin/env python3
"""Run native/nearest/bilinear stages or inspect the Eastern Washington run plan.

Run from the repository root after ``pixi install --locked``:
    pixi run python scripts/regrid_eastern_washington.py --stage native --plan-only --selection outputs/eastern_washington/sme2_statistics.json --cache-dir data/nisar --proj-cache-dir data/proj --output-dir outputs/eastern_washington/regridding_selected

--plan-only prints configuration and code/environment provenance as JSON. It
does not audit inputs, estimate resource needs, create the output directory or
run fields/figures. No credentials or network are needed. --resume records the
requested restart intent; it never bypasses scientific/integrity gates. Native
support is 200 m EPSG:6933 and target 90 m EPSG:5070; m³/m³ fields, UTC times.
Omit --plan-only to audit/write native.zarr and figures/odessa_native.png. All
originals and both approved PROJ grids must exist. --resume validates completed
data before continuing. After visually reviewing the PNG, record its exact
manifest checksum with --inspect-native-figure SHA256. Later method stages
remain gated on sequential completion and their unresolved scientific choices.
Use --stage native --original-grid-figure --resume for the additional EPSG:6933
PNG; record review with --inspect-original-grid-figure SHA256. Run the approved
nearest implementation with --stage nearest --resume after native inspection;
strict transform failures stop execution. --inspect-nearest-figure SHA256 records
its review only after all nearest tasks complete. Run bilinear with --stage bilinear --resume;
record its exact PNG review with --inspect-bilinear-figure SHA256. All stages
support --summary for verified reporting.
New production requires matching completed sme2_field_audit.json and
sme2_diagnostics.json in --output-dir, created by prepare_nisar_sme2.py. The gate
rechecks original bytes, report digests and QA identities; permitted warnings
remain non-filtering. Historical summaries/figure inspection do not retroactively
certify old runs. Existing frozen method manifests are not rewritten by QA.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from regridding.run_plan import configure_threads, describe_plan, serial_io


def main(argv=None):
    """Print an inspectable plan or reject a stage whose prerequisites remain open."""
    configure_threads()
    from regridding.schema import DEFAULT_OUTPUT_DIR

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True,
                        choices=("native", "nearest", "bilinear"))
    parser.add_argument("--selection", default="outputs/eastern_washington/sme2_statistics.json")
    parser.add_argument("--cache-dir", default="data/nisar")
    parser.add_argument("--proj-cache-dir", default="data/proj")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--summary", action="store_true", help="Print verified stored stage counts without executing fields")
    parser.add_argument("--inspect-native-figure", metavar="SHA256")
    parser.add_argument("--original-grid-figure", action="store_true",
                        help="Export the additional original EPSG:6933 six-panel native reference")
    parser.add_argument("--inspect-original-grid-figure", metavar="SHA256")
    parser.add_argument("--inspect-nearest-figure", metavar="SHA256")
    parser.add_argument("--inspect-bilinear-figure", metavar="SHA256")
    args = parser.parse_args(argv)
    actions = (args.inspect_native_figure, args.original_grid_figure,
               args.inspect_original_grid_figure, args.inspect_nearest_figure, args.inspect_bilinear_figure, args.summary)
    if sum(bool(action) for action in actions) > 1:
        parser.error('Choose only one figure action')
    if args.summary and args.plan_only:
        parser.error('--summary cannot be combined with --plan-only')
    if (args.original_grid_figure or args.inspect_original_grid_figure) and (args.stage != 'native' or args.plan_only):
        parser.error('Original-grid actions require --stage native without --plan-only')
    if args.inspect_nearest_figure and (args.stage != 'nearest' or args.plan_only):
        parser.error('--inspect-nearest-figure requires --stage nearest without --plan-only')
    if args.inspect_bilinear_figure and (args.stage != 'bilinear' or args.plan_only):
        parser.error('--inspect-bilinear-figure requires --stage bilinear without --plan-only')
    if args.inspect_native_figure and (args.stage != 'native' or args.plan_only):
        parser.error('--inspect-native-figure requires --stage native and cannot use --plan-only')
    if not args.plan_only and args.stage == 'nearest' and not (Path(args.output_dir)/'run_manifest.json').is_file():
        parser.error('Nearest requires an existing completed native stage and inspected figure')
    if not args.plan_only and args.stage == 'bilinear' and not (Path(args.output_dir)/'run_manifest.json').is_file():
        parser.error('Bilinear requires existing completed native/nearest stages and inspected figures')
    with serial_io():
        if args.inspect_bilinear_figure:
            from regridding.comparison_figures import inspect_comparison
            inspect_comparison(args.output_dir, 'bilinear', args.inspect_bilinear_figure)
            return
        if args.summary:
            from regridding.reporting import stage_summary
            print(json.dumps(stage_summary(args.output_dir,args.stage),indent=2,allow_nan=False))
            return
        if args.inspect_original_grid_figure or args.inspect_nearest_figure:
            from regridding.comparison_figures import inspect_comparison
            mode = 'native_epsg6933' if args.inspect_original_grid_figure else 'nearest'
            inspect_comparison(args.output_dir, mode, args.inspect_original_grid_figure or args.inspect_nearest_figure)
            return
        if args.original_grid_figure:
            from regridding.comparison_figures import export_comparison
            from regridding.storage import RunStore
            identity = json.loads((Path(args.output_dir)/'run_manifest.json').read_text())['identity']
            with RunStore(args.output_dir, identity, resume=args.resume) as run:
                export_comparison(run, 'native_epsg6933', args.proj_cache_dir)
            return
        if args.inspect_native_figure:
            from regridding.figures import record_inspection
            record_inspection(args.output_dir, args.inspect_native_figure)
            return
        plan = describe_plan(args.stage, args.selection, args.cache_dir, args.proj_cache_dir,
                             args.output_dir, args.resume)
        plan['command'] = ['pixi', 'run', 'python', 'scripts/regrid_eastern_washington.py',
                           *(sys.argv[1:] if argv is None else argv)]
        if args.plan_only:
            print(json.dumps(plan, indent=2, allow_nan=False))
        else:
            from nisar.qa.reports import require_qa
            from regridding.run_plan import SELECTION_SHA256
            plan['qa'] = require_qa(args.output_dir, args.cache_dir, selection_sha256=SELECTION_SHA256)
            if args.stage == 'native':
                from regridding.native_stage import run_native
                run_native(args, plan)
            elif args.stage == 'nearest':
                from regridding.nearest_stage import run_nearest
                run_nearest(args, plan)
            else:
                from regridding.bilinear_stage import run_bilinear
                run_bilinear(args, plan)


if __name__ == "__main__":
    main()
