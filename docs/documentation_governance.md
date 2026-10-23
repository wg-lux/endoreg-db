# Cross-Repository Documentation Governance

This guide explains how to maintain, review, and publish documentation.
[Documentation.yml](../feature-tracking/Documentation.yml) tracks the work and
its verification evidence.

## Repositories

The [central policy](../quality/documentation_governance.yml) covers `endoreg-db`,
`lx-annotate`, `lx-data-models`, and `lx-anonymizer`. Repository paths are local
checkout hints. Override them for a different layout:

```bash
.devenv/state/venv/bin/python scripts/check_documentation_governance.py \
  --repo-root lx-annotate=/work/lx-annotate \
  --repo-root lx-data-models=/work/lx-data-models
```

The checker inventories each `docs/` tree, `README.md`, and `AGENTS.md`.
`additional_documentation_paths` registers other maintained sources. EndoReg-DB
uses it for setup guides, changelogs, design logs, and documentation beside code.

Each inventory entry records its class, owner, lifecycle, visibility, canonical
source, review fields, and optional Wiki slug. Export a review copy with:

```bash
.devenv/state/venv/bin/python scripts/check_documentation_governance.py \
  --format json --output documentation-inventory.json
```

Keep generated inventories out of version control. Maintain their source policy
and documents instead.

For topics spanning repositories, `canonical_topics` names the main repository
and document. Related pages describe their component's role and link to that
source. Missing source or related paths fail validation.

## Language and production-state claims

Write maintained EndoReg-DB documentation in English. The policy declares
`canonical_language: en`; the checker rejects predominantly German prose.
Preserve required spellings in code, identifiers, commands, clinical terms,
and quoted values.

Use these terms consistently:

- **Implemented:** behavior is present in the repository.
- **Verified:** the feature tracker records reproducible evidence and an assessor.
- **Deployed:** an operator confirms the behavior in a named environment.

Tests establish behavior in the tested environment. Deployment claims need
runtime evidence. Link to the feature YAML for readiness assessments and use
runbooks to explain operation.

## Artifact classes and lifecycle

| Class | Contents |
| --- | --- |
| `source_document` | Maintained Markdown or reStructuredText |
| `entrypoint` | Repository `README.md` or `AGENTS.md` |
| `generated` | Sphinx build output, kept out of version control |
| `diagram_source`, `rendered_diagram` | Editable diagrams and renderings |
| `test_fixture` | Documentation examples used by tests |
| `publication_artifact` | Papers and publication drafts |
| `sensitive_capture` | HTTP Archive (HAR) files and similar diagnostic captures |
| `binary_asset`, `documentation_config` | Supporting assets and build configuration |

New pages start as `review_due`. An owner then reviews their accuracy. Generated
files use `generated`; captures and publication artifacts use `restricted`.
Temporary exceptions need an owner, review deadline, and measurable exit criteria.
Expired exceptions fail validation.

## Entry-point roles

`README.md` introduces the project and setup. `AGENTS.md` defines contribution
rules, safety requirements, required reading, and checks. `docs/` contains guides
and runbooks. Each repository provides these roles or configures an alternative.

## Publication boundary

A Wiki build selects reviewed `source_document` entries with `public_candidate`
visibility. It excludes generated output, publications, captures, fixtures, and
restricted content. Edit the repository Markdown, then regenerate published
pages. Direct Wiki edits create drift.

## Pruning workflow

Review each `review_due` page and choose: keep, improve, merge, replace, archive,
restrict, or delete. Move actionable plans and progress records into feature
YAML. Keep useful architecture, operational guidance, and audit evidence in the
documentation. Before removing a replaced page, update incoming links and record
its successor.
