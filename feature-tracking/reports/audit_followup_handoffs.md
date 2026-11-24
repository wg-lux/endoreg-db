# Audit follow-up handoffs

These are bounded work instructions, not a separate readiness or completion
tracker. Scope, approvals, assessments and new evidence belong exclusively in the
linked feature YAML. Audit line numbers, counts and complexity scores are historical
snapshots and must be rechecked before implementation.

## Protocol for every handoff

Read `AGENTS.md`, root `structure.yml`, the nearest manifests and the relevant
feature criteria. Choose a stable agent ID, read the inbox, declare exact editable
paths and exclusions, and acquire file/criterion locks before editing. Coordinate
conflicts with the recorded owner. Sharpen missing measurable acceptance criteria
in the feature YAML before extending scope.

Return changed paths, the ownership decision, manifest changes, exact verification
commands and results, and blockers. Run Pyright before focused pytest for code
changes. Check `pgrep -af 'pytest|py.test'` before broader lanes; do not stack suites.
Run the structure validator, feature validation and `tracker.py show <feature_id>`.
Only assess readiness with stable evidence and a named assessor. Reply to acted-on
feedback and acknowledge it before releasing locks.

## 1. Model cycles and identity workflows

**Receiving owner:** Backend architecture, with security review for identity.
**Authority:** [CodeQuality.yml](../CodeQuality.yml), criteria
`helper_class_boundaries`, `persistence_ownership` and
`sensitive_meta_verification_authority`;
[model-layer map](../../docs/model_layer_map_for_agents.md).
**Navigation:** `endoreg_db/models/structure.yml`,
`endoreg_db/models/metadata/structure.yml`, `endoreg_db/services/structure.yml`.

**First bounded assignment:** Inventory consumers and dependency direction around
`endoreg_db/models/metadata/sensitive_meta_logic.py`; select one connected workflow
for extraction. Then identify and lock the actual destination service and type
contracts. Models retain persistence, constraints and necessary thin compatibility
wrappers. Reuse existing identity, hashing and rotation services.

**Acceptance:** No new model-to-service dependency or barrel import; public callers
remain compatible. Verify identity stability, idempotency, rollback and concurrent
access. Recount the historical 41 barrel imports and nine service imports before
using them as evidence. Do not remove all `VideoFile` facades in this cohort.

**Verification:** Pyright, then `tests/app/test_clean_layer_cohorts.py` plus the
identity/import tests identified from the selected callers. Name those exact test
paths in the handoff return; architecture tests alone do not establish identity
correctness.

## 2. Large workflow modules

**Receiving owner:** One domain owner per hub, streaming or video cohort.
**Authority:** [CodeQuality.yml](../CodeQuality.yml),
`helper_class_boundaries`, `centralized_exception_contracts`, `incremental_rollout`;
[ImportPipelineRobustness.yml](../ImportPipelineRobustness.yml),
[VideoStorageNormalization.yml](../VideoStorageNormalization.yml).
**Navigation:** The nearest manifests under `services/hub`, `services/streaming`
and `services/video_files`.

**Separate assignments:** `hub/ingest.py`, `hub/transfers.py`,
`streaming/hls_media.py` and `video_files/_ai.py`. Select one decision block and
its actual callers per assignment. Separate pure transformations from side effects;
remeasure complexity before extraction. File length and Radon scores do not prove
dead code.

**Acceptance:** Preserve lease, transaction, fencing and publication boundaries.
Rust remains the sole owner of import states. Preserve presentation timestamps,
hashes, clinical frame identity and the binding of HTTP Live Streaming (HLS) to the
canonical master. Helpers must not import their orchestrator. Preserve documented
compatibility paths. Do not combine extraction with an algorithm change.

**Verification:** Pyright, then the relevant selection from
`tests/services/test_hub_ingest_unit.py`,
`tests/services/test_hub_ingest_dispatch.py`,
`tests/services/test_hub_transfer_unit_decisions.py`,
`tests/services/test_hls_media.py`, or the identified artificial-intelligence tests.
Include negative and concurrent regression coverage for the affected side-effect
boundary. Video work first reads the [storage runbook](../../docs/video_storage_normalization.md)
and [timestamp inventory](../../docs/video_pts_fps_callsite_inventory.md).

## 3. Remaining duplication cohorts

**Receiving owner:** The relevant API, serializer or annotation owner.
**Authority:** [CodeQuality.yml](../CodeQuality.yml),
`helper_class_boundaries` and `incremental_rollout`.

**First deliverable:** A reproducible inventory for annotator normalization,
filename suffixes, media pagination, query parameters and serializer choices.
The audit did not provide complete verified path groups. Navigate through the
owning manifests and record definitions, registrations, dynamic references and
external consumers before selecting editable files.

**Acceptance:** One selected cohort has one domain owner and exact editable paths.
Resolve differences in errors, ordering, input mutation and defaults before
consolidation. Test valid and invalid inputs and retained public imports. Do not
create a generic miscellaneous helper module. The already consolidated interval
and task-ID helpers are precedents, not new unfinished work.

**Verification:** Pyright, then the consumer tests named before implementation and
focused regressions for each documented semantic difference.

## 4. Public legacy contracts

**Receiving owner:** Maintainers with access to external consumer repositories.
**Authority:** [CodeQuality.yml](../CodeQuality.yml),
`dead_code_baseline`, `dead_code_reduction`, `incremental_rollout`;
[existing consumer inventory](import_dead_code_removal.yml).

**Independent candidates:** `models/medical/patient/medication_examples.py`,
`models/metadata/pdf_meta.py::PdfMeta.create_from_file` and the three
`DEFAULT_PATIENT_*` constants in `import_files/context/default_sensitive_meta.py`.
Paths are relative to `endoreg_db/`; read the nearest model/import manifests first.

**Acceptance:** Record reproducible per-symbol searches across internal callers,
available sibling repositories, installation scripts and documented imports.
Unavailable consumers remain an explicit evidence limit. Obtain an explicit
maintainer decision to retain, deprecate or remove each public contract; absence
of internal references is insufficient. Removal includes registrations, tests,
inventories and manifests. Do not remove service aliases or `VideoFile` facades
as a group. Do not evaluate the lazy example against clinical data and create
patients while checking consumers.

**Verification:** Pyright, relevant consumer/import regressions, then
`devenv tasks run quality:dead-code`.

## 5. Audit coverage beyond the current Python scan

**Receiving owner:** Quality tooling, with Rust, Nix and workflow reviewers.
**Authority:** [CodeQuality.yml](../CodeQuality.yml),
`dead_code_baseline` and `quality_regression_guards`.
**Navigation:** `scripts/structure.yml`, `quality/structure.yml`,
`rust/structure.yml`, `workflow/structure.yml` and the management manifest.

**First assignment:** Check scan exclusions against active commands, Django
registrations, settings and migrations. Review Rust, Snakemake and Nix separately
with suitable tools and their dynamic entrypoints. For a small complexity cohort,
locate and remeasure `migrate_legacy_hashes`, `compare_annotations` or
`check_lx_dtypes_contract_boundaries` before proposing exact editable paths.

**Acceptance:** Reproducible tool invocations, explicit coverage boundaries and
reviewed exceptions in existing quality inventories. Do not delete historical
migrations based on static references or infer approval for destructive media
migration from a clean dead-code guard. Review baseline changes semantically.
The repaired deletion-candidate expiry check does not need reimplementation.

**Verification:** Relevant tooling regressions, then
`devenv tasks run quality:dead-code` and `devenv tasks run quality:boundaries`.
Run Pyright before pytest for new Python tooling.

## 6. LuxNix environment parity

**Receiving owner:** LuxNix deployment and backend configuration owners.
**Authority:** In the sibling LuxNix checkout,
`feature-tracking/lx_annotate_environment_lifecycle_audit.yaml`, especially
`application_contract_complete`, `provisioning_contract_identical`,
`runtime_secret_verification_complete`, `lifecycle_tests_close_the_contract`.
Read that repository's instructions, manifests and locks before editing.

**Candidate scope after consumer review:**
`modules/nixos/services/lx-annotate-local/config.nix` and `scripts/env.nix`.
Backend configuration needs its own Endoreg lock. Reconcile web, workers and
maintenance wrappers: inspect the three service-only `ENDOREG` registry values
and inline database, Django and OpenID Connect (OIDC) secrets against the required
file-handle contract. Separate application configuration from process controls.
Never print secret values in comparisons or evidence.

**Acceptance:** A machine-checked matrix distinguishes shared application values,
file handles and legitimate process controls. Required missing values fail loudly;
inline master keys remain prohibited outside explicit tests. Preserve the already
added native `libmagic` runtime for web, workers and maintenance. Evaluate both
supported host profiles and verify wrapper/application parity.

**Verification:** Use the exact evaluations and tests recorded in the LuxNix feature,
including `tests/lx-annotate/test_lx_annotate_ocr_runtime.py`; add parity regressions.
Check Endoreg consumers through `tests/environment/test_secret_resolution.py` and
the upload contract. This source-change assignment does not include host activation,
key rotation or data migration.

## 7. Release handoff: native runtime and existing data

**Receiving owner:** Release/operations; storage, security and clinical approvers
for any later media migration.
**Authority:** [StorageSecurity.yml](../StorageSecurity.yml),
[ImportPipelineRobustness.yml](../ImportPipelineRobustness.yml),
[VideoStorageNormalization.yml](../VideoStorageNormalization.yml),
[StateMachineConformance.yml](../StateMachineConformance.yml).

**Deliverable:** A reviewable release package containing matching Python code,
Rust extension, stubs and schema, with versions and artifact hashes. Confirm native
capabilities and import-state vocabulary match Python. Before applying migration
`0117_center_shift_presence_fraction`, identify invalid existing values without
silently correcting them; migration must abort on such data. Check keyring files
and service access without disclosing keys.

**Acceptance:** Explicit target artifact, verification under the deployment runtime,
data preflight, backup/rollback procedure and separate authorization for the actual
target host. Local tests are not deployment evidence. Upload detection, audit
persistence and import prechecks remain fail-closed when configuration is missing.
Storage cleanup remains disabled. This handoff grants no approval to delete
canonical masters or execute destructive legacy migration.

**Verification:** Pyright followed by the selected native parity, upload, audit,
CenterShift and import tests; record the exact selection in release evidence.
The original broad audit run was supplemented by focused fixes and reruns: do
not describe it as a subsequently repeated, fully green complete suite.
