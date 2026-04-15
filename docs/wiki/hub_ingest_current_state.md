# Hub Functionality Current State

## Purpose

This page documents the hub-related behavior that is implemented in this
repository today.

It is a current-state engineering and operator note. It describes the code that
exists now, not the full target architecture from
`endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`.

## Short Answer

The repository already supports a hub-aware ingest model with three entry
boundaries:

- `watcher`: trusted local drop-folder ingest
- `api`: upload-job based HTTP ingest
- `transfer`: node-to-node transfer-job ingest for hub deployments

These boundaries share center resolution, provenance recording, processing
handoff, and retention semantics. The repository is therefore more than a
single upload endpoint.

It is still not a complete peer-to-peer federation layer. The implemented
system is best understood as one central ingest-capable deployment with optional
node-to-hub synchronization.

## Deployment Roles

Hub behavior is driven by `ENDOREG_DEPLOYMENT_ROLE`, not by a separate
`ENDOREG_HUB_MODE` or `ENDOREG_ENABLE_HUB_TRANSFERS` flag.

Supported roles:

- `standalone`
- `site_node`
- `central_hub`

Current role-dependent behavior:

- `central_hub` enables hub-mode API upload policy
- `central_hub` exposes `/api/media/hub/transfers/`
- `standalone` and `site_node` keep the transfer endpoints disabled with `404`
- production `central_hub` settings require a non-SQLite database
- production `central_hub` settings also require secure transport plus mTLS
  configuration for transfer traffic

Relevant files:

- `endoreg_db/services/hub/deployment.py`
- `endoreg_db/config/settings/base.py`
- `endoreg_db/config/settings/prod.py`

## Core Hub Models

### Center identity

`Center.center_key` is the durable machine-facing site identifier.

Current properties:

- unique
- generated automatically when absent
- immutable after assignment
- used for API ingest scoping and transfer source-center resolution

Relevant file:

- `endoreg_db/models/administration/center/center.py`

### Network topology identity

The repository now has an explicit deployment-node model: `NetworkNode`.

Current properties:

- immutable `node_key`
- `role` of `central_hub`, `site_node`, or `standalone`
- optional `owning_center`
- `base_url` for deployment identity
- hashed `shared_secret_hash` for request authentication

`NetworkNode.shared_secret` is request-auth material only. It is not used for
payload encryption.

Relevant file:

- `endoreg_db/models/hub/network_node.py`

### Upload ledger

`UploadJob` is the ingest ledger for `watcher` and `api`.

Current fields of interest:

- `source_center`
- `source_system`
- `content_hash`
- `idempotency_key`
- `ingest_mode`
- `storage_class`
- `storage_tier`
- `retention_policy`
- `cleanup_status`
- typed JSON `processing_provenance`

Upload provenance is validated at the model boundary with pydantic-backed schema
validation.

Relevant files:

- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/services/hub/payloads.py`

### Transfer ledger

`TransferJob` is the cross-node synchronization ledger.

Current fields of interest:

- `transfer_key`
- `source_node`
- `target_node`
- `source_center`
- `resource_kind`
- `resource_hash`
- `transfer_mode`
- `transfer_status`
- `processing_policy`
- `processing_intent`
- `processing_decision`
- `cleanup_policy`
- `cleanup_status`
- typed JSON `provenance`

Transfer provenance is also validated at the model boundary.

Relevant files:

- `endoreg_db/models/hub/transfer_job.py`
- `endoreg_db/services/hub/payloads.py`

## Ingress Boundaries

### 1. Watcher ingest

Watcher ingest remains a first-class trusted local path.

Current behavior:

- scans configured local drop folders for reports, videos, and preanonymized
  media
- resolves the default center when no center is explicitly passed
- creates or reuses an `UploadJob`
- uses content hash plus file metadata for watcher idempotency
- processes the artifact through the same import services used elsewhere
- uses `delete_after_success` retention for watcher source artifacts

There is also a preanonymized watcher path that accepts a sidecar JSON payload,
validates it as `PreanonymizedIngestPayload`, and persists report or video data
without re-running the full anonymization pipeline.

Relevant files:

- `endoreg_db/services/file_watcher.py`
- `endoreg_db/services/hub/ingest.py`

### 2. Upload API

The upload API is the default HTTP ingress boundary:

- `POST /api/upload/`
- `GET /api/upload/<uuid>/status/`

Current behavior:

- accepts report and video uploads
- resolves center identity from the authenticated user, declared
  `center_key` or `center_name`, or the default center depending on role
- creates or reuses an `UploadJob`
- deduplicates first by `content_hash`, then by logical `idempotency_key`
- hands off processing through Celery when available, otherwise inline
- exposes center-scoped upload status responses

When `ENDOREG_DEPLOYMENT_ROLE=central_hub`:

- authentication is required for API uploads
- `center_key` must be declared
- default-center fallback is disabled on the API path

Relevant files:

- `endoreg_db/views/misc/upload_views.py`
- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/serializers/hub/upload_job.py`
- `endoreg_db/tasks.py`

### 3. Transfer API

The transfer API is the node-to-hub synchronization boundary:

- `POST /api/media/hub/transfers/`
- `GET /api/media/hub/transfers/<transfer_key>/status/`
- `POST /api/media/hub/transfers/<transfer_key>/media/`

Current gating:

- available only when `ENDOREG_DEPLOYMENT_ROLE=central_hub`
- returns `404` in `standalone` and `site_node` deployments

Current validation and security behavior:

- secure transport is enforced when configured
- if the request is not already authenticated as a user, the caller must
  present `X-Network-Node-Key` and `X-Network-Node-Secret`
- when `ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true`, the request must also carry
  the configured proxy-verified mTLS metadata
- source-center access is still scoped for authenticated users

Current transfer-mode rules:

- `metadata_only` is supported
- `metadata_and_processed_media` is supported
- raw-media transfer modes are rejected
- the media upload endpoint accepts only anonymized `processed` media

Relevant files:

- `endoreg_db/views/media/hub/transfers.py`
- `endoreg_db/serializers/hub/transfer_job.py`
- `endoreg_db/services/hub/transfers.py`

## Shared Processing Behavior

### Upload jobs

`watcher` and `api` converge on the same upload processing core.

Current behavior:

- `start_upload_job_processing(...)` normalizes provenance and chooses Celery
  or inline execution
- `process_upload_job(...)` dispatches to `ReportImportService` or
  `VideoImportService`
- successful completion marks the job `anonymized`
- failed processing marks the job `error`
- missing or inconsistent stored input fails loudly rather than silently
  recovering

### Transfer jobs

Transfer registration does more than record a request.

Current behavior:

- creates or updates placeholder `VideoFile` or `RawPdfFile` records
- applies state payloads and `SensitiveMeta`
- records `ProcessingHistory`
- attempts case resolution and records linkage status
- computes a transfer decision such as:
  - `wait_for_missing_media`
  - `skip_processing_existing_success`
  - `skip_processing_preserved_state`
  - `mark_inconsistent`

This means a transfer can synchronize metadata first, then wait for processed
media, or suppress replay when equivalent local success already exists.

Relevant files:

- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/services/hub/transfers.py`

## Provenance, Audit, And Cleanup

### Provenance

The repository now treats persisted provenance as a typed contract.

Normalized upload provenance includes fields such as:

- `entrypoint`
- `ingest_mode`
- `source_system`
- `source_center_key`
- `storage_class`
- `storage_tier`
- `retention_policy`

Normalized transfer provenance includes fields such as:

- `entrypoint`
- `source_node_key`
- `target_node_key`
- `source_center_key`
- `transfer_mode`
- `processing_policy`
- `cleanup_policy`

### Audit

Hub services emit structured JSON audit events through the dedicated hub audit
logger.

Examples include:

- center resolution
- upload-job creation and reuse
- transfer-job creation and reuse

Relevant file:

- `endoreg_db/services/hub/audit.py`

### Cleanup

Upload cleanup is implemented today.

Current upload behavior:

- `preserve_source` becomes `cleanup_status=skipped` on success
- `delete_after_success` becomes `cleanup_status=eligible` on success
- `reap_upload_job_sources(...)` deletes persisted source artifacts for eligible
  jobs and marks cleanup `completed`

Current transfer behavior:

- `retain_all` becomes `cleanup_status=not_requested`
- non-`retain_all` policies are recorded as deferred operator intent
- automatic transfer-artifact cleanup is not implemented yet

Relevant files:

- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/services/hub/cleanup.py`
- `endoreg_db/models/hub/transfer_job.py`

## Operational Boundaries And Current Limits

Implemented today:

- center-key based tenant routing
- explicit node identity with `NetworkNode`
- upload-job based API and watcher ingest
- transfer-job based node-to-hub metadata synchronization
- processed-media upload for transfer flows
- center-scoped status reads
- structured provenance and audit output

Not implemented yet:

- envelope encryption for transferred blobs
- KMS-backed key management
- full peer discovery or topology negotiation
- automatic transfer cleanup execution
- cross-node conflict resolution between multiple authoritative peers
- complete federation of the wider database graph beyond the currently applied
  media-related rows

The safe operator interpretation is:

- use `central_hub` as the central ingest authority
- use `site_node` or external clients as senders
- rely on `center_key` and `node_key` for machine-facing identity
- treat transfer support as controlled hub synchronization, not full mesh
  federation

## Code Map

Primary files for current hub functionality:

- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/services/hub/transfers.py`
- `endoreg_db/services/hub/deployment.py`
- `endoreg_db/services/hub/payloads.py`
- `endoreg_db/services/hub/audit.py`
- `endoreg_db/services/hub/cleanup.py`
- `endoreg_db/views/misc/upload_views.py`
- `endoreg_db/views/media/hub/transfers.py`
- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/models/hub/transfer_job.py`
- `endoreg_db/models/hub/network_node.py`
- `endoreg_db/models/administration/center/center.py`

## Related Docs

- `docs/deployment_note_hub_contract.md`
- `docs/wiki/hub_ingest_gap_closure.md`
- `endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`
