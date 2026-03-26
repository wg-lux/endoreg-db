# Multi-Centre Storage Hub Roadmap

## Purpose

This document is for engineers extending `endoreg-db` for multi-centre operation.

Its goal is to assess the current export and storage behavior of the repository
and prescribe the changes required to make `endoreg-db` suitable for hub-grade
deployment as a central multi-centre data storage system.

This is an implementation roadmap, not a neutral architecture note. The current
system is partially suitable for centralized deployment, but it is not yet a
robust authoritative multi-centre hub without additional work.

## Current Capabilities

### Export surfaces

`endoreg-db` already exposes several useful export and retrieval paths:

- Django fixture export and import via `export_db.sh` and `import_db.sh`.
  These scripts use `manage.py dumpdata` and `manage.py loaddata` as a repo-local
  backup and restore mechanism.
- Frame and annotation dataset export via
  `endoreg_db/export/frames/export_frames_with_labels.py`.
  This supports CSV and JSON export, optional media export, and optional frame
  transcoding.
- Media and report streaming and download endpoints.
  Video streaming supports Nginx offload through `X-Accel-Redirect`, while PDF
  and report endpoints support inline viewing and download flows.
- `lx_dtypes` contract-style export via `endoreg_db/services/lx_video_contracts.py`.
  This maps videos, segments, and sensitive metadata into typed `lx_dtypes`
  payloads for downstream integration.

These capabilities are real product value. They should be preserved, but they
must be positioned correctly in a hub architecture: as export and integration
features, not as the primary operational replication or recovery strategy.

### Ingestion and storage model

The current repository also already contains the basic ingredients of a
centralized application server:

- an upload job API for report and video submission
- a watcher and import-service flow for filesystem-based ingestion
- local managed storage rooted under `STORAGE_DIR`
- centre-scoped filtering already present in some report-facing APIs

The import services describe a managed-storage workflow where raw imports are
staged, canonicalized, anonymized, and then retained as durable managed files.
The surrounding services use Django transactions in a number of important write
paths, and the production settings support a non-SQLite database and a real
authentication and authorization stack.

That said, the surrounding architecture is still strongly single-node and
shared-filesystem oriented. Many code paths assume local path access, local
filesystem coordination, and storage behavior centered around one application
host or one shared mount.

## Why The Current System Is Not Yet A Multi-Centre Hub

The following blockers prevent the current system from being treated as the
authoritative multi-centre data hub.

### Centre identity is name-based and not strongly unique

The centre model currently relies on `name` as the natural lookup key. That is
serviceable for a local or lightly managed deployment, but it is not strong
enough for tenancy, routing, or machine-to-hub ingestion across many centres.

For hub-grade operation, centre identity must be immutable, unique, and safe to
use as a durable integration key. Mutable display labels are not sufficient.

### Upload endpoints are not authenticated or tenant-scoped enough

The repository already has an upload API, but the current shape is not strong
enough for a remote multi-machine ingest contract. A hub needs authenticated,
centre-aware ingestion with provenance, authorization, and durable source
identity for every job.

Anonymous or weakly-scoped upload/status access is acceptable during local
development, but not for a central study hub serving multiple centres.

### Ingestion depends on path-based file locks and shared filesystem semantics

The import pipeline uses watched import paths and path-based lock files to
coordinate work. That is useful for workstation and edge-style ingestion, but it
is not a sufficient distributed coordination model for multiple machines sending
data to one central authority.

The existing import services are retry-aware and cleanup-aware, but they do not
form a full distributed transaction protocol.

### Watcher ingestion remains filesystem-bound

Watcher-based safe system-dropoff ingestion remains an important supported path,
but it is still intentionally rooted in watched directories, file stability
checks, and path-based processing semantics. That is appropriate for trusted
local ingestion, but it is not sufficient as the sole contract for remote
multi-machine hub ingest.

### Backup and export are fixture-oriented

The current `dumpdata` and `loaddata` scripts are useful for development,
inspection, migration work, and local backups. They are not a sufficient
production-grade operational story for the authoritative multi-centre hub.

A hub needs database-native backup and restore, plus media storage backup and
versioning expectations that match the durability requirements of the study.

### Storage assumptions are too local-path oriented

Several workflows resolve files directly from `STORAGE_DIR` paths and assume the
application can inspect and serve media from a directly mounted local or shared
filesystem. That is convenient, but it couples integrations and exports too
closely to one storage topology.

### Conclusion

`endoreg-db` is suitable as a centralized study application server.

`endoreg-db` is not yet suitable as the authoritative multi-centre data hub.

## Required Architecture Changes

### Identity and tenancy

The hub must gain a stronger tenant identity model.

Required outcomes:

- add an immutable unique centre key separate from display name
- stop resolving centres purely by mutable `name`
- attach centre identity and source-system identity to every ingest path
- define tenant scoping expectations for all read and write APIs, not only a few
  selected report endpoints

The centre key should become the durable integration identifier used by remote
machines, upload jobs, and authorization policy. Display names should remain
presentation-oriented.

### Ingestion

The hub must expose one authenticated remote ingestion contract while preserving
watcher-based safe system-dropoff ingestion as a supported first-class path.

Required outcomes:

- establish one authenticated ingestion contract for remote machines
- keep watcher and dropoff ingestion as a supported ingress mode for trusted
  system-local workflows
- make upload jobs carry source centre, source system, idempotency key, and
  processing provenance
- ensure watcher ingestion and API ingestion both flow into the same ingest core
  after mode-specific entry checks
- require idempotent duplicate protection by logical ingest key, not only by
  watched file path

This means remote machines should not need to rely on a shared import directory
as the primary system boundary. The canonical remote boundary should be an
authenticated API with durable job semantics. At the same time, the watcher path
must remain available for safe dropoff-based ingestion on trusted systems.

### Persistence and storage

Hub deployments must be opinionated here.

Required outcomes:

- require PostgreSQL for hub deployments
- keep media in shared durable storage or object-storage-like semantics rather
  than assuming node-local paths
- reduce direct dependence on `STORAGE_DIR` absolute path access for
  integration-facing workflows

The current support for non-SQLite production databases is a good foundation,
but the hub profile should be explicit: PostgreSQL is required, and media must
be backed by durable storage semantics compatible with central operation and
recovery.

### Export and operations

Exports should remain available, but their role must be reframed.

Required outcomes:

- keep fixture export and import for development, testing, and migration work
- define hub-grade operational backup expectations as database-native backups
  plus media storage backup and versioning
- keep dataset export and media/report streaming as product features, but do not
  position them as disaster recovery or synchronization mechanisms

### Security and access

A hub must tighten both ingest and read access.

Required outcomes:

- remove anonymous upload and upload-status access for hub deployments
- require authenticated, centre-aware authorization for ingest and media access
- keep Nginx offload for heavy media delivery

Nginx offload is aligned with the deployment model for large media, so it should
remain part of the target architecture.

## Important Interfaces And Public Contract Changes

The roadmap requires these public-facing changes:

- the upload API must change from anonymous file drop to authenticated,
  centre-aware ingest
- the upload job model must gain source identity and idempotency metadata
- the centre model must gain a unique immutable key suitable for tenancy and
  routing
- the hub deployment contract must require PostgreSQL and durable shared storage
  semantics
- watcher-based local drop-folder ingestion remains a supported first-class
  ingestion mode for safe system dropoff
- authenticated remote hub ingest is added alongside the watcher path, and both
  must converge on the same ingest core

These are not internal-only refactors. They change the integration contract of
the system and should be communicated as such.

## Implementation Phases

The implementation should proceed in the following order.

### 1. Foundation

Deliver these first:

- unique centre identity
- PostgreSQL-only hub profile
- authenticated ingest model

This phase creates the minimum trust and tenancy foundation required for all
later changes.

### 2. Ingestion hardening

Deliver next:

- idempotent upload contract
- upload job metadata expansion
- preserve watcher-based safe dropoff ingestion alongside the new hub/API ingest
- converge watcher and API ingestion on one shared ingest core

The goal of this phase is to make both ingestion modes safe and attributable:
remote-machine ingestion must become repeatable under retries or duplicate
submissions, while watcher ingestion must keep its current safety properties for
system dropoff.

### 3. Storage abstraction

Deliver next:

- remove assumptions that all consumers can read local files directly
- define durable managed media layout

This phase should separate integration behavior from local path layout so the hub
can evolve toward durable shared storage or object-storage-style access.

### 4. Authorization consistency

Deliver next:

- centre scoping across all relevant APIs

This includes ingest, read, media retrieval, exports, search, and any workflow
that exposes patient-linked or centre-linked data.

### 5. Operations

Deliver last:

- production backup and restore guidance
- monitoring guidance for hub deployment

By this point the target architecture should be stable enough that the
operational guidance can describe the real production profile rather than the
legacy local-development workflow.

## Acceptance Criteria

The work is only complete when all of the following are true:

- a remote machine can upload report or video data with authenticated centre
  identity
- duplicate uploads from multiple machines do not create duplicate logical
  records
- a non-privileged user only sees data for allowed centres
- hub deployment works with PostgreSQL and shared durable media storage
- backup and restore guidance is production-grade and no longer based on
  `dumpdata` as the primary operational story

## Verification Plan

The implementation must be verified with these scenarios:

- authenticated upload succeeds for an allowed centre and records source metadata
- unauthenticated upload is rejected
- the same logical upload from two machines resolves idempotently
- watcher ingestion still safely processes files from system dropoff
- watcher and API ingestion both converge on the same canonical managed-storage
  result
- centre-scoped users cannot read other centres’ reports, videos, or upload jobs
- report and video retrieval still work with Nginx offload enabled
- dataset export still works after tenancy and authentication changes
- PostgreSQL-backed deployment passes core ingest and retrieval flows
- the recovery procedure restores both database state and media references
  correctly

## Backwards Compatibility Guardrails For File Watcher

The migration toward hub-grade ingest must not break the trusted local
filewatcher flow that already exists in this repository.

Required guardrails:

- keep `scripts/file_watcher.py` as a supported entrypoint for local drop-folder
  ingestion
- keep default-centre resolution for watcher ingestion through configured
  application defaults so existing deployments do not need a new watcher-side
  centre selector on day one
- let watcher ingestion keep file-stability checks, path-based polling, and the
  current import-service cleanup semantics
- move shared job creation, source metadata, and provenance recording into the
  shared ingest core so watcher and API ingest diverge only at the boundary
- tighten API centre validation without forcing watcher clients to send new
  fields they do not have today

This means "backwards compatible" does not mean freezing the ingest model in its
current state. It means the watcher remains a first-class supported boundary
while the authenticated API becomes stricter about declared centre identity and
tenant scope.

## Current Implementation Status

The repository has already started this transition:

- `Center` now has a durable `center_key`
- `UploadJob` now records `source_center`, `source_system`, `ingest_mode`,
  `idempotency_key`, and `processing_provenance`
- watcher ingestion already creates upload jobs through
  `endoreg_db.services.hub`
- API uploads and watcher ingestion now conceptually share the same ingest
  metadata model

The next safe steps should continue from that base:

- reject invalid declared API centre identities instead of silently falling back
  to the first centre
- preserve watcher default-centre behavior until watcher-side explicit centre
  routing is intentionally introduced
- extend tests so watcher compatibility remains enforced while hub ingest grows
  stricter

## Assumptions

- This document is an engineering roadmap, not user-facing documentation.
- It intentionally preserves existing export capabilities while reframing which
  ones are product features versus operational tooling.
- It is behavior-focused and implementation-oriented, not a file-by-file dump of
  the repository.
