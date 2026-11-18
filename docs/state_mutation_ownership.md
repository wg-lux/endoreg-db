# State Mutation Ownership

Long-running workers, retries and operator actions can change the same database
row concurrently. A lifecycle field (a status or state) stays consistent only
if its writes are owned: each write happens under the row lock and passes a
transition check, ideally in one module. This document explains how the
project records and enforces that ownership. Scope and readiness evidence live
only in
[`feature-tracking/StateMachineConformance.yml`](../feature-tracking/StateMachineConformance.yml).

## The map and the check

[`quality/state_mutation_owners.yml`](../quality/state_mutation_owners.yml)
lists every module in `endoreg_db` that writes a field named `status`, `state`,
`*_status`, `*_state` or `failure_class`, together with the state machine it
belongs to and a reviewed writer kind.

[`scripts/check_state_mutation_owners.py`](../scripts/check_state_mutation_owners.py)
scans the package and fails when:

- a write is not listed (new writers need review),
- a listed write no longer exists (the map must stay exact),
- a field with a native single writer is written by any other module except
  that machine's model transition methods, or
- a native single writer's field is written outside the functions its entry
  names.

A write is an attribute assignment, a `setattr` with a literal field name, a
keyword of an `update(...)` call, or a literal field of `bulk_update(...)`.
Creating a row with its initial status is not a mutation and is not scanned.
The check runs in the `state-mutation-owners` pre-commit hook and in
`tests/scripts/test_state_mutation_owners.py`.

## Writer kinds

| Kind | Meaning |
| --- | --- |
| `native_single_writer` | The only writer of the field. It locks the row and asks a native (Rust) transition table. |
| `native_validated` | Writes under the row lock after native transition validation. |
| `model_transition_method` | Model method called only by its owning locked lifecycle service. |
| `locked` | Writes under a row lock or a conditional update, with checks in Python only. |
| `state_relation` | Assigns a foreign key to a state row; no status value changes. |
| `result_record` | Status stored on a result, report, metric or exception object. |
| `unreviewed` | Direct write without a verified lock and native check. Listed so it cannot spread, not endorsed. |

No `locked` or `unreviewed` writers remain; the kinds stay available so a
future exception is recorded honestly instead of hidden.

Current native single writers:

- Outbound hub transfers:
  [`outbound_transfer_state.py`](../endoreg_db/services/hub/outbound_transfer_state.py)
  ([state machine](hub_transfer_state_machine.md)).
- HTTP Live Streaming (HLS) artifacts:
  [`hls_artifact_state.py`](../endoreg_db/services/streaming/hls_artifact_state.py)
  ([transition table](video_hls_permissions_and_streaming.md#artifact-materialization)).
- Every other lifecycle with its own table:
  [`status_writers.py`](../endoreg_db/services/runtime/status_writers.py), backed
  by the Rust tables in
  [`status_transition.rs`](../rust/endoreg_rust_backend/src/status_transition.rs)
  (capability `status_transition_v1`).

## Native status tables

`decide_status_transition(machine, current, target)` returns apply or ignore,
and raises `ValueError` for unknown tokens or illegal transitions. An empty
`current` is an unsaved row. Each writer in `status_writers.py` requires an
atomic block, reads the persisted status under `SELECT ... FOR UPDATE`, asks
the table, and assigns the target when applied or the persisted value when
ignored, so a later save never writes back a stale status. Compare-and-set
writers (`update_frame_export_job_status`, `update_transfer_status`,
`mark_transfers_lost`) validate `expected -> target` natively and update only
rows still in `expected`; they refuse other lifecycle fields in their extra
arguments.

| Machine | Field | Final statuses | Notable rules |
| --- | --- | --- | --- |
| `report_llm_inference_job` | `status` | `success`, `cancelled` | Redelivery restarts `failure`/`lost` as `running`; late failures after success are ignored. |
| `video_processing_history` | `status` | `success`, `failure`, `cancelled` | Late outcomes after a final status are ignored. |
| `quarantine_item` | `status` | none | Only `approved_for_deletion` items are deleted; a reappearing file returns to `pending_review`. |
| `frame_annotation_export_job` | `status` | `completed`, `failed`, `lost` | Completion requires a running claim. |
| `ai_dataset_export_artifact` | `status` | `failed` | A completed artifact whose file vanished becomes `failed`. |
| `dicom_export_job` | `status` | `imported`, `failed` | |
| `patient_examination_report` | `status` | none | `final -> draft` reopening is allowed by product decision. |
| `hub_inbound_transfer` | `transfer_status` | `inconsistent` | Only `applied` becomes `lost`; `claimed`/`retry_wait` are legacy statuses no writer sets. |
| `hub_inbound_case_resolution` | `case_resolution_status` | none | Never returns to `pending`. |
| `upload_job_source_cleanup` | `cleanup_status` | `completed` | `deleting` cannot be skipped; late skips after completion are ignored. |
| `hub_outbound_local_cleanup` | `local_cleanup_status` | `cleaned` | Cleaning starts only from `eligible`. |

Rust tests check every table for final statuses that never move, defined
re-application of every applied target, and reachability of every non-legacy
status from a new row.

## Changing a writer

1. Prefer the existing writer of the machine. For a machine with a native
   single writer, call it; never assign the field directly.
2. If a new write is unavoidable, hold the row lock, validate the transition
   natively where a reducer exists, and add the entry with the honest kind.
3. When an `unreviewed` writer is migrated, change its kind in the same change.

## Limits

- The scan is syntactic. It cannot see writes through raw SQL, `**kwargs`
  expansion or dynamically built field names.
- A kind records a review decision. The check proves that the list is complete,
  not that each kind is still accurate; reviewers re-check the kind when the
  listed code changes.
- Only `endoreg_db` is scanned. Consumers such as lx-annotate write through the
  same service functions but are not checked here.
