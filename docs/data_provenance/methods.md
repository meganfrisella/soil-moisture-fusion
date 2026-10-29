# Data provenance methods

The graph traces documented product lineage for the Eastern Washington box and
study window defined in [data.md](data.md). Product lineage does not establish
statistical independence or use of a particular input in an individual acquisition.

## Methods

Reviewed product-version nodes connect upstream to downstream, distinguishing
input, calibration, validation and shared ancestry. Dashed edges or question marks
identify uncertain dependencies; unresolved versions remain explicit in metadata.
The overview groups nodes while preserving named-product paths, shared ancestors
and evidence strength. Validation relationships and temporal self-dependencies remain in the evidence
but are omitted from the drawing.

## Outputs and reproduction

[Interactive graph](https://meganfrisella.github.io/soil-moisture-fusion/data_provenance/)
· [PNG](../../site/data_provenance/dependencies.png)
· [Reviewed evidence](../../metadata/data_provenance/edge_evidence.json) · [Source log](../../metadata/data_provenance/sources.json)

Run offline from the repository root after `pixi install`, using a new output directory:

```sh
pixi run python scripts/build_data_provenance.py --output-dir outputs/data_provenance_replay
pixi run python -m unittest discover -s tests -p 'test_data_provenance.py'
```

Publish `index.html` and `dependencies.png` under `site/data_provenance/`. `dependencies.graphml` remains a reproducible local intermediate. Source products are not downloaded by this workflow.
