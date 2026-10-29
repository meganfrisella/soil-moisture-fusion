# Data provenance agent instructions

## Scope and sources

- Follow the repository [AGENTS.md](../AGENTS.md) and the [provenance methods](../docs/data_provenance/methods.md) and [data scope](../docs/data_provenance/data.md); this file governs execution and decision handling.
- Use documentation and metadata for this audit. Do not download actual data products or measurement arrays.
- Prefer official provenance records, processing and algorithm documentation, version histories, catalog metadata, and acquisition or processing records. Use secondary sources to locate primary evidence, not as substitutes for evidence of direct use. Consult [related work](../docs/references/related_work.md) when evaluating scientific methods or technical design choices.
- Verify that each source applies to the relevant product version, region, and time window. A distribution or marketplace listing alone does not establish processing lineage or the availability of a different product.

## Software design

- Reuse repository code and constants, and prefer established tools where they meet the specification. Keep workflow code in `scripts/`; use `src/` only for code with current reuse and downstream ingestion value.
- Follow repository conventions for the pixi environment, dependencies, script documentation, and tests.
- Keep evidence collection and review separate from graph generation. Generate GraphML from explicit evidence records with stable product-version, edge, and source identifiers; do not embed undocumented scientific judgments in graph-building code.
- Preserve native product identities and metadata. Distinguish product releases, processing versions, science versions, and file-format versions rather than treating them as interchangeable. Do not harmonize fields, layers, units, or temporal support as part of provenance exploration.

## Specification and decision handling

- The specification does not label assumptions or unresolved decisions. Actively inspect it and the evidence for ambiguities; the absence of a decision marker is not permission to choose silently.
- **Raise every significant design or data decision with the user before implementing dependent work.** This includes changing selected products or sources, choosing among unresolved versions, setting lineage depth or stopping criteria, interpreting ambiguous spatial or temporal support, and choosing graph or evidence semantics that change the audit's conclusions.
- Research the issue first, then present concise feasible options, supporting sources, tradeoffs, and a recommendation. State the affected step and the exact decision needed.
- Never silently substitute a product, invent a missing version, or resolve conflicting evidence by assumption. Flag unknowns and unsupported relationships explicitly.

## Reproducibility

- Record provider, URL or persistent identifier, source title and version, access date, and the exact page, section, or metadata field supporting each claim. Retain existing official provenance records as references and connect them to the relevant evidence and graph edges.

## Outputs

- Produce only the deliverables specified under [Outputs and reproduction](../docs/data_provenance/methods.md#outputs-and-reproduction).
- Use product-version nodes and upstream-to-downstream edges.
- Log unsupported proposed relationships, conflicting records, unresolved versions, and limits on geographic or temporal applicability.
- Check that GraphML parses, identifiers are stable and unique, edge endpoints exist, and evidence/source references resolve.
