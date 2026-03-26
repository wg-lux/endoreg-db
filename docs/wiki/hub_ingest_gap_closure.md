# Hub Ingest Gap Closure

## Purpose

This page defines the next concrete architecture needed to move the current
`hub_ingest` implementation from "single central ingest endpoint" toward a real
study-network hub model.

It addresses the missing pieces called out in:

- `docs/wiki/hub_ingest_current_state.md`
- `endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`

This document is implementation-oriented. It specifies what should be built,
what identifiers should be used, how row-plus-media transfer should work, and
how the receiver decides whether processing must run again.

The key missing capability is explicit data transfer for resources that include
both:

- the database rows that describe a video or report
- the corresponding media payload

Without that, a hub can only receive bytes. It cannot reliably decide whether
processing should start, resume, or be skipped.

## Design Goals

The next version of hub ingest must support:

- authenticated remote site to hub transfer
- transfer of database row payloads together with the referenced video or report
- deterministic replay behavior on the receiver
- explicit processing policy so the hub can start or skip processing
- transfer acknowledgement back to the sender
- storage policies that allow central cleanup after durable synchronization
- explicit identification of the central node versus the submitting site

## Current Foundation To Reuse

The repository already contains the right primitives for an incremental design:

- `Center.center_key` as a durable site identity key
- `UploadJob` metadata fields for source and provenance
- `VideoFile.video_hash` and `RawPdfFile.pdf_hash` as stable content identifiers
- `ProcessingHistory.file_hash` as a logical processing identity keyed by
  content hash
- `VideoState` and `RawPdfState` as process state holders

These must be reused rather than replaced.

## Required New Concepts

### 1. Deployment node identity

The application currently models centres, but not network nodes.

Add a separate model for deployment identity.

Recommended shape:

- `NetworkNode`
- `node_key`: immutable unique key
- `display_name`
- `role`: `central_hub`, `site_node`, or `standalone`
- `base_url`
- `is_active`
- `owning_center`: optional `Center` for site nodes

Why this is needed:

- `center_key` identifies the submitting site or tenant
- it does not identify the software deployment that sent or received data
- hub routing, trust, and cleanup policy should be node-based, not center-name
  based

### 2. Transfer ledger

Upload jobs alone are not enough for durable cross-node transfer semantics.

Add a dedicated transfer ledger.

Recommended shape:

- `TransferJob`
- `transfer_key`: immutable unique logical transfer identifier
- `source_node`
- `target_node`
- `source_center`
- `resource_kind`: `video`, `report`
- `resource_hash`: `video_hash` or `pdf_hash`
- `source_object_id`: sender-side object id if available
- `target_object_id`: receiver-side object id if available
- `transfer_status`: `pending`, `receiving`, `stored`, `applied`, `failed`
- `processing_policy`: see below
- `processing_decision`: `started`, `skipped`, `reused_existing`
- `cleanup_policy`
- `cleanup_status`
- `provenance`

This ledger is the correct place to describe synchronization state. `UploadJob`
should remain the ingest boundary record, but not become the only cross-node
replication contract.

### 3. Resource synchronization snapshot

The sender must transfer a canonical synchronization snapshot for the resource.

Recommended shape:

- `ResourceSyncSnapshot`
- stored as JSON on `TransferJob` for the first implementation
- contains the normalized row payload used for apply
- contains the sender-side processing summary used for replay decisions

This avoids tying replay behavior to ad-hoc serializer output.

## Remote Transfer Contract

## Overview

Transfer must not mean "just upload a file".

A remote site must be able to submit:

- media bytes
- canonical row payload for the resource
- enough metadata for the hub to decide whether processing should run again

That means hub ingest needs a resource transfer contract, not only a file upload
endpoint.

## Resource bundle

The remote sender should submit a bundle with two parts:

- `metadata.json`
- media payload

Recommended top-level metadata shape:

```json
{
  "transfer_key": "site_a__video__<video_hash>",
  "source_node_key": "site_a_node",
  "target_node_key": "study_hub",
  "source_center_key": "site_a",
  "resource_kind": "video",
  "resource_hash": "<video_hash>",
  "processing_policy": "preserve_processing_state",
  "cleanup_policy": "delete_central_raw_after_apply",
  "payload_schema_version": "1.0",
  "resource_rows": {
    "video_file": {},
    "video_state": {},
    "sensitive_meta": {},
    "processing_history": {}
  },
  "processing_snapshot": {
    "sender_processing_state": "completed",
    "sender_processing_success": true,
    "raw_file_present": true,
    "processed_file_present": true,
    "frames_present": false,
    "replay_hint": "skip_if_receiver_has_required_artifacts"
  },
  "media": {
    "content_type": "video/mp4",
    "filename": "example.mp4",
    "sha256": "<video_hash>"
  }
}
```

All contract fields must remain `snake_case`.

## Transfer Modes

The system needs more than one transfer mode.

Recommended `transfer_mode` values:

- `metadata_only`
- `metadata_and_raw_media`
- `metadata_and_processed_media`
- `metadata_raw_and_processed_media`

Why this matters:

- some study-network flows only need state replication first
- some flows need raw media so the hub can process centrally
- some flows want to preserve sender processing and send the anonymized output
- some flows need both raw and processed media so the hub can archive and
  verify

The chosen `transfer_mode` must be recorded on `TransferJob`.

## Which rows should transfer with a video

At minimum, video synchronization should transfer:

- `VideoFile` canonical fields
- `VideoState`
- `SensitiveMeta` if hub is expected to preserve current pseudonymization state
- `ProcessingHistory` keyed by `video_hash`
- `Center` identity by `center_key`, not by mutable display name
- sender-side processing outcome summary

Optional later transfer objects:

- `Patient`
- `PatientExamination`
- attached labels, segments, and annotation summaries

The initial version should not try to replicate the entire database graph in one
step. It should transfer the canonical ingest row set needed to reconstruct the
video record and decide replay behavior.

## Canonical video row payload

The sender should transmit a normalized video bundle in this order:

1. `center`
2. `patient` and `patient_examination` if they are in scope for the transfer
3. `sensitive_meta`
4. `video_file`
5. `video_state`
6. `processing_history`

Initial required payload for a video transfer:

```json
{
  "resource_rows": {
    "center": {
      "center_key": "site_a"
    },
    "sensitive_meta": {
      "patient_identifier": "<pseudonymous_id>",
      "exam_identifier": "<site_exam_id>",
      "source_video_datetime": "2026-03-20T10:00:00Z"
    },
    "video_file": {
      "video_hash": "<video_hash>",
      "processed_video_hash": "<processed_video_hash>",
      "original_file_name": "example.mp4",
      "suffix": ".mp4",
      "fps": 50.0,
      "duration": 120.5,
      "frame_count": 6025,
      "width": 1920,
      "height": 1080,
      "meta": {}
    },
    "video_state": {
      "processing_started": true,
      "sensitive_meta_processed": true,
      "raw_frames_extracted": true,
      "outside_frames_censored": true,
      "anonymized_video_created": true
    },
    "processing_history": {
      "file_hash": "<video_hash>",
      "success": true
    }
  }
}
```

The receiver must not trust sender-side primary keys. The receiver must resolve
relationships by natural keys and content hash.

## Canonical identifiers for video transfer

Use these as the durable keys:

- site identity: `Center.center_key`
- source node identity: `NetworkNode.node_key`
- content identity: `VideoFile.video_hash`
- logical transfer identity: `TransferJob.transfer_key`

Do not use:

- Django primary keys across nodes
- mutable display names
- local filesystem paths

## Processing Policy

The receiver must know whether to start processing again.

That decision must be explicit, not inferred from accidental local state.

Recommended `processing_policy` values:

- `reprocess_always`
- `reprocess_if_missing_outputs`
- `preserve_processing_state`
- `ingest_only_no_processing`

Recommended `processing_intent` values:

- `sender_requests_hub_processing`
- `sender_requests_state_preservation`
- `sender_requests_archive_only`

`processing_policy` governs allowed behavior.
`processing_intent` records what the sender expected.
If they disagree, the receiver should prefer policy over intent and log the
mismatch on `TransferJob`.

### Policy meanings

`reprocess_always`

- accept the transferred rows and media
- run the import/anonymization pipeline again on the receiver
- use this when the hub is the authoritative processing authority

`reprocess_if_missing_outputs`

- if the receiver already has canonical processed outputs for the transferred
  `resource_hash`, do not process again
- otherwise run processing

`preserve_processing_state`

- import row state as authoritative
- if sender says processing already completed and required artifacts are present,
  do not process again
- if artifacts are missing or inconsistent, mark transfer as incomplete and do
  not silently process unless policy fallback says so

`ingest_only_no_processing`

- store rows and media
- never start processing from this transfer
- use this for archival or secondary analysis nodes

## Receiver Decision Matrix

The receiver should persist the incoming metadata first, then compute a
`processing_decision`.

Recommended decisions:

- `start_processing`
- `skip_processing_existing_success`
- `skip_processing_preserved_state`
- `wait_for_missing_media`
- `mark_inconsistent`
- `reject_transfer`

Decision inputs:

- `processing_policy`
- `transfer_mode`
- existing `VideoFile` matched by `video_hash`
- existing `ProcessingHistory` for `file_hash`
- transferred `VideoState`
- local artifact presence for raw and processed media

### Video replay rules

For a video transfer:

1. Resolve by `video_hash`.
2. Check for a successful `TransferJob` with the same `transfer_key`.
3. Check local `ProcessingHistory(file_hash=video_hash)`.
4. Check local `VideoFile` and artifact presence.
5. Check transferred `video_state` and `processing_snapshot`.
6. Compute `processing_decision`.

Recommended matrix:

| Situation | Decision |
| --- | --- |
| Same `transfer_key` already applied | `skip_processing_existing_success` |
| New transfer, no local row, `transfer_mode=metadata_only` | `wait_for_missing_media` |
| New transfer, no local row, raw media present, `processing_policy=reprocess_always` | `start_processing` |
| Local `ProcessingHistory.success=True`, required artifacts present, `processing_policy=reprocess_if_missing_outputs` | `skip_processing_existing_success` |
| Sender says completed, transferred processed media exists, `processing_policy=preserve_processing_state` | `skip_processing_preserved_state` |
| Sender says completed, but required artifacts are missing or state is contradictory | `mark_inconsistent` |
| `processing_policy=ingest_only_no_processing` | `skip_processing_preserved_state` or `wait_for_missing_media` depending on artifact requirements |

### Inconsistency rules

Mark a transfer inconsistent when any of the following is true:

- `ProcessingHistory.success=True` but `video_state` indicates processing never
  started
- sender claims anonymized completion but no processed artifact was transferred
  and no local processed artifact exists
- transferred `processed_video_hash` does not match the uploaded processed media
- transferred `resource_hash` does not match the uploaded raw media

An inconsistent transfer must not silently trigger processing unless the policy
explicitly permits fallback reprocessing.

## Receiver Decision Rules

The receiver should decide replay using the following order.

### 1. Match by content identity

For videos:

- locate existing records by `video_hash`

For reports:

- locate existing records by `pdf_hash`

### 2. Match transfer history

If a `TransferJob` with the same `transfer_key` already succeeded:

- do not re-apply rows
- do not re-run processing
- return the existing transfer result idempotently

### 3. Match processing history

If `ProcessingHistory.success=True` already exists for the same `resource_hash`:

- apply the selected `processing_policy`
- for `reprocess_if_missing_outputs`, inspect canonical artifacts before
  deciding
- for `preserve_processing_state`, reuse the completed state if artifacts and
  transferred rows are consistent

### 4. Verify required artifacts

For `preserve_processing_state`, the receiver must verify:

- canonical raw media exists if required
- canonical anonymized media exists if sender claims anonymized completion
- row state and file reality agree

If they do not agree:

- mark the transfer inconsistent
- do not silently convert to success
- only start processing if the policy explicitly allows it

### 5. Persist the decision

The receiver must write the result to `TransferJob.processing_decision` and
`TransferJob.transfer_status`.

That decision record is required so later retries, audits, and sender polling
see the same outcome.

## Row Synchronization Rules

### General rules

- sender-side primary keys are not durable across nodes
- receiver must resolve by durable natural keys and content hashes
- row application must be idempotent
- row application must happen before any destructive cleanup

### `VideoFile`

Receiver should upsert by `video_hash`.

Fields safe to synchronize:

- `video_hash`
- `processed_video_hash`
- `original_file_name`
- `fps`
- `duration`
- `frame_count`
- `width`
- `height`
- `suffix`
- `meta`

Fields that must be rewritten locally:

- `raw_file`
- `processed_file`
- `frame_dir`
- local path-derived storage fields

Required additional local rewrite behavior:

- associate the receiver-local `center` by `center_key`
- associate `sensitive_meta` by receiver-created row ids
- rewrite all file storage fields to canonical receiver paths
- never import sender filesystem paths verbatim

### `VideoState`

Receiver should apply the transferred processing state only under a defined
policy.

That means:

- `preserve_processing_state` may import state as authoritative
- `reprocess_always` should reset local state into a fresh ingest-ready state

### `ProcessingHistory`

This is the key mechanism that should enable "start or do not start processing
again".

Required rule:

- synchronize `ProcessingHistory` by `file_hash`

Meaning:

- if a remote site already completed processing for the exact content hash, the
  hub can know that before starting work
- the hub can then apply its configured processing policy instead of blindly
  reprocessing

This is the simplest current-model-compatible way to support deterministic
replay decisions.

## Media Transfer Rules

The sender may transfer:

- raw video only
- processed video only
- both raw and processed video

Canonical rules:

- raw video identity is `video_hash`
- processed video identity is `processed_video_hash`
- receiver must verify uploaded bytes against the declared hashes
- receiver must place files into canonical managed storage paths
- receiver must never rely on sender filenames as the durable identity

### Raw video behavior

If raw media is provided:

- store under the receiver canonical raw path for `video_hash`
- connect `VideoFile.raw_file` locally
- use raw media as the processing source if the decision is
  `start_processing`

### Processed video behavior

If processed media is provided:

- verify against `processed_video_hash`
- store under the receiver canonical processed path
- connect `VideoFile.processed_file` locally
- if `processing_policy=preserve_processing_state` and the processed artifact is
  valid, do not start processing again

## Sender Acknowledgement

The transfer flow should end with an explicit acknowledgement back to the
sender.

Required acknowledgement fields:

- `transfer_key`
- `transfer_status`
- `processing_decision`
- `resource_hash`
- `target_object_id`
- `cleanup_status`
- `status_detail`

This acknowledgement is what allows the sender to know whether:

- synchronization succeeded
- the hub accepted the sender's preserved state
- the hub started its own processing
- the sender may delete its local staging copy

## Central Storage Cleanup

## Problem

Current ingest is persistence-oriented. It keeps central managed files unless
watcher-local cleanup removes an external source file.

That is not sufficient for a hub that may receive large numbers of synchronized
videos from many sites.

## Required cleanup policy

Cleanup must be explicit and policy-driven.

Recommended `cleanup_policy` values:

- `retain_all`
- `delete_central_raw_after_apply`
- `delete_central_raw_after_verified_backup`
- `delete_central_source_after_anonymized_derivatives_exist`

### Minimum safety rule

The hub must never delete the only authoritative copy before both conditions are
true:

- row synchronization completed successfully
- durable storage policy confirms the retained copy exists in the intended tier

### Initial safe recommendation

For first implementation:

- keep anonymized managed outputs centrally
- allow deletion of transferred raw central media only after:
  - transfer marked `applied`
  - row sync committed
  - required derived artifacts exist
  - backup or object-store durability precondition is satisfied

## API Surface To Add

### 1. Transfer creation endpoint

Add one authenticated endpoint for remote transfer registration.

Suggested shape:

- `POST /api/media/hub/transfers/`

Purpose:

- register metadata first
- return transfer id and upload target

### 2. Transfer media upload endpoint

Suggested shape:

- `POST /api/media/hub/transfers/<transfer_key>/media/`

Purpose:

- upload the video or report payload attached to an already declared transfer

### 3. Transfer status endpoint

Suggested shape:

- `GET /api/media/hub/transfers/<transfer_key>/status/`

Purpose:

- expose row-apply status
- expose processing decision
- expose cleanup status

### 4. Optional transfer finalize endpoint

Suggested shape:

- `POST /api/media/hub/transfers/<transfer_key>/finalize/`

Purpose:

- explicit transition from receiving to apply
- useful if transfer is chunked or multipart later

## Authentication And Authorization

The transfer contract must not rely on anonymous upload.

Required behavior:

- every remote node authenticates as a known `NetworkNode`
- the authenticated node is authorized only for allowed `Center.center_key`
  values
- transfer metadata must record both `source_node_key` and `source_center_key`
- status access must be node-scoped and centre-scoped

## Implementation Order

### Phase 1: Data model and docs

Build first:

- `NetworkNode`
- `TransferJob`
- current-state docs
- remote transfer contract docs

### Phase 2: Metadata-only transfer path

Build next:

- authenticated transfer registration
- status endpoint
- row bundle validation
- replay decision engine using `video_hash` and `ProcessingHistory`
- sender acknowledgement payload

### Phase 3: Media transfer

Build next:

- media upload bound to transfer ledger
- canonical storage placement
- post-upload row apply

### Phase 4: Cleanup and retention

Build next:

- cleanup policy execution
- safe central raw deletion
- backup verification hooks

## Acceptance Criteria

The design is only complete when all of the following are true:

- one site can transfer a video row bundle plus video bytes to the hub
- the hub can match the transfer by `video_hash`
- the hub can decide to start or not start processing again using
  `processing_policy`, `VideoState`, and `ProcessingHistory`
- the hub can return an explicit transfer acknowledgement with the replay
  decision
- the same transfer is idempotent under retry
- the central node is explicitly modeled, not only implied by deployment URL
- central cleanup policy can remove transferred raw media only after apply and
  durability checks

## Recommended First Concrete Build Slice

If implementation starts now, the first slice should be:

- add `NetworkNode`
- add `TransferJob`
- add a metadata-only transfer endpoint
- transfer `VideoFile`, `VideoState`, and `ProcessingHistory` payloads without
  media bytes first
- implement replay decision logic:
  - if `video_hash` exists and `ProcessingHistory.success=True`, skip or start
    according to `processing_policy`
  - if `video_hash` does not exist, create pending ingest and await media upload

This slice gives the project the missing processing-decision contract before the
larger media-transfer work lands.

## Minimal Implementation Notes

To keep the first implementation compatible with the current repository:

- reuse `UploadJob` as the local ingest execution record behind a `TransferJob`
- reuse `ProcessingHistory` as the canonical replay identity
- upsert `VideoFile` by `video_hash`
- use the existing reconciliation service to relink transferred artifacts if the
  apply step is interrupted

The important change is not a new ingest pipeline. The important change is that
the pipeline becomes transfer-aware and policy-driven.
