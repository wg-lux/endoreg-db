# Production Workflow Hardening Plan

## Goal

Make the media import, anonymization, validation, segment review, case resolution, and reporting flow safe for production-grade data import and reporting.

## Scope

This plan covers:

- video import
- pdf import
- anonymization
- sensitive metadata validation
- segment validation
- case resolution
- report submission and finalization
- reporting materialization

This plan does not assume that compatibility shims or fallback behavior are acceptable as final production behavior.

## Hard Requirements

### Canonical workflow state machine

Define one canonical workflow model with explicit states and transitions for:

- imported
- anonymization_in_progress
- anonymized
- sensitive_metadata_validated
- segment_review_required
- segment_review_completed
- case_resolution_required
- case_resolution_completed
- report_draft
- report_finalized
- failed

Each transition must define:

- allowed predecessor states
- required data prerequisites
- actor or system responsibility
- rejection reason on invalid transition
- audit event emitted

### Workflow invariants

Enforce these invariants in code, not just in UI:

- no report finalization without resolved `patient_examination`
- no video annotation workflow if anonymization is incomplete
- no report materialization if `document_type` is unresolved
- no export/materialization with unresolved segment labelset
- no silent fallback for unsupported requirement/report validators in production mode
- no segment validation completion if required review segments remain unresolved

### End-to-end verification

Add end-to-end integration coverage for:

1. video import -> anonymization -> sensitive metadata validation -> segment review -> case resolution -> reporting handoff
2. pdf import -> validation -> case resolution -> report materialization
3. retry and reimport of the same source asset
4. duplicate import of the same source asset
5. partial failure recovery after a mid-pipeline exception

### Stable API contracts

Add contract tests for:

- video metadata payloads
- segment list/create/update payloads
- anonymization validation payloads
- case-resolution payloads
- report submission payloads

These tests must assert payload shape explicitly at the frontend/backend boundary.

### Idempotent import behavior

Guarantee:

- repeated import of the same asset does not create uncontrolled duplicate workflow records
- reimport is explicit and auditable
- duplicate detection rules are deterministic
- state transitions on retries are deterministic

### Observability

Add:

- correlation IDs for workflow operations
- structured logs for every major transition
- audit events for validation, case resolution, report finalization, and reimport
- counters/metrics for import failures, validation failures, unresolved case matches, materialization failures

### Legacy API cleanup

Classify every transitional endpoint or compatibility shim as one of:

- supported
- deprecated
- scheduled for removal

No compatibility path should remain undocumented.

## Implementation Phases

### Phase 1: Explicit workflow invariants

Add server-side guards at the highest-risk boundaries:

- report finalization
- report materialization
- video segment review completion
- case resolution writes

Deliverables:

- invariant helper module
- explicit validation errors
- test coverage for blocked transitions

### Phase 2: Canonical workflow state model

Introduce a central workflow state representation for media/report progression.

Deliverables:

- state definitions
- transition helpers
- audit emission per transition
- migration path from existing booleans and derived status fields

### Phase 3: End-to-end workflow tests

Create integration scenarios for real workflow execution.

Deliverables:

- video workflow integration test
- pdf workflow integration test
- retry/reimport integration test
- duplicate import integration test
- failure recovery integration test

### Phase 4: Contract tests

Freeze frontend/backend payload expectations.

Deliverables:

- serializer/view response snapshots or explicit schema assertions
- request payload assertions for mutation endpoints
- documented compatibility rules

### Phase 5: Import idempotency and deduplication

Harden import services and persistence layer.

Deliverables:

- duplicate detection policy
- reimport policy
- deterministic storage/state update rules
- audit logging for import/reimport decisions

### Phase 6: Observability and operational readiness

Deliverables:

- correlation ID propagation
- structured workflow logs
- metrics emitters
- operator-facing failure summaries

### Phase 7: Legacy cleanup

Deliverables:

- endpoint inventory
- deprecation annotations
- removal of dead compatibility routes
- removal of no-longer-needed serializer shims

## Immediate Backlog

### P0

- enforce `patient_examination` linkage before final report submission/finalization
- enforce resolved `document_type` before PDF report materialization
- enforce resolved segment labelset before segment export/materialization
- replace unsupported validator fallback behavior with explicit rejection in production mode

### P1

- add integration test for pdf validation -> case resolution -> report materialization
- add integration test for video anonymization -> segment review readiness
- add contract tests for segment CRUD payloads and report submission payloads

### P2

- add correlation IDs and structured workflow logging
- define duplicate import policy and implement tests
- mark all remaining legacy routes/shims with support status

## Suggested First PRs

### PR 1

Introduce workflow invariant checks in report finalization and materialization paths.

### PR 2

Add integration tests for pdf validation + case resolution + report materialization.

### PR 3

Add integration tests for video anonymization + segment review readiness + validation completion.

### PR 4

Introduce contract tests for video/segment/report payloads used by the frontend.

## Done Criteria

The workflow is production-grade only when:

- all critical transitions are server-enforced
- no critical path depends on fallback behavior
- import/reimport behavior is deterministic and audited
- frontend/backend contracts are tested explicitly
- end-to-end workflow tests pass
- observability is sufficient to diagnose failures without ad hoc reproduction
