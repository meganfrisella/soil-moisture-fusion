# Regridding agent instructions

Historical execution instructions for the broader experiment. The current retained methods and operative Axx/Rxx decisions are recorded in [the concise regridding document](../docs/eastern_washington_regridding.md); regression and coarse experiments now reside in `exploration/` or local archives.

## Scope and sources

- Follow the repository [AGENTS.md](../AGENTS.md) and the [Eastern Washington experiment specification](../docs/eastern_washington_regridding.md). This file governs execution and decision handling; scientific requirements, assumptions, and unresolved R-items remain in the experiment specification.
- Consult [mlgeo-book](../../mlgeo-book/), especially [the resampling notebook](../../mlgeo-book/book/Chapter2-DataManipulation/2.6_resampling.ipynb); [gwl-space-time-smooth](../../gwl-space-time-smooth/), especially [GAIA's implementation](../../gwl-space-time-smooth/src/models/downscale.py); [related work](../docs/related_work.md); and relevant internet searches when resolving choices.

## Software design

- Reuse existing repository code, helpers, and constants wherever possible.
- Favor established scientific/statistical packages and tools over reimplementation when they satisfy the experiment specification.
- Follow the existing practices in the top-level [AGENTS.md](../AGENTS.md), including environment management, code organization, testing, and documentation.
- Keep workflow code in `scripts/`; use `src/` for code with current reuse and downstream ingestion value.

## Specification and decision handling

- Treat **Assumption (Axx)** items as modeling approximations, not evidence that they hold locally. Report active assumptions with results.
- At every unresolved **Requires further specification (Rxx)** item, **pause work that depends on that decision and request the user's input**. Wait for an explicit answer before implementing the choice or advancing dependent steps. Unrelated tasks may continue under the parallel-work rules below.
- Reuse decisions already supplied by the user; do not request them again. Retain A/R IDs when updating the specification; record the user's decision  in the specification.
- **Continue through resolved work:** After implementing a user decision, run the relevant checks, update the specification/reproducibility log, and proceed directly to the next authorized task in the sequential workflow.

## Parallel work

- Unrelated, independently authorized tasks may proceed simultaneously, including while long-running jobs execute or another task awaits a decision. Preserve dependencies and required ordering within each task.
- Continue prompting for decisions in **each parallel task as they arise**.
- Pause only the work affected by an unanswered decision. Continue other authorized work whose requirements are resolved.

## Reproducibility

- Everything done by the agent must be reproducible through repository code.
- Record seeds and numerical settings where applicable. Preserve frozen inputs and distinguish rerunning an existing dataset from refreshing it; never log credentials.

## Requesting user input

- Research the sources above before asking; use primary internet sources where available.
- Cite the specific notebook sections, code/functions, papers, and web pages supporting the options. Explain relevant differences in spatial support, inputs, uncertainty assumptions, or validation setting. Identify sources that offer no applicable guidance rather than implying support.
- Present concise feasible options and their tradeoffs. Recommend the **simplest scientifically defensible design, setting, parameter, or requirement** that satisfies the experiment's goal.
- State why the recommendation is sufficient, what it assumes, and what it cannot establish; distinguish sourced values from proposed defaults.
- Each question should contain the R-ID, affected step, options, recommended option with evidence, and the exact choice requested.

## Outputs and handoff

- Resolve the experiment specification's output/run requirements (R32/R33) and visual export requirements (R35); generate only agreed artifacts.
- Every report must state input/target support and CRS, units, UTC window, included/excluded counts, active assumptions, validation limitations, and uncertainty components actually quantified.
- Report skipped/failed runs and remaining decisions explicitly.
