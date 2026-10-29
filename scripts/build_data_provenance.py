#!/usr/bin/env python3
"""Validate reviewed source evidence, build GraphML and render provenance PNG/HTML.

Run offline from the repository root after pixi install:
    pixi run python scripts/build_data_provenance.py --output-dir outputs/data_provenance

Reads curated evidence in metadata/data_provenance/ (override with --evidence-dir).
Writes dependencies.graphml, dependencies.png and index.html. Drawing coordinates are points, not geographic coordinates. This
workflow makes no scientific source selections and downloads no data products.
"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from data_provenance.graph import build_graph
from data_provenance.png import render_png
from data_provenance.html import render_html


def main(argv=None):
    """Generate the reviewed graph and both published views in a new directory."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--evidence-dir', type=Path, default=ROOT / 'metadata/data_provenance')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    evidence = json.loads((args.evidence_dir / 'edge_evidence.json').read_text())
    sources = json.loads((args.evidence_dir / 'sources.json').read_text())
    graph = args.output_dir / 'dependencies.graphml'
    build_graph(evidence, sources, graph)
    render_png(graph, args.output_dir / 'dependencies.png')
    render_html(graph, args.evidence_dir, args.output_dir / 'index.html')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
