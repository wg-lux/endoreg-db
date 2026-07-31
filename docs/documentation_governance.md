# Cross-Repository Documentation Governance

The production-readiness scope and completion state live only in
[`feature-tracking/Documentation.yml`](../feature-tracking/Documentation.yml).
This page defines the working contract for maintainers; it is not a parallel
roadmap or status tracker.

## Repositories

The central policy in [`quality/documentation_governance.yml`](../quality/documentation_governance.yml)
covers `endoreg-db`, `lx-annotate`, `lx-data-models`, and `lx-anonymizer`. Repository
paths in that file are checkout hints, not public links. Override any hint when
the repositories use a different local layout:

```bash
.devenv/state/venv/bin/python scripts/check_documentation_governance.py \
  --repo-root lx-annotate=/work/lx-annotate \
  --repo-root lx-data-models=/work/lx-data-models
```

The command discovers every file below each `docs/` tree plus the repository
`README.md` and `AGENTS.md`. It emits a machine-readable inventory with a stable
artifact class, owner, lifecycle, visibility, canonical source, review fields,
and optional Wiki slug:

```bash
.devenv/state/venv/bin/python scripts/check_documentation_governance.py \
  --format json --output documentation-inventory.json
```

The generated inventory is a review artifact and should not be committed. The
policy and source documents remain authoritative.

Topics that legitimately span repositories are registered under
`canonical_topics`. Each topic names one canonical repository and source path.
Related component documents must declare a narrower role; they do not become a
second source of truth. Missing canonical or related paths fail validation.

## Artifact classes and lifecycle

- `source_document`: maintained Markdown or reStructuredText.
- `entrypoint`: repository `README.md` or `AGENTS.md`.
- `generated`: Sphinx build output; it must not be versioned.
- `diagram_source` and `rendered_diagram`: editable diagrams and their renderings.
- `test_fixture`: documentation-owned examples used by tests.
- `publication_artifact`: papers and publication drafts, not Wiki input.
- `sensitive_capture`: HAR or comparable diagnostic captures; always restricted.
- `binary_asset` and `documentation_config`: supporting assets and build configuration.

New source pages start as `review_due` until an owner confirms their accuracy.
Generated files are `generated`; sensitive captures and publication artifacts
are `restricted`. A temporary exception requires an owner, review deadline, and
objective exit criteria. Expired exceptions fail validation.

## Entry-point roles

`README.md` is a concise project and bootstrap page. `AGENTS.md` contains scoped,
binding contribution rules, required reading, safety invariants, and verification
commands. Detailed explanations and runbooks belong under `docs/`. Each repository
must provide all three roles or explicitly configure an intentional alternative.

## Publication boundary

Nothing is published merely because it is inside `docs/`. A future Wiki build
must select only reviewed `source_document` entries with `public_candidate`
visibility. Generated output, publications, captures, fixtures, and restricted
content are excluded by construction. Repository Markdown remains the source of
truth; direct Wiki edits are drift, not authoritative changes.

## Pruning workflow

For each `review_due` document, choose one outcome: keep, improve, merge, replace,
archive, restrict, or delete. Plans, TODOs, migration notes, current-state reports,
and readiness verdicts must move actionable state into feature-tracking YAML.
Retain only durable architecture, operational guidance, or audit evidence. When a
page is replaced, update incoming links and record its canonical successor before
removal.
