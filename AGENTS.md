# Repository Guidelines

## Scope & Design Decisions

- For questions about GAIA implementation, use the local checkout at `../gwl-space-time-smooth` as the primary source.
- Consult `docs/references/related_work.md` when evaluating or proposing scientific methods, computational approaches, and technical design choices.
- Produce only deliverables specifically requested by the user. Do not add unrequested previews, exports, or auxiliary products.
- Before implementation, ask the user to resolve any major design decisions left unspecified in the request. Wait for their answer before implementing work that depends on those decisions.
- Ask the user for approval before adding content to the top-level `README.md`.

## Build, Test, and Development Commands

Run commands from the repository root:

- `pixi install`: install the project environment.
- `pixi shell`: enter the environment.
- `pixi run python scripts/<script_name>.py`: run a workflow after adding its script.
- Add packages only through pixi.toml.

## Coding Style

- For new Python code, use four-space indentation, `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. 
- Keep code in src/ only when it has current reuse and downstream ingestion value; put workflow-specific and presentation code in scripts/.
- Organize modules by purpose in subdirectories, with thin command-line entry points.
- Reuse existing implementations and constants; avoid speculative abstractions, unnecessary wrappers and compatibility aliases without active callers.
- Every command-line script must support --output-dir.
- Download workflows must accept a cache directory, verify existing files and download only missing products.
- Remove unused code and obsolete files when refactoring, after checking callers and validating behavior.
- Document units, coordinate systems, temporal resolution, and uncertainty assumptions at data boundaries.

## Scripting

- Document every script with a module-level docstring at the top of the file describing its purpose and how to run it. Include commands runnable from the repository root and any required environment variables or prerequisites.
- Document every script with a 1-2 sentence description in scripts/README.md.

## Testing Guidelines

- Name tests `tests/test_<module>.py`
- Prefer small synthetic inputs covering missing observations, spatial and temporal alignment. 
- Keep tests independent of large downloads and private datasets.
- Document every test with a concise docstring describing the behavior or edge case it verifies.

## Data and reporting

- Keep downloaded data under ignored `data/`. Retained reference metadata belongs in tracked `metadata/`. JSON used directly by published pages belongs in `site/`.
- Common scientific data formats include CSV, Parquet, netCDF, HDF5, Zarr
- When reporting results, state the reference frame, units, and time window of every number. 


## Commits

Commits should disclose AI usage. User will make all commits but may request a commit message for the staged files. Format commit messages like "AI-assisted: <tools>, <what it drafted>".
