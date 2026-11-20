# Controlled `(k, l, t)` Release View

This feature creates a separate, controlled release view from an already
de-identified CSV table. It does not modify the source clinical table. It
writes an output CSV only when the fully configured release predicate is
satisfied.

## Overview

```text
de-identified source table
+-- fixed quasi-identifier (QI) definition
+-- fixed sensitive attributes
+-- fixed utility metrics and thresholds
    |
    +-- frequency check (k)
    +-- optional l-diversity
    +-- optional total-variation-based t-closeness
    +-- Jensen-Shannon-divergence/Wasserstein-based utility check
        |
        +-- satisfied: release CSV + protected audit manifest
        +-- not satisfied: no release CSV, audit manifest with rejection reason
```

The result is a frequency property of the released table, not proof of
classical person-level anonymity. Synthetic rows may count toward `k` only
when recipients can neither see nor reliably infer their provenance. The
configuration must explicitly confirm this governance assumption.

## Configuration

```yaml
schema_version: "1.0"

release_columns:
  - center
  - age_band
  - sex
  - examination_month
  - diagnosis_group
  - procedure_duration_minutes

quasi_identifiers:
  - center
  - age_band
  - sex
  - examination_month

sensitive_attributes:
  - name: diagnosis_group
    allowed_values:
      - benign
      - premalignant
      - malignant
    l_diversity: 2
    t_closeness: 0.20

utility_features:
  - name: diagnosis_group
    kind: categorical
    weight: 0.6
  - name: procedure_duration_minutes
    kind: continuous
    weight: 0.4
    normalization_scale: 60.0

k: 5
tau_max: 0.08
max_synthetic_rows: 500
max_state_evaluations: 10000
max_candidate_combinations: 10000
max_input_rows: 100000

synthetic_rows_count_toward_k: true
recipient_can_observe_synthetic_provenance: false
include_projection_diagnostics: true

repair_cost_weights:
  size: 1.0
  sensitive_changes: 1.0
  distribution: 1.0
```

Direct identifiers such as names, dates of birth, case numbers, or external
patient identifiers are prohibited in `release_columns`. Undeclared input
columns are not copied into the release view.

The `utility_features` weights must sum to `1.0` within the implementation
tolerance of `1e-9`. `allowed_values` defines the predetermined finite domain
of a sensitive attribute. A value outside that domain aborts the run;
continuous sensitive values must be binned according to domain requirements
before the run. Categorical features use Jensen-Shannon divergence with a
base-2 logarithm. Continuous features use the 1-Wasserstein distance divided by
the predetermined domain-specific `normalization_scale`.

## Execution

```bash
devenv shell -- python manage.py build_k_pseudonymous_release \
  release_policy.yaml \
  deidentified_study_table.csv \
  --release-output /protected/path/release.csv \
  --audit-output /custodian-only/audit.json
```

Both files are written atomically with mode `0600`. When the release check is
not satisfied, the command removes any existing stale release CSV. The
protected audit manifest remains and records:

- configuration and thresholds;
- initial and final release-predicate state;
- complete QI classes and optional projection diagnostics;
- `k` deficits, l-diversity, and total-variation-based t-closeness;
- weighted Jensen-Shannon-divergence/Wasserstein utility deviations;
- the count, proportion, and internal row positions of synthetic records;
- canonical SHA-256 bindings for the source and release tables;
- the termination reason and number of evaluated states.

Synthetic row positions appear only in the protected manifest. They are not
written to the release CSV as a recipient-visible field.

## Security and Interpretation Boundaries

- The search is bounded and heuristic. `no_release` does not prove that no
  mathematically valid table exists; it means only that the permitted search
  path found none.
- Real rows are immutable. Repairs add only explicitly synthetic rows from a
  finite value domain determined by the source data.
- The reference distribution for t-closeness and utility remains the initial
  real, de-identified table; synthetic rows do not move the baseline.
- Synthetic observations must not be interpreted as real patient counts or
  clinical audit events.
- This process does not replace access control, encryption, a recipient model,
  a data protection impact assessment, or domain-expert approval.
