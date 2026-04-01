# Hub Ingest Current State

## Purpose

This page documents what `hub_ingest` supports in the repository today.

It is intentionally a current-state operator and engineering note, not a target
architecture document. For planned hub-grade multi-centre work, see
`endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`.

For the concrete gap-closure design, including row-plus-media transfer and
processing replay rules, see `docs/wiki/hub_ingest_gap_closure.md`.

## Short Answer

`hub_ingest` is usable today as an ingest boundary for:

- API uploads that create `UploadJob` records and then hand off to the existing
  report and video import services
- watcher-based local drop-folder ingestion that also creates `UploadJob`
  records before processing

`hub_ingest` is not yet a full multi-node federation or replication layer.

## What Exists Today

### Shared ingest metadata model

The current transition work is real and already in code:

- `Center` has a durable `center_key`
- `UploadJob` stores `source_center`, `source_system`, `ingest_mode`,
  `idempotency_key`, `original_filename`, and `processing_provenance`
- watcher ingestion and API ingestion both flow through
  `endoreg_db.services.hub`

Relevant files:

- `endoreg_db/models/administration/center/center.py`
- `endoreg_db/models/hub/upload_job.py`
- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/migrations/0011_hub_ingest_metadata.py`

### API ingestion

The upload API currently accepts multipart uploads and creates an `UploadJob`.

Current behavior:

- entrypoint: `POST /api/upload/`
- status polling: `GET /api/upload/<uuid>/status/`
- center identity may be declared through `center_key` or `center_name`
- source identity may be declared through `source_system`
- idempotency may be provided through `Idempotency-Key` or
  `idempotency_key`
- when Celery is available, job execution is queued through
  `endoreg_db.process_upload_job`
- when Celery is not available, the same upload job is processed inline through
  `endoreg_db.services.hub.process_upload_job(...)`

Relevant files:

- `endoreg_db/views/misc/upload_views.py`
- `endoreg_db/serializers/hub/upload_job.py`
- `endoreg_db/tasks.py`

### Watcher ingestion

The watcher path remains supported for trusted local drop-folder workflows.

Current behavior:

- watcher calls `process_watcher_file(...)`
- files are deduplicated into `UploadJob` records using a watcher-built
  idempotency key derived from content hash, mtime, and size
- watcher processing then calls the existing report or video import service
- watcher keeps default-centre behavior through application settings

Relevant files:

- `endoreg_db/services/hub/ingest.py`
- `endoreg_db/management/commands/start_filewatcher.py`
- `scripts/file_watcher.py`
- `tests/import_files/test_file_watcher.py`

## How Usable It Is Right Now

### Usable now

`hub_ingest` is usable today for:

- centralizing upload-job creation and source metadata recording
- API-based ingest into one deployment
- watcher-based ingest on a trusted local system
- sharing the same import core after job creation

### Not yet complete

`hub_ingest` is not yet sufficient as an authoritative study-network hub.

Gaps that still matter operationally:

- no explicit node-to-node replication protocol
- no explicit remote site registration or trust model
- no central-hub identity model in application data
- no documented operator contract for remote machine ingestion
- no explicit storage-tiering or retention model for synced data
- no strong statement that hub mode requires PostgreSQL plus durable shared
  storage in deployment docs

The roadmap states this directly:

- `endoreg_db` is suitable as a centralized study application server
- `endoreg_db` is not yet suitable as the authoritative multi-centre data hub

Source:

- `endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`

## Is It Sufficiently Documented

No.

What exists:

- a roadmap and architecture-gap document:
  `endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`
- a local import and watcher workflow document:
  `endoreg_db/import_files/import_service.md`

What is still missing:

- a deployment guide for a central ingest node
- a concrete remote-site ingest contract
- an authentication and authorization guide for hub ingest
- a retention and storage-policy guide
- an explicit explanation of what another `endoreg-db` instance can and cannot
  do when talking to this one

This page fills part of that gap, but it does not replace a full hub operator
guide.

## Can One `endoreg-db` Talk To Others

### Today

Yes, but only in a limited sense.

One `endoreg-db` deployment can act as an external client and upload files to
another deployment through the upload API in `endoreg_db/views/misc/upload_views.py`.

That means one instance can submit data to another.

### What that does not mean

This is not currently a first-class federation model.

There is no built-in support for:

- node discovery
- site registration
- push and pull synchronization
- cross-node reconciliation
- shared job ledgers between deployments
- conflict resolution between two authoritative peers

So the correct description is:

- instance-to-instance submission is possible
- instance-to-instance federation is not yet implemented

## Storage Efficiency And Synced Data Retention

### Current behavior

The current ingest flow is persistence-oriented, not sync-and-evict oriented.

For API ingest:

- uploaded files are stored through `UploadJob.file`
- processing is then handed off either through Celery or inline execution
- `process_upload_job(...)` calls the report or video import service with
  `delete_source=False`

For watcher ingest:

- the watched source file may be deleted after successful processing
- this is watcher/drop-folder cleanup, not central managed-storage eviction

Relevant file:

- `endoreg_db/services/hub/ingest.py`

### What is not implemented

There is currently no central policy that says:

- once data is synchronized, delete the central managed copy
- keep only derived metadata centrally
- retain anonymized assets but evict raw ingest artifacts after confirmation
- tier old media out of central storage automatically

So the answer is no: current hub ingest is not storage-efficient in the sense
of automatically removing synced data from the central node after replication or
downstream confirmation.

## How The Central Node Is Identified

### Current reality

The application does not model a distinct `hub node` or `study network node`
entity.

What exists instead:

- `Center.center_key` identifies the submitting centre or tenant
- `ApplicationSettings.center` provides a default centre for local workflows,
  especially watcher ingest

Relevant files:

- `endoreg_db/models/administration/center/center.py`
- `endoreg_db/models/administration/app_settings.py`

### Operational meaning

Today, the central node is identified operationally, not by application data.

In practice that means:

- the central node is the deployment whose base URL remote clients send uploads
  to
- the deployment operator decides which node is the central ingest authority
- the repository does not currently store or advertise a first-class hub-node
  identity

### Consequence

`center_key` is a site identity key, not a hub-node identity key.

If a study network needs explicit topology in the product itself, the data model
still needs a separate concept for:

- local site node
- central hub node
- trust and routing between nodes

## Minimum Safe Operator Interpretation

If you deploy this today, the safe interpretation is:

- use it as one central ingest-capable deployment
- treat remote sites as external upload clients, not as peer nodes
- use `center_key` to identify the submitting centre
- do not assume built-in replication, federation, or central storage eviction
- keep watcher ingest for trusted local drop-folder workflows only

## Recommended Next Documentation

The next documents that should exist for hub-grade rollout are:

- central hub deployment guide
- authenticated remote ingest contract
- storage and retention policy
- tenancy and centre-scoping guide
- study-network topology guide describing hub node versus centre identity
