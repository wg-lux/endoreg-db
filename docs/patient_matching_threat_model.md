# Patient matching: manuscript threat model

Scope: site-local inputs, the experimental A/B comparison, hub transfer and
release disclosure. This companion records the evidence behind Supplement S2 of
`patient_matching_lean/infrastructure_lx_annotate_linkage_research_revised.docx`.
Feature readiness remains in `feature-tracking/HubTransfer.yml`; implementation
work in progress is not treated as a validated control.

## Assets and trust boundaries

Original demographic tuples, site HMAC keys and retained generations, UUID
relationships, approved media, and computation integrity are protected assets.
Import/API inputs, worker requests, output parsers, exports, logs and recovery
access are relevant entry points. Build/test tooling is a separate trust domain.

```mermaid
flowchart LR
    A["Centre A records and key"] --> WA["A worker"]
    B["Centre B original tuples"] --> WB["B worker"]
    WA -->|Private computation| WB
    WA -->|Approved transfer| H["Research hub"]
    WB -->|Authorized relationship| H
    H -->|Curated release| R["Recipient and auxiliary data"]
    O["Privileged administration"] --> A
    O --> B
```

The diagram depicts the intended role boundaries. The measured prototype used
two processes on one host, shared administration, plaintext localhost peers and
incremental outputs to both parties. It does not demonstrate independent
administration or the proposed minimum-output policy.

## Evidence and conditional ranking

| Abuse path | Conditional likelihood; impact; priority | Evidence and control |
|---|---|---|
| Obtain retained key and target tokens, then test demographic guesses | Frequency unmeasured; high; high where exposed | `endoreg_db/utils/hashs.py:get_identity_salt_fingerprint`; LuxNix `scripts/provision-identity-salts.py` gives application/root access. Inventory actual recovery and backup privileges. |
| Substitute UUID or fabricate a formally valid candidate | Medium where assertions are accepted; high; high | `patient_matching_lean/PatientMatching/Core.lean:candidate_uuid_substitution`; `Workflow.lean:Checks`. Local snapshot binding and authenticated worker completion are implementation obligations. |
| Adapt fresh or overlapping membership queries | Medium with repeat access; high; high | `PatientMatching/Privacy.lean:exact_membership_distinguishes`. Existing transfer replay controls do not imply cumulative matching budgets. |
| Attribute released records through known content or metadata | Medium with relevant auxiliary information; high; high | `docs/patient_matching_2pc_assessment.md`, artifact-identity and export analysis. Field allowlists and release UUIDs address particular channels, not all content recognition. |
| Exhaust worker resources or exploit partial output before abort | Medium with worker access; medium; medium | Assessment measurements: 100 candidates, 171.815 s, 32,482.8 MB. Bounded execution limits work; aggregate output and cancellation behaviour require independent verification. |

These ordinal rankings are scenario judgments, not incident probabilities or a
numeric risk score. Effective privilege assignments, collusion frequency and
recipient knowledge were not measured. The user previously requested inspection
of LuxNix instead of assuming separate administration; its source configuration
does not settle those runtime questions.

## Quantification and interpretation

Supplement S3 supplies reproducible conditional calculations: the ideal
256-bit accidental-collision union bound, a uniform-key guessing bound,
aggregate-output cardinality, illustrative membership-query coverage and an
exact zero-success binomial upper limit for a future attack study.

None is labelled an empirical patient-identification probability. Duplicate
demographic inputs are not digest collisions. A disclosed authorized relationship
can identify a source record to a party that already knows its local patient.
The benchmark's repeated synthetic tuples cannot estimate clinical linkage
precision, sensitivity or attack prevalence.

## Mitigation and evaluation priorities

Keep keys and original tuples within their respective controlled workers;
inventory application, root, database and recovery access before claiming
independent administration. Bind computations to authorized immutable records
and enrolled generations. Release aggregate results to the designated recipient,
persist signed session acceptance atomically, and enforce query budgets across
fresh sessions. Test malformed inputs, changes during execution, revocation and
interruption separately from ordinary correctness.

Assess public content release independently through known-content and
auxiliary-information attacks. Clinical adjudication, representative sampling,
false-attribution counts and confidence intervals would improve risk
calibration. No clinical safety or production approval is inferred from the paper.
