# Application Storage and Cleanup

This document describes how `endoreg-db` stores and removes data in the current
codebase. It is the general current-state reference for application storage. The
[video storage normalization runbook](video_storage_normalization.md) remains the
specialized contract for video generations, timelines, transcoding, and HTTP Live
Streaming (HLS). Feature scope and production-readiness evidence remain in
[`feature-tracking/`](../feature-tracking/README.md), especially
[`StorageSecurity.yml`](../feature-tracking/StorageSecurity.yml),
[`UploadJobSourceReaperSafety.yml`](../feature-tracking/UploadJobSourceReaperSafety.yml),
and [`VideoStorageNormalization.yml`](../feature-tracking/VideoStorageNormalization.yml).


## Storage model in one view

The application splits state between two stores:

1. The relational database owns identity, relationships, workflow state,
   content hashes, storage names, leases, fencing tokens, cleanup receipts, and
   audit evidence.
2. The runtime filesystem owns the byte payloads: upload sources, canonical
   reports and videos, HLS derivatives, extracted frames, temporary processing
   files, exports, quarantine files, model weights, and local manifests.

A database row is therefore not the file, and a file is not sufficient evidence
of ownership. Destructive workflows normally require both a database ownership
record and a verified file identity. The database also outlives many files:
successful source cleanup clears the `UploadJob.file` reference but preserves the
upload job and its cleanup receipt, while deleting a media record marks related
active upload jobs `LOST` instead of deleting their provenance.

All application-owned paths derive from `LX_RUNTIME_ROOT` through
`endoreg_db.utils.paths.get_runtime_paths()`. Django's `MEDIA_ROOT` and
`PROTECTED_MEDIA_ROOT` both resolve to `<runtime_root>/storage`. A stored
`FileField` name is relative to that directory. Application code must not invent
an independent media root.

```text
<LX_RUNTIME_ROOT>/
├── storage/                         protected media boundary and MEDIA_ROOT
│   ├── upload_jobs/
│   │   ├── api/                     API upload sources
│   │   ├── watcher/                 watcher-owned upload sources
│   │   └── preanonymized/           pre-anonymized upload sources
│   ├── sensitive_videos/            canonical raw video FileFields
│   ├── processed_videos_final/      canonical anonymized video FileFields
│   ├── sensitive_reports/           canonical raw report FileFields
│   ├── processed_reports_final/     canonical anonymized report FileFields
│   ├── streamable_videos/
│   │   ├── raw/                     legacy MP4 and current raw HLS derivatives
│   │   └── processed/               legacy MP4 and current processed HLS derivatives
│   ├── frames/                      extracted frame cache by raw video hash
│   ├── raw_frames/                  raw frame workspace
│   ├── temp/                        protected plaintext/transcode staging
│   ├── documents/                   generated or attached report documents
│   ├── model_weights/               persisted model weights
│   ├── sensitive_sidecars/          managed sensitive sidecars
│   ├── locks/                       local lock files
│   └── test/ and lx_anonymizer_eval/ specialized local data
├── import/                          watched drop folders and SAP import folders
├── export/                          explicit operator/user export destinations
├── quarantine/                      isolated files awaiting a decision
├── migration_staging/
│   └── manifests/                   cleanup cursor and migration manifests
├── terminology/                     shared terminology data
└── logs/                            local application logs when file logging is used
```

The complete typed topology, including compatibility aliases, is defined in
`endoreg_db/utils/paths.py`. Calling `ensure_directories()` creates the known
directories; defining a path does not by itself create a retention or cleanup
policy for it.

## What is stored and how it is protected

| Artifact | Filesystem owner | Database owner | Current protection and role |
| --- | --- | --- | --- |
| Upload source | `storage/upload_jobs/<tier>/...` | `UploadJob` and, for current imports, `UploadJobFile` inventory rows | An ingest copy retained according to `retention_policy`; it is not the canonical processed result. It uses the protected filesystem boundary and the default Django storage rather than `LazyEncryptedStorage`. Production therefore depends on the deployment's encrypted filesystem for these bytes. |
| Canonical video | `sensitive_videos/` and `processed_videos_final/` | `VideoFile.raw_file` and `VideoFile.processed_file` | Both fields currently use `LazyEncryptedStorage`, which writes chunked application-layer ciphertext atomically. Raw and processed masters have separate identities and lifecycle rules. |
| Canonical imported report | `sensitive_reports/` and `processed_reports_final/` | `RawPdfFile.file` and `RawPdfFile.processed_file` | Both fields currently use `LazyEncryptedStorage`. Processed reports are immutable generation-named PDF files; uncertain or referenced prior generations are retained for reconciliation. |
| Other report document | `storage/documents/` | concrete models derived from `AbstractDocument` | Uses the default Django storage under the protected filesystem boundary. It is not automatically covered by the raw-report validation cleanup path. |
| HLS derivative | `streamable_videos/<raw|processed>/hls/<video_uuid>/<key_id>/v0/` | `VideoHlsArtifact` | Reproducible derivative, not a canonical master. Segments are HLS-encrypted; the content key is wrapped and stored in the database. Publication moves a complete validated directory into place atomically. |
| Legacy streamable MP4 | `streamable_videos/raw/` or `processed/` | relative-path fields on `VideoFile` | Compatibility derivative inside the protected boundary. Current HLS is preferred; reconciliation may rehome or remove a proven legacy copy. |
| Extracted frames | `storage/frames/<raw_video_hash>/` | `Frame` rows and `VideoState` flags | Plain JPEG cache inside the encrypted filesystem boundary. Persisted presentation timestamps remain authoritative even if the image cache is removed. Dataset-backed files receive stronger retention. |
| Temporary working data | mostly `storage/temp/` | attempt, artifact, or inventory records where the workflow needs durable ownership | May contain complete plaintext materializations for FFmpeg, PDF processing, model loading, or export. Files are private, operation-owned, and normally removed on context exit or after commit. Filesystem encryption is mandatory because process death can leave a temporary file behind. |
| Quarantine | `<runtime_root>/quarantine/` | `QuarantineItem` and sometimes `UploadJobFile` | Fail-closed holding area. Age alone does not delete a file; deletion requires an explicit approval state and then a reaper apply action. |
| Exports | `<runtime_root>/export/` or a request-owned response | export-specific workflow | Explicit outputs are not canonical storage. Cleanup is owned by the exporting workflow; the general periodic media cleanup does not sweep all export directories. |
| Model weights | `storage/model_weights/` | `ModelMeta.weights` | Uses `LazyEncryptedStorage`. Training staging is removed when a run finishes, but persisted weights follow their model record and operator workflow. |
| Manifests and cursors | `migration_staging/manifests/` | typed JSON schemas rather than media rows | Private, atomically written operational state. The periodic cleanup cursor and advisory lock live here. |

The typed `ENDOREG_STORAGE_PROFILE` policy distinguishes application-encrypted
payloads from filesystem-streamable video payloads. The default
`hybrid_default` policy classifies raw video and non-video payloads as
application encrypted and processed video as filesystem streamable. The actual
canonical `VideoFile` and `RawPdfFile` fields still use `LazyEncryptedStorage`;
streamable video delivery is provided by explicit derivatives and range-aware
readers. Treat the profile as routing policy, not as evidence that every file
beneath the runtime root has an application encryption header.

`LazyEncryptedStorage` requires the configured master key at the first real
read or write, encrypts to a temporary sibling, and atomically publishes the
ciphertext. It supports decrypted byte ranges without creating a complete
plaintext copy. Path-based tools instead use `ensure_local_file()`, which creates
a private temporary plaintext file under `storage/temp/` and deletes only the
copy it created when the context exits.

Deletion through `safe_unlink_file()` removes a directory entry. It is not
physical erasure from solid-state media, copy-on-write storage, snapshots, or
backups. The backing filesystem and backups must remain encrypted, and backup
retention is a deployment concern described in [database backup and
recovery](backup_recovery.md).

## Import and publication lifecycle

### Upload intake

API, watcher, and pre-anonymized intake create an `UploadJob` source beneath
the matching `storage/upload_jobs/` tier. New jobs default to
`retention_policy=preserve_source`, so successful import does **not** imply
automatic source deletion. A workflow must explicitly choose
`delete_after_success` and set or reach `source_file_delete_eligible_at` before
the source reaper can act. `migration_managed` is also retained by the ordinary
reaper.

Current imports inventory owned files in `UploadJobFile` rows. Older provenance
fields are still read for compatibility. Duplicate-import and post-publication
staging cleanup accepts only registered or narrowly recognized files beneath
approved roots and rejects symbolic links, non-files, and paths outside those
roots.

### Reports

Report import hashes the source before creating or reusing the canonical
`RawPdfFile`. Raw and processed PDFs are separate encrypted FileFields. The
processed PDF, its digest, processing state, and success history are committed
together. Temporary render and parsing files are removed after the surrounding
operation or after database commit.

Report metadata validation defaults to `delete_original_raw=True`; it schedules
raw PDF deletion only after the validation transaction commits. A caller can
explicitly retain the raw PDF by passing false, so this is a workflow decision
rather than an age-based retention period. Deleting the whole `RawPdfFile`
explicitly also removes its owned raw and processed files and recorded upload
sources, but fails if another row or storage placement still references the
content.

### Videos

The canonical raw and processed FileFields are the masters. HLS, legacy
streamable MP4 files, and extracted frames are derivatives or caches. A video
import publishes validated canonical files and matching raw and processed HLS
before it is considered successful. Replacement of a processed master records a
typed cleanup receipt for the old generation before the new generation commits.

Once anonymization is validated and processed HLS is ready, the workflow calls
raw-video cleanup. It deletes raw HLS, the canonical raw FileField, and any raw
legacy streamable copy only when `raw_cleanup_blockers()` is empty and no media
operation lease owns the video. Missing normalization, source-timeline, clinical
quality, or ready processed-HLS evidence therefore retains the raw source.
Detailed gates are in the
[video storage normalization runbook](video_storage_normalization.md).

### Reads and streaming

Protected media URLs do not expose arbitrary runtime paths. Normal application
reads go through Django storage or bounded protected-path resolvers. Encrypted
FileFields support direct decrypted byte ranges. HLS playlist, key, and segment
access is authorization checked and renews the video media-operation lease.
Deployments can offload approved protected responses to Nginx, but the
authorization and storage identity are still application decisions.

## Cleanup that runs now

Cleanup is not one garbage collector. Each mechanism has a distinct owner and
authorization rule.

### Operation-scoped cleanup

Context managers and `finally` blocks remove files they created, including
decrypted materializations, report-rendering frames, HLS key files, HLS plaintext
sources, transcode candidates, export scratch directories, and model-training
staging. A failed delete is generally surfaced or logged by the owning workflow;
the code does not treat an unknown old file as automatically disposable.

Published report and video staging is normally removed after the database commit
so cleanup failure cannot roll back a valid publication. Durable inventory or
generation receipts allow later reconciliation where implemented.

### Periodic media cleanup

Django settings register `endoreg_db.cleanup_media_sources` with Celery Beat
every 900 seconds. The task uses a non-blocking advisory lock, processes bounded
batches of 25, and persists a typed video cursor in
`migration_staging/manifests/periodic_media_cleanup.json`. This schedule only
has an effect where Celery Beat and a maintenance worker are actually running.

The task always reads `UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED`:

- When false (the default), it inventories candidates and blockers without
  deleting upload sources, old video masters, or superseded HLS.
- When true, it applies all three cleanup lanes after each lane revalidates its
  own ownership and integrity conditions.

The lanes are:

1. **Upload source reaper.** Selects eligible/deleting upload jobs and certain
   failed jobs with a later successful replacement. It requires
   `delete_after_success`, due time, no retry or active import lease, no shared
   reference, successful target integrity, and for videos matching ready raw and
   processed HLS. It snapshots name, size, content hash, and fencing token under
   a row lock, records a stable deletion receipt, rechecks everything, deletes
   registered source copies, clears the FileField, and marks cleanup completed.
2. **Superseded HLS cleanup.** Deletes only `superseded` or `failed` artifacts
   whose current canonical source and ready replacement derivative match. It
   removes the published HLS directory and attempt-owned temporary key, output,
   and plaintext-source directories before deleting the artifact row.
3. **Processed generation cleanup.** Acts only on journaled, committed cleanup
   receipts. It requires the replacement name and hash to match the current
   `VideoFile`, valid normalization evidence, no reference to the old content,
   no active media lease, and matching ready processed HLS. The receipt remains
   pending after a failure so the operation can be retried.

The upload reaper is also available as `manage.py reap_upload_job_sources`. It
is a dry run unless `--apply` is passed, and apply is rejected unless the same
environment gate is enabled. `manage.py reap_processed_video_generations`
likewise defaults to a dry run for one video.

There is a conservative interaction between the first lane and raw-video
cleanup: the upload-source reaper currently requires both matching raw and
processed ready HLS, while validated raw cleanup removes the raw HLS artifact.
If raw cleanup has already run, that upload source is blocked with
`video_hls_not_ready`; the reaper does not infer that processed HLS can replace
the missing raw derivative.

### Explicit media deletion

Calling `VideoFile.delete()` uses the owned-file deletion service. It obtains
database ownership, validates recorded upload sources and references, deletes
frames, both HLS kinds, canonical raw and processed files, and legacy streamable
copies, then deletes the database record. Active/retryable imports, shared
sources, storage placements, symbolic links, content changes, or active media
operations block the operation.

Calling `RawPdfFile.delete()` similarly locks the record, rejects shared files
or storage placements, removes recorded upload sources and both report
FileFields, and then deletes the database row.

The media-management API can preview or force removal of unfinished, failed, or
stale media. Unfinished and failed videos are deleted one object at a time and
therefore reach `VideoFile.delete()`. The stale-media branch currently calls
`QuerySet.delete()` instead. Django emits delete signals, so related upload jobs
are marked `LOST`, but the model's owned-file deletion override is bypassed and
canonical or derived files are not guaranteed to be removed. This is a current
cleanup gap, not an approved way to reclaim storage.

### Frame cleanup

`VideoFile.delete_frames()` separates database coordinates from cached images.
It resets extraction state, marks ordinary `Frame` rows as not extracted, and
after commit atomically renames then removes frame directories. Files attached
to an artificial-intelligence dataset are preserved. A separate bounded
retention service prunes unannotated, non-dataset frames that fall inside
validated `outside` segments while keeping the `Frame` rows and timestamps.

### Quarantine cleanup

Quarantine is intentionally not part of periodic media cleanup. Inventory sync
creates or updates `QuarantineItem` rows. An operator can mark a pending item for
deletion with a reason, or retain it. `reap_quarantine` and the corresponding
media API delete only approved items after path, type, and identity validation;
their default behavior is a dry run. Health checks report quarantine age but do
not silently delete old items.

### Reconciliation and remote placement

Startup reconciliation can relink an unvalidated video's missing raw pointer
when exactly one content-verified candidate exists. It removes only narrowly
recognized stale unpublished streamable-cache files. It retains apparent
orphaned canonical or temporary artifacts when no durable attempt identity
proves ownership, and it does not remove lock files by age.

`reconcile_media_integrity` compares database and filesystem state, performs
only explicit safe repairs, and marks unrecoverable records `LOST`. Optional
stale-artifact cleanup uses the same conservative reconciliation rules.

Hub storage placement is a separate control plane. `StorageArtifactPlacement`,
transfer evidence, reservations, and rotation receipts describe which storage
node owns a protected artifact; they do not turn the local runtime tree into a
shared filesystem. Rotation follows copy, verify, atomic placement commit, then
deferred source cleanup. Endoreg-db records deletion evidence only after the data
plane reports the exact authorized deletion. Storage reconciliation records
discrepancies and expires safe reservations, but explicitly does not guess a
canonical copy, retry a transfer, or delete remote bytes.

## What is not automatically cleaned

The current general cleanup task does not provide an age-based sweep of the
entire runtime root. In particular:

- upload sources with the default `preserve_source` policy remain;
- quarantine remains until review and explicit approval;
- unknown or unowned files are reported or retained, not guessed away;
- canonical report and video files are not deleted merely because they are old;
- export directories, logs, terminology, model weights, test data, and
  evaluation data have no single periodic retention policy in the media task;
- database rows and audit/cleanup receipts are retained after many file cleanup
  operations;
- backups and snapshots are outside application cleanup;
- a configured Celery schedule does nothing unless the deployment runs the
  scheduler and maintenance worker; and
- the apply gate defaults to false, so deployment configuration determines
  whether the periodic task only reports or actually deletes eligible files.

This conservative behavior is deliberate. A full disk can therefore require
operator diagnosis rather than broad age-based deletion.

## Operator inspection map

Use dry-run or read-only commands first:

| Purpose | Command or interface |
| --- | --- |
| Validate configured runtime paths, keys, and storage contract | `manage.py validate_runtime_storage_contract` |
| Check overall operational state, including cleanup backlog and quarantine age | `manage.py check_system_health` |
| Inspect upload-source cleanup candidates | `manage.py reap_upload_job_sources --json` |
| Inspect one video's old-generation cleanup receipt | `manage.py reap_processed_video_generations --video-id <id>` |
| Reconcile media database/file integrity | `manage.py reconcile_media_integrity --dry-run --json` |
| Inventory quarantine | `manage.py reap_quarantine --json` |
| Inspect legacy/canonical media path migration | `manage.py migrate_media_storage --json` |
| Check video storage and HLS readiness | `manage.py check_production_hls_readiness` |

Do not delete files directly from the runtime tree to resolve a database/file
mismatch. Use the owning service or command so references, hashes, leases,
receipts, and database state remain consistent.

## Implementation map

The principal current call sites are:

- topology and path validation: `endoreg_db/utils/paths.py`;
- storage profile policy: `endoreg_db/utils/storage_profile.py` and
  `rust/endoreg_rust_backend/src/storage_profile.rs`;
- encrypted FileField backend: `endoreg_db/utils/encryption/encrypted.py`;
- atomic filesystem wrappers currently used by the repository:
  `endoreg_db/utils/file_operations.py`;
- upload source lifecycle: `endoreg_db/models/hub/upload_job.py`,
  `endoreg_db/services/hub/upload_job_files.py`, and
  `endoreg_db/services/hub/cleanup.py`;
- report file lifecycle: `endoreg_db/services/raw_pdf_files/io.py` and
  `endoreg_db/services/raw_pdf_files/validation.py`;
- canonical video and derivative cleanup:
  `endoreg_db/services/video_storage/generation_cleanup.py`,
  `endoreg_db/services/video_files/io.py`, and
  `endoreg_db/services/streaming/hls_media.py`;
- frame retention: `endoreg_db/services/video_files/_frames/_delete_frames.py`
  and `endoreg_db/services/frames/frame_retention.py`;
- quarantine: `endoreg_db/services/hub/quarantine.py`;
- periodic scheduling: `endoreg_db/config/settings/base.py` and
  `endoreg_db/tasks.py`;
- local recovery: `endoreg_db/services/runtime/reconciliation.py` and
  `endoreg_db/services/media/integrity.py`; and
- remote placement and rotation: `endoreg_db/services/hub/storage_placement.py`,
  `storage_rotation.py`, `storage_transfer.py`, and
  `storage_reconciliation.py` in the same package.
