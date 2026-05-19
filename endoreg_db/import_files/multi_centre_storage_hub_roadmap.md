# Multi-Centre Storage Hub Roadmap

## Purpose

This document is for engineers extending `endoreg-db` for multi-centre
operation.

It reflects the current repository state and the remaining roadmap work for
using `endoreg-db` as a hub-grade storage and ingest component. It is not a
neutral architecture note: it identifies what is already implemented, what is
safe to rely on, and what still blocks authoritative multi-centre hub
operation.

## Current Position

`endoreg-db` is now hub-aware. The repository implements centre identity,
upload-job based ingest, local watcher-style ingest functions, and an optional
node-to-hub transfer ledger.

It is still not a complete federated storage layer. The implemented shape is
best described as:

- one central ingest-capable deployment
- optional site-node to central-hub synchronization
- trusted local drop-folder ingestion for selected deployments
- protected local filesystem storage, with streaming offload where configured

The current cryptographic phase is Phase 1: transport and authentication.
Central-hub production settings require secure transport and mTLS metadata for
hub transfer traffic. Envelope encryption and KMS integration are not
implemented yet.

## Implemented Capabilities

### Deployment roles

Hub behavior is driven by `ENDOREG_DEPLOYMENT_ROLE`.

Implemented roles:

- `standalone`
- `site_node`
- `local_study_server`
- `central_hub`

Current production settings enforce several fail-closed checks:

- production startup rejects `DJANGO_DEBUG=true`
- `central_hub` and `local_study_server` reject SQLite
- `central_hub` requires secure transfer transport
- `central_hub` requires mTLS configuration for transfer traffic
- `ENDOREG_ENABLE_HUB_TRANSFERS=true` is allowed only with
  `ENDOREG_DEPLOYMENT_ROLE=central_hub`
- `local_study_server` requires explicit protected runtime storage roots
  inside `LX_ANNOTATE_ENCRYPTED_DATA_DIR`

Relevant files:

- `endoreg_db/services/hub/deployment.py`
- `endoreg_db/config/settings/prod.py`
- `tests/deployment/test_prod_settings_contract.py`

### Centre identity

`Center.center_key` is implemented as the durable machine-facing centre
identifier.

Current behavior:

- generated automatically when absent
- unique
- immutable once assigned
- separate from `display_name`
- used by API upload, local study server workflows, dataset export scoping, and
  transfer source-centre resolution

Name-based lookup still exists for compatibility, including natural keys and
some legacy resolution paths. Machine-facing integrations should use
`center_key`.

Relevant file:

- `endoreg_db/models/administration/center/center.py`

### Node identity

`NetworkNode` is implemented for deployment-node identity.

Current behavior:

- `node_key` is generated when absent and immutable once assigned
- node roles include `central_hub`, `site_node`, and `standalone`
- nodes may be linked to an owning centre
- shared secrets are stored as password hashes in `shared_secret_hash`
- shared secrets are used only for request authentication

`NetworkNode.shared_secret_hash` must not be used for payload encryption. It is
not a data-encryption key and must not replace mTLS or future envelope
encryption.

Relevant file:

- `endoreg_db/models/hub/network_node.py`

### Upload ingest

The upload API is implemented at:

- `POST /api/upload/`
- `GET /api/upload/<uuid>/status/`

Current behavior:

- accepts report and video uploads
- creates or reuses `UploadJob`
- records `source_center`, `source_system`, `content_hash`,
  `idempotency_key`, `ingest_mode`, storage lifecycle fields, and typed
  `processing_provenance`
- deduplicates first by active content hash, then by scoped idempotency key
- dispatches processing through Celery when available, otherwise inline
- preserves API upload source files after successful processing
- centre-scopes upload status reads for authenticated centre-bound users

Strict centre-scoped API upload mode is active when the role is
`central_hub` or `local_study_server`.

In strict mode:

- authentication is required
- `center_key` is required
- unknown `center_key` is rejected
- default-centre fallback is disabled for API upload
- authenticated centre-bound users may not upload outside their centre

In `standalone` and `site_node` modes, default-centre fallback remains for
compatibility.

Relevant files:

- `endoreg_db/views/misc/upload_views.py`
- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/serializers/hub/upload_job.py`
- `tests/views/misc/test_upload_endpoints.py`

### Watcher-style local ingest

The current repository implements watcher-compatible service functions rather
than a standalone watcher daemon entrypoint in the current file tree.

Implemented entrypoints:

- `process_watcher_file(...)`
- `process_preanonymized_watcher_file(...)`
- `create_or_reuse_watcher_upload_job(...)`

Current behavior:

- raw watcher ingest supports report and video files outside
  `local_study_server`
- `local_study_server` rejects raw watcher ingest and requires the
  preanonymized path
- preanonymized watcher ingest accepts `.pdf`, `.mp4`, and `.txt`
- local study server preanonymized ingest requires a strict JSON sidecar
- sidecars are pydantic-validated
- sidecar hash mismatches, unknown centres, unsafe paths, or missing human
  validation fail loudly and quarantine the drop where applicable
- watcher jobs use `UploadJob.IngestMode.WATCHER`
- watcher source artifacts use `delete_after_success` retention
- duplicate watcher drops reuse active jobs and remove duplicate drop files

Relevant files:

- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/services/hub/payloads.py`
- `tests/services/test_watcher_storage_import.py`
- `tests/services/test_preanonymized_watcher_ingest.py`
- `docs/local_study_server_deployment.md`

### Transfer ingest

The transfer API is implemented under the media endpoint namespace:

- `POST /api/media/hub/transfers/`
- `GET /api/media/hub/transfers/<transfer_key>/status/`
- `POST /api/media/hub/transfers/<transfer_key>/media/`

Current behavior:

- transfer endpoints are exposed only when
  `ENDOREG_DEPLOYMENT_ROLE=central_hub` and
  `ENDOREG_ENABLE_HUB_TRANSFERS=true`
- endpoints return `404` for `standalone`, `site_node`, and
  `local_study_server`
- secure transport is enforced when configured
- mTLS proxy metadata is enforced when configured
- node authentication uses `X-Network-Node-Key` and
  `X-Network-Node-Secret`
- `TransferJob` records source node, target node, source centre, resource kind,
  resource hash, transfer mode, processing policy, cleanup policy, status, and
  typed provenance
- transfer registration is idempotent by `transfer_key`
- metadata registration creates or updates placeholder `VideoFile` or
  `RawPdfFile` records, applies state payloads, records processing history, and
  attempts case resolution
- transfer media upload accepts only anonymized `processed` media
- raw media transfer modes are rejected
- processed video uploads are hash-checked against
  `processed_video_hash`

This transfer path is controlled hub synchronization. It is not full mesh
federation and it does not currently encrypt standalone transfer blobs with
envelope encryption.

Relevant files:

- `endoreg_db/views/media/hub/transfers.py`
- `endoreg_db/serializers/hub/transfer_job.py`
- `endoreg_db/services/hub/transfers.py`
- `endoreg_db/models/hub/transfer_job.py`
- `tests/views/media/test_hub_transfer_endpoints.py`

### Provenance, audit, and cleanup

Persisted upload and transfer provenance are validated at the model boundary
using pydantic-backed schemas.

Implemented provenance schemas:

- `UploadProvenancePayload`
- `TransferProvenancePayload`
- `PreanonymizedIngestPayload`
- `LocalStudyServerPreanonymizedIngestPayload`

Hub services emit structured JSON audit events for centre resolution, upload
job creation and reuse, transfer job creation and reuse, and selected
preanonymized drop outcomes.

Upload cleanup is implemented:

- `preserve_source` becomes `cleanup_status=skipped` after success
- `delete_after_success` becomes `cleanup_status=eligible` after success
- `reap_upload_job_sources(...)` deletes eligible persisted upload sources

Transfer cleanup is only recorded as policy intent today. Automatic execution
of transfer cleanup policies is not implemented yet.

Relevant files:

- `endoreg_db/services/hub/payloads.py`
- `endoreg_db/services/hub/audit.py`
- `endoreg_db/services/hub/cleanup.py`
- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/models/hub/transfer_job.py`

### Storage and streaming

The repository has moved toward typed storage behavior, but remains mostly
protected-filesystem based.

Implemented today:

- `VideoStorageMode` models encrypted application storage versus filesystem
  encrypted streamable storage
- upload artifacts are routed through typed `UploadJob` storage tier,
  storage class, and retention policy fields
- explicit local file moves, copies, writes, quarantine moves, and safe unlink
  operations in hub ingest paths use wrappers from
  `endoreg_db.utils.file_operations`
- those wrappers use atomic write, move, and copy semantics and emit structured
  JSON logs
- video streaming can use Nginx `X-Accel-Redirect` for streamable protected
  artifacts
- report streaming can also hand off to Nginx when a safe local protected path
  exists
- raw Django video streaming fallback can be disabled for operational safety

Remaining storage work is still significant:

- many workflows still assume local protected path access under `STORAGE_DIR`
- not every Django storage mutation is routed through the typed filesystem
  wrappers yet
- object-storage-style access is not implemented as the primary abstraction
- transfer media are not envelope-encrypted
- KMS-backed key management and key rotation are not implemented

Relevant files:

- `endoreg_db/models/media/video/storage_mode.py`
- `endoreg_db/utils/file_operations.py`
- `endoreg_db/utils/storage_profile.py`
- `endoreg_db/views/video/video_stream.py`
- `endoreg_db/views/report/report_stream.py`

### Export surfaces

Export and retrieval remain product features, not operational replication or
disaster-recovery mechanisms.

Current export and retrieval paths include:

- Django fixture export/import through `export_db.sh` and `import_db.sh`
- frame and annotation dataset export through
  `endoreg_db/export/frames/export_frames_with_labels.py`
- report and video stream endpoints under `/api/media/...`
- local study server training export scoping with `center_key` or
  `all_centers`

Fixture export is still development and migration tooling. Hub-grade operations
must use database-native backup plus protected media backup/versioning.

## Current Blockers To Authoritative Hub Operation

### Read-side centre scoping is incomplete

Upload status and transfer status paths have centre-aware scope checks, but the
general report/video read surfaces are not yet uniformly centre-filtered.
Existing tests explicitly preserve some media reads outside centre scope.

Required outcome:

- all patient-linked, report-linked, video-linked, upload, transfer, export,
  search, and timeline reads must have consistent centre-aware authorization
  before the system is considered an authoritative multi-centre hub

### Envelope encryption is not implemented

The current transfer architecture is Phase 1. It relies on mTLS and request
authentication. It does not wrap standalone transfer blobs with per-transfer
data encryption keys.

Required outcome for Phase 2:

- generate a per-transfer data encryption key
- encrypt outbound payloads with that data encryption key
- wrap the data encryption key with the receiving hub public key
- never transmit a long-lived master key

Until this exists, transfer media must remain constrained to anonymized
processed media and protected transport.

### KMS integration is not implemented

There is no Vault or KMS-backed key lifecycle in the current repository.

Required outcome for Phase 3:

- delegate long-lived key lifecycle, rotation, and machine identity policy to
  LuxNix-provided KMS or Vault when available

### Storage abstraction is still local-filesystem oriented

The runtime storage layout is protected, typed in several places, and safer
than the older raw path model. It still assumes local or shared filesystem
access for many workflows.

Required outcome:

- make integration-facing workflows depend on storage services and typed media
  references rather than direct local path availability
- define durable shared media semantics for central-hub deployments
- keep Nginx offload as an implementation detail behind authenticated API
  authorization

### Transfer cleanup is deferred

Upload source cleanup is implemented. Transfer cleanup policies are recorded
but not executed automatically.

Required outcome:

- implement audited cleanup execution for transfer artifacts
- fail loudly if cleanup intent conflicts with media integrity, retention, or
  backup requirements

### Federation remains limited

The transfer layer covers media-related metadata and processed-media
synchronization. It does not implement peer discovery, topology negotiation,
full database graph synchronization, or cross-peer conflict resolution.

Required outcome:

- define which node is authoritative for each resource class
- reject ambiguous multi-writer cases unless explicit conflict rules exist
- keep transfer semantics idempotent and fail-closed

## Required Architecture Changes

### Security and transport

The current implementation should remain within Phase 1 until Phase 2 is
designed and implemented.

Required outcomes:

- keep central-hub production startup fail-closed when mTLS configuration is
  absent
- keep `NetworkNode.shared_secret_hash` limited to request authentication
- reject active hub transfer nodes that have no usable request-auth secret
  unless a stronger mTLS-only machine identity policy is explicitly modeled
- continue rejecting raw-media transfer
- implement envelope encryption before allowing standalone transfer blobs to
  leave the local storage boundary

### Tenancy and authorization

Centre identity is now in place. Authorization still needs broad coverage.

Required outcomes:

- use `center_key` for all machine-facing centre selection
- remove remaining name-first integration paths except compatibility-only
  imports
- apply centre-aware authorization consistently to media reads, report reads,
  video reads, timelines, exports, search, upload jobs, transfer jobs, and
  workflow endpoints
- ensure list endpoints are filtered, not only detail endpoints

### Ingestion

Upload, watcher-style, and transfer ingest should continue to converge on
shared ledger and provenance patterns.

Required outcomes:

- keep strict API upload behavior for `central_hub` and `local_study_server`
- preserve watcher-compatible local ingest for trusted local workflows
- keep raw watcher ingest disabled in `local_study_server`
- keep local study server preanonymized sidecars strict and fail-closed
- add or document the operational watcher daemon/scheduler if drop-folder
  polling is expected in production
- keep duplicate detection content-hash first and idempotency-key second

### Persistence and storage

Current hub and local study roles reject SQLite in production. That foundation
should remain.

Required outcomes:

- require PostgreSQL or an equivalent durable multi-user database for hub
  deployments
- define production backup and restore around database-native backups plus
  protected media backup
- make storage routing exhaustive and enum-driven
- continue using typed filesystem wrappers for every filesystem mutation
- reduce direct dependence on `STORAGE_DIR` absolute paths for integration
  behavior

### Operations

Operational guidance exists for local study server deployment, but central-hub
runbooks are still incomplete.

Required outcomes:

- document central-hub backup and restore
- document mTLS proxy expectations and headers
- document node and centre provisioning
- document transfer cleanup and quarantine handling
- define health checks for failed/lost upload jobs, inconsistent transfer jobs,
  quarantine growth, storage capacity, audit logging, and media integrity

## Implementation Phases

### 1. Close authorization gaps

Deliver next:

- centre-filtered report and video list endpoints
- centre checks on report and video detail and stream endpoints
- centre-scoped timeline, search, export, and workflow endpoints
- tests proving cross-centre reads are denied or filtered consistently

### 2. Harden transfer authentication

Deliver next:

- explicit active-node credential requirements
- tests for missing `shared_secret_hash` in central-hub transfer mode
- documented proxy mTLS metadata contract
- audit events for all transfer auth failures that can be safely logged

### 3. Add envelope encryption

Deliver next:

- per-transfer data encryption key generation
- payload encryption with the data encryption key
- data encryption key wrapping with receiver public key
- transfer metadata fields for wrapped keys and encryption parameters
- tests proving no master key is transmitted or persisted in application config

### 4. Finish storage abstraction

Deliver next:

- typed storage-service interface for integration-facing media access
- exhaustive storage-mode branching for video and report artifacts
- removal of direct local path assumptions from transfer and export boundaries
- durable shared-media or object-storage-compatible deployment contract

### 5. Complete operations

Deliver last:

- central-hub backup and restore runbook
- transfer cleanup executor
- media integrity reconciliation for hub transfers
- production monitoring and alerting guidance

## Acceptance Criteria

The hub roadmap is complete only when all of the following are true:

- a remote site node can submit anonymized metadata and processed media to a
  central hub with mTLS and node authentication
- raw media export or raw media transfer is rejected
- transfer blobs that leave the local storage boundary are envelope-encrypted
- duplicate upload and transfer submissions resolve idempotently
- non-privileged users cannot read or list data outside their allowed centre
- hub deployments run on a durable production database
- media storage has documented backup, restore, and integrity reconciliation
- transfer cleanup policies are executed or explicitly deferred with audited
  state
- recovery procedures restore both database state and protected media references

## Verification Plan

Required verification scenarios:

- central-hub production settings fail without mTLS configuration
- central-hub production settings fail with SQLite
- authenticated API upload with valid `center_key` succeeds
- unauthenticated strict-mode API upload is rejected
- missing strict-mode `center_key` is rejected
- same logical upload resolves idempotently by content hash or idempotency key
- local study server rejects raw watcher ingest
- local study server accepts only strict validated preanonymized sidecars
- transfer endpoints return `404` outside enabled central-hub transfer mode
- transfer registration requires secure transport, mTLS metadata, and matching
  node credentials
- transfer registration rejects raw media modes
- transfer media upload rejects `raw` and accepts only verified `processed`
  media
- centre-scoped users cannot read or list other centres' reports, videos,
  uploads, transfers, timelines, or exports
- streaming continues to work through Nginx offload after authorization checks
- database-native restore plus protected-media restore preserves media
  references and integrity

## Compatibility Guardrails

The current transition must preserve existing safe workflows while tightening
hub behavior.

Guardrails:

- keep `center_key` immutable
- keep name-based centre resolution only where needed for compatibility
- keep default-centre fallback outside strict API upload modes
- keep watcher-compatible service functions for trusted local ingestion
- keep `local_study_server` raw watcher ingest disabled
- keep upload source cleanup retention-driven
- keep fixture export/import available for development and migration work
- do not position fixture export/import as hub disaster recovery
- keep all API payload fields snake_case

## Assumptions

- This document is an engineering roadmap, not user-facing documentation.
- It intentionally separates current implementation from remaining target
  architecture.
- It preserves export and streaming as product features while treating backup,
  synchronization, and cryptographic transfer as separate operational
  concerns.
