# Hub ingest operations

Run commands in the installed environment. Start with
[diagnostics](#diagnostics-and-restart), the [recovery matrix](#binding-recovery-matrix),
or the [incident workflow](#incident-workflow).
Readiness evidence belongs to [HubIngest](../feature-tracking/done/HubIngest.yml)
and [ImportMonitoring](../feature-tracking/ImportMonitoring.yml).
For sender implementation, use the [typed transfer contract](hub_transfer_typed_contract.md)
and its [English version](hub_transfer_typed_contract.en.md).
HTTP Live Streaming (HLS) storage rules belong to the
[video runbook](video_storage_normalization.md).

## Continuous folder import

Use four input folders; extensions select formats within each folder.
Scope: [continuous_format_folder_ingestion](../feature-tracking/ImportPipelineRobustness.yml).

| Desktop shortcut | Destination | Processing |
| --- | --- | --- |
| `Report_Input` | Configured report input of the file mover → `report_import` | Anonymize PDF and TXT |
| `Video_Input` | Configured video input of the file mover → `video_import` | MP4, AVI, MOV, MKV, WEBM, and M4V through existing video import |
| `preanonymized_import` | `preanonymized_import` | PDF, TXT, and MP4 with an associated metadata file |
| `sap_import` | `sap_import` | Convert SAP ZIP to TXT reports with metadata files |

- `Report_Input` replaces the shortcut `PDF_Input` without changing its path.
  Existing subdirectories remain readable and are retained.
- Keep preanonymized payloads and their JSON or YAML metadata together. Their
  approval contract differs from raw input, even for the same extension.
- Supply related SAP tables as a ZIP or use `import_sap_ish_txt`; SAP TXT tables
  are not ordinary reports. General CSV, FHIR and individual-image folder
  imports are not implemented.
- Write temporary names, then atomically rename completed inputs. Temporary
  files/directories and symlinks are skipped. Subdirectories are read recursively.

The mover publishes complete report batches atomically in unique subdirectories,
then handles videos. Repeated names cannot overwrite earlier batches; changed
sources remain for a later run. Systemd reacts to folder changes; watcher timers
catch missed, unstable and nested inputs. SAP runs five minutes after completion,
with up to 30 seconds of random delay.

Deploy the backend, LX-Annotate watcher and LuxNix configuration together.
NixOS `tmpfiles` uses mode `0770` and the service group: source directories belong
to `root`, internal inputs to the service user. Handoff can remove an input after
copying to encrypted storage, before anonymization finishes. Check the UploadJob
for completion.

### Native frame rates and interrupted ownership

Directory transcoding accepts valid positive frame rates and preserves source
timestamps. The annotation target is not an intake limit; codec, pixel-format,
integrity and complete timestamp-equivalence checks still apply. The mover does
not quarantine for frame rate, including legacy rejection reports. Separate
pre-annotation resampling requires its coordinate prerequisites.

Atomic source claims protect later arrivals and existing outputs. Files complete
independently; changed sources and failures remain, with bounded retry delays.

The retry dispatcher reconciles bounded batches of expired raw-video owners under
row locks: it advances fencing and records `import_interrupted`, retaining media
and cancellation intent. This proves neither success nor plaintext cleanup.
Broker redelivery, ordinary retry and reimport cannot clear the marker. Inspect
the previous process, attempt files and source before a separately reviewed
recovery action; there is no automatic reset or workspace cleanup.

The mover stop recorded on 2026-10-15 was temporary: reboot or activation may
restart enabled units. Deploy native-rate acceptance, lease migration and mover
changes together before restoring intake. Local tests neither reconcile live
jobs nor attest staging encryption.

## Import Monitoring and State Axes

Import attempt, anonymization, HLS materialization, and cleanup are monitored separately, but share a common commit boundary for video success. A video import is only successful when the canonical master, raw and processed HLS, states, and successful `ProcessingHistory` reference the same validated generation. An HLS or history error before this boundary leaves the attempt failed or retryable, while a previous valid generation remains readable. Report imports do not require HLS, but publish PDF, text, `SensitiveMeta`, state, and success history under the same fencing token.

| Import State | Meaning | Automatic Behavior | Operator Action |
| --- | --- | --- | --- |
| `pending` | Persisted and awaiting processing | Worker dispatch | Check worker and queue if age is unusual |
| `processing` | A worker is processing the import | No parallel processing | Escalate if operational runtime threshold is exceeded |
| `retrying` | Transient dispatch error; source remains protected | Limited exponential retry | Wait until `next_retry_at`; monitor attempt counter |
| `anonymized` | Generation fully published; raw and processed HLS are ready for video | None | Verify generation correlation and cleanup axis |
| `error` | Terminal, stably coded error | No automatic retry | Check error code; correct configuration or plan safe re-import |
| `lost` | Source or ledger is inconsistent | Fail-closed | Secure logs and storage; never manually set to success |
| `cancel_requested` | Operator cancelled while a worker still owns the import | The owner unwinds at its next checkpoint | Wait for `cancelled`; an expired owner becomes an interruption `error` |
| `cancelled` | Cancellation acknowledged; no execution owns the job | None | Terminal; start a new upload to import again |
| Quarantine | Protected source isolated from active import flow | No automatic deletion | Reconcile ledger and document review decision |

Allowed main transitions are `pending -> processing -> anonymized`, `processing -> retrying -> processing`, and `retrying -> error` once attempts are exhausted. `lost` is terminal. A quarantine entry has its own review lifecycle and does not overwrite an upload job status.

Rust `lifecycle_state_v3` owns transitions. Python's
`endoreg_db/services/hub/upload_job_state_machine.py` validates through
`validate_upload_job_status_transition` before persistence, including cancellation:

| UploadJob status | Native operation state |
| --- | --- |
| `pending` | `queued` |
| `processing`, `cancel_requested` | `running` |
| `retrying` | `retry_wait` |
| `anonymized` | `succeeded` |
| `error` | `failed` |
| `lost` | `lost` |
| `cancelled` | `cancelled` |

`cancel_requested` shares `running` with `processing`, so the state machine adds one rule: a cancellation request can only become `cancelled`, an interruption `error`, or `lost`. It can never return to `processing`, `retrying` or `pending`. A native `cancelled` state accepts no further transitions.

Anonymization has a separate native phase: `not_started`, `in_progress`,
`awaiting_validation`, `validated` or `failed`. Filters, statistics, reconciliation,
deletion and transfer use `anonymization_phase` or `anonymization_states_in_phase`.
Here `anonymized` means in progress, not approved for transfer. See
[StateMachineConformance.yml](../feature-tracking/StateMachineConformance.yml)
for the unresolved destructive-cleanup policy.

The retry policy starts at 30 seconds, doubles the delay per attempt, is capped at 15 minutes, and defaults to three attempts. The periodic task `endoreg_db.retry_due_upload_jobs` claims due jobs under a database lock and hands them off idempotently to the pipeline queue.

Stable import error codes:

| Code | Meaning and Next Step |
| --- | --- |
| `dispatch_unavailable` | Transient; await automatic retry, escalate queue or broker if exhausted |
| `duplicate_content` | No re-import; existing validated data remains authoritative |
| `invalid_configuration` | Terminal; correct center, worker, or runtime configuration |
| `invalid_input` | Terminal; correct input contract and safely re-import |
| `media_integrity_failed` | Terminal; check source and quarantine, no unsafe recovery |
| `processing_failed` | Terminal; correlate protected structured logs via upload job ID |
| `source_missing` | Terminal or `lost`; reconcile storage and ledger |

The monitoring API provides exclusively approved operator messages. Absolute paths, hashes, stack traces, raw media, and technical exception texts remain in access-restricted logs. The anonymization overview detects duplicates strictly via `error_code`, never through text searches.

### HLS Materialization

Raw and processed HLS each report `queued`, `materializing`, `ready` or `failed`,
with job correlation, opaque source/target generation, segment count and timestamps.
`ready` requires validated atomic publication of a complete playlist, key and
segments. Failures preserve the previous valid generation and report
`dispatch_failed`, `materialization_failed`, `inconsistent_artifact` or `stale_attempt`.
See [publication and leases](video_storage_normalization.md).

### Diagnostics and Restart

```sh
python manage.py check_system_health --json
python manage.py materialize_video_hls --artifact-kind raw --json
python manage.py materialize_video_hls --artifact-kind processed --json
python manage.py reap_quarantine --older-than-days 30 --dry-run --json

```

System health checks report pending/due retries, exhausted attempts, `ERROR`, `LOST`, failed or hung HLS materializations, quarantine age, and free storage. Before manual retry, verify that no active job or media operation lease conflicts. Quarantine is synchronized and reviewed first; deletion occurs strictly after explicit approval and a separate reap step.

### Binding Recovery Matrix

| Finding | Automatic Action | Prohibited Action | Operator Inspection |
| --- | --- | --- | --- |
| Lease expired, source and ledger unambiguous | Retry with higher fencing token and limited retry | Re-authorize old worker | Correlate owner, token, DB time, and source generation |
| Success history missing | Do not treat attempt as successful; safe retry or reconciliation | Suppress history error or manually set status to success | Compare master/PDF, derivatives, states, and attempt ID |
| Streamable/HLS error | Leave new generation unpublished; preserve previous valid generation | Publish partial playlist or delete old master | Check raw/processed generation, playlist, key, and segment count |
| Database-only or storage-only | Relink only if hash and ownership are unambiguous | Guess based on file name or age | Check hash, storage boundary, center, generation, and history |
| Ambiguous source, unknown ownership, or trust boundary violation | `LOST` or quarantine; retain all sources | Automatic deletion, export, or using weaker code path | Obtain security, storage, and clinical review |
| Pre-anonymized attestation or media profile invalid | Quarantine and terminal integrity error | Accept as normal anonymized success | Verify sidecar, center scope, hash, profile, and timeline |

Reconciliation must not treat local lock files as cluster ownership based on age. A local lock is strictly a performance optimization; authoritative truth resides solely in the attempt/upload job row, database time, lease owner, and fencing token. Unknown artifacts are classified and preserved—never deleted by a generic startup cleanup.

## Scope and Security Phase

The production transfer contract is currently in Phase 1:

* Transport encryption and node authentication occur via mTLS.
* An additional `NetworkNode` shared secret authenticates requests; it is not an encryption key.
* Only anonymized, processed media are transmitted.
* Raw media are neither exported nor accepted by the transfer endpoint.
* The long-lived master key remains within local secret/storage boundaries and must not be configured in lx-annotate or transmitted.

Envelope encryption is the locked Phase 2. As long as no recipient public key, per-transfer DEK, and tested unwrap path are implemented, Phase 1 must not be extended to standalone artifacts distributed outside protected mTLS transfers.

## Role and Ingress Matrix

| Role | Local Watcher | User Upload | Hub Transfer Receiver | Outbound to Hub |
| --- | --- | --- | --- | --- |
| `standalone` | Allowed | Compatible local mode | Disabled (`404`) | Not intended |
| `site_node` | Allowed | Compatible local mode | Disabled (`404`) | Allowed via lx-annotate |
| `central_hub` | Trusted local path | Authenticated & requires `center_key` | Enabled | Not intended |
| `local_study_server` | Approved pre-anonymized imports only | Authenticated & requires `center_key` | Disabled (`404`) | Controlled export path |

API, Watcher, and Transfer utilize separate ingress boundaries, but converge on persisted upload/transfer ledgers, content hashes, center resolution, provenance, and explicit retention states.

The Hub Transfer Receiver is a machine-to-machine endpoint: registration, status, and upload always authenticate the `NetworkNode` and inherit center scope exclusively from its `owning_center`. A Django session is neither required nor an alternative permission; an existing session must not expand or restrict node boundaries.

## Central Hub Configuration

The Central Hub requires at minimum the following settings:

```sh
ENDOREG_DEPLOYMENT_ROLE=central_hub
DJANGO_DEBUG=false
DB_ENGINE=django.db.backends.postgresql
DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO
DJANGO_SECURE_PROXY_SSL_HEADER_VALUE=https
ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=true
ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true
ENDOREG_HUB_TRANSFER_MTLS_META_KEY=HTTP_X_CLIENT_CERT_VERIFIED
ENDOREG_HUB_TRANSFER_MTLS_META_VALUE=SUCCESS

```

The reverse proxy must strip incoming client-set forwarded and mTLS attestation headers. It sets them only after successful TLS/client certificate verification. Under this trust model, the Django process must not be directly accessible from an untrusted network.

Storage, I/O, and quarantine paths must lie within the encrypted local runtime boundary. The application validates path boundaries; host configuration governs encryption, mount order, ownership, and permissions.

## Center and Node Provisioning

1. Create a center with an immutable, machine-readable `center_key`.
2. Create exactly one active local `central_hub` node, and one active `site_node` with `owning_center` per sender.
3. Set a random request secret for each sender via `NetworkNode.set_shared_secret(...)`. Only the password hash is persisted.
4. Store the plaintext secret exclusively in the sender's secret store—never in Git, database, frontend, or logs.
5. Restrict sender `base_url` and receiver targets to `https://` and perform a probe transfer using an anonymized test artifact.

The lx-annotate administration interface displays node, transport, and transfer readiness, but never private keys or shared secrets.

## Certificate Rotation

1. Provision new CA, server, and client certificates with an overlap window.
2. Update CA trust on the hub and senders first.
3. Deploy client certificate and private key as new read-only secret files.
4. Reload services and verify mTLS readiness in administration.
5. Execute a test metadata-plus-processed-media transfer and verify its remote acknowledgment.
6. Only then revoke and remove old certificates.

If certificate material or proxy attestation is missing, transfer must fail-closed with `403`. Shared-secret-only is not an acceptable substitute.

## Transfer Verification and Correlation

Correlate the administration view, sender log `lx_annotate.hub_export.audit`,
hub log `endoreg_db.hub.audit`, and file events `endoreg_db.utils.file_operations`:

- Sender `outbound_job_id`; hub `TransferJob.id` / `remote_transfer_id`.
- Idempotency `transfer_key`, content `resource_hash`, source center/node and target node keys.
- Sender `local_cleanup_status` and hub `cleanup_status`.

Only a matching `transfer_status=applied` acknowledgment completes transfer.
`awaiting_media` is intermediate; `failed`, `inconsistent`, hash mismatch or
conflicting snapshots require incident handling.

Compare the acknowledgment against the immutable sender job: transfer ID/key,
source/target nodes, source center, resource type/hash, processed-media hash,
transfer mode and schema version. Any mismatch is a terminal integrity error,
not an automatic retry. Only a fully matching acknowledgment permits local
completion or cleanup eligibility.

## Cleanup and Quarantine

* Upload sources require explicit cleanup eligibility, including the verified repeated-import rule below; interrupted `deleting` receipts can resume.
* `preserve_source` is respected; `delete_after_success` becomes cleanup-eligible only after verified media integrity.
* Outbound processed media are retained by default. A policy of `eligible_after_verified_apply` merely signals local release after confirmed `applied`; it does not automatically delete the sole copy.
* Hub transfer cleanup remains explicitly traceable as operator intent or `not_requested`.
* Quarantine deletion requires a documented review decision, explicit approval, and a separate reap step.

### UploadJob Source Reaper

Terminal `error` jobs are also considered when a newer `anonymized` job proves
a successful repeat import: the nonempty content hash, known source center,
content type, source system, ingest mode, storage class, and storage tier must
match. The failed job must use `delete_after_success`; `pending`, `eligible`,
and resumable `deleting` cleanup states are supported. The replacement's
`source_file_delete_eligible_at` must be present and due, and any later explicit
due date on the failed job still applies. Both jobs must have no pending retry
or active processing lease. Apply locks both records and rechecks the evidence
before deletion, including the existing target integrity and video playback gates.

The failed job's own persisted upload path and verified additional video-source
copies may be removed, with no other upload or canonical media reference to them.
The error status, error details, and
audit record remain intact. `lost` jobs, unidentified legacy paths, quarantine,
and arbitrary temporary directories are excluded. Missing repeat evidence yields
`successful_replacement_missing`; shared files yield `source_still_referenced`.
The same dry-run and apply commands below cover these sources. Feature scope
and verification are tracked in
[`UploadJobSourceReaperSafety.yml`](../feature-tracking/UploadJobSourceReaperSafety.yml).

The source reaper operates in read-only mode by default. Individual selection uses the UploadJob UUID; batch runs strictly require a positive limit. Output contains only UploadJob ID, decision code, stable block reason, media type, ingest mode, age, and byte count—omitting absolute paths, content, patient data, or content hashes.

For video jobs, additional copies are selected from the persisted `watched_path`,
`watcher_processing_path`, `stored_upload_path`, `legacy_source_path`, and
`migrated_destination_path` provenance fields. Only upload, video input,
preanonymized input, anonymized-video input, and migration-staging directories
under the configured runtime root are eligible. Each existing copy must match
the source content hash and have no foreign references or symbolic links.
Copies are deleted before the primary upload; a partial failure retains the
cleanup receipt and primary source for retry. Quarantine and external archives
are not additional source roots.

The `endoreg_db.cleanup_media_sources` Celery task runs every fifteen minutes on
the maintenance queue, inspecting at most 25 source jobs and 25 videos with pending
generation receipts per invocation. It applies by default; setting
`UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED=false` turns every lane into a dry run. Generation retirement
also removes temporary key, plaintext-source, and output directories of matching
failed or superseded HTTP Live Streaming (HLS) attempts. Current masters,
unmatched attempts, active leases, and unrecorded historical files remain
protected. Scope is recorded in the source-reaper YAML above and
[`VideoStorageNormalization.yml`](../feature-tracking/VideoStorageNormalization.yml).

The same task, under the same gate, also reclaims staging that the ledger
reaper cannot reach. The grace period `ENDOREG_STAGING_CLEANUP_GRACE_SECONDS` defaults to 24 hours and
must be at least 6 hours, the worker hard time limit. Each pass deletes at most
25 entries per run, revalidates each entry immediately before deletion, and
emits a summary event with reclaimable and retained bytes.

- **Orphaned upload directories**
  (`endoreg_db/services/hub/upload_staging_orphans.py`, event
  `upload_staging_orphan_cleanup_summary`): a directory
  `upload_jobs/<tier>/<id prefix>/<upload job id>/` is deleted only if that
  UploadJob no longer exists and no other job's file, provenance path, inventory
  entry (including entries recorded as removed) or video/report field points into
  it. It must also contain no in-progress handoff file or symbolic link, and be
  unchanged for the grace period. Directories whose UploadJob still exists stay
  with the ledger reaper. For example, `preserve_source` jobs are retained by
  policy and reported as `job_exists`. Files outside the job layout are
  reported as `unrecognized_layout` and never deleted.
- **Abandoned temporary attempt artifacts** (`storage/temp`, event
  `transient_artifact_cleanup_summary`): see the "Abandoned temporary
  artifacts" section of
  [`video_storage_normalization.md`](video_storage_normalization.md).

```sh
python manage.py reap_upload_job_sources --upload-job-id <uuid> --json
python manage.py reap_upload_job_sources --limit 25 --json

```

Run the same limited dry run first. The command still deletes only with the explicit `--apply` flag, and refuses it on hosts where `UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED=false`:

```sh
python manage.py reap_upload_job_sources --limit 25 --apply --json
python manage.py reap_upload_job_sources --limit 25 --repeat-until-empty --apply --json

```

Invalid or conflicting selectors, disabled apply mode, or non-positive limits cause non-zero exits. Safely blocked candidates are valid diagnostic results and remain preserved with their blocking code. Applying authorizes deletions under database lock, persisting a receipt containing DB timestamp, fencing token, opaque source identity, and size. Immediately before mutation, the system re-verifies status, retention, due date, retry, processing lease, fencing, target integrity, `ProcessingHistory`, video HLS generations, media operation leases, and storage boundary. Additional source ownership and content-identity evidence is required only for undocumented legacy files. File deletion occurs exclusively via audited file operation wrappers.

### Central file inventory

`UploadJobFile` records sources, sidecars, work files, quarantine and retained
media through `files`, separately from mutable `processing_provenance`. Apply
migration `0091_upload_job_file` before new workers.

`services/hub/upload_job_files.py` serves both cleanup modules. Registered
associations are authoritative: they need no additional provenance or content
rehash. Sidecars and work outputs need not match the source hash. Only undocumented
legacy paths require that evidence. Existing registered sources are adopted
without per-entry existence queries; staging cleanup reads inventory once per job
and only the supplied paths' work directories. Explicit external workspaces are
still scanned for undocumented outputs from interrupted tools.

Execution registers atomic writers, planned temporary files and external-tool
workspaces; removed files remain traceable. Registration cannot override retention,
leases or canonical-media protection. Shared paths, symlinks and paths outside
cleanup boundaries remain protected. See
[UploadJobSourceReaperSafety.yml](../feature-tracking/UploadJobSourceReaperSafety.yml).

Recovery aligns with stable block codes:

* `source_missing_unexpected` without a receipt is retained and investigated as a ledger/file system divergence;
* `delete_failed` remains in `deleting` with its receipt and can be re-run after resolving storage errors;
* If a file is missing while in `deleting`, the receipt idempotently reconciles the interrupted run to `completed`;
* `active_processing_lease`, `retry_allowed`, `active_media_operation_lease`, `fencing_token_changed`, `target_integrity_failed`, `video_hls_not_ready`, and `video_hls_generation_mismatch` prohibit mutation until authoritatively resolved. Fencing and identity deviations require new domain authorization and are not auto-overwritten.

Direct file system deletion outside this reaper is prohibited. The reaper does not transcode upload sources in-place because they may represent the sole recoverable source. Size-bounded canonical videos are created via `normalize_video_storage` with its dedicated clinical quality, timeline, capacity, and destructive migration gate. Only after passing target integrity checks does the upload source become eligible for deletion.

`check_system_health --json` flags cleanup errors, stale `eligible`/`deleting` states, ledger deviations, and unusually large blocked receipts. Local alert thresholds include `ENDOREG_HEALTH_UPLOAD_SOURCE_ELIGIBLE_MAX_AGE_SECONDS` (default 24h), `ENDOREG_HEALTH_UPLOAD_SOURCE_DELETING_MAX_AGE_SECONDS` (default 1h), and `ENDOREG_HEALTH_UPLOAD_SOURCE_LARGE_BLOCKED_BYTES` (default 2 GiB). Positive findings cause non-zero health check exits on `local_study_server` profiles and must be investigated prior to further apply runs.

Example safe quarantine sequence:

```sh
python manage.py reap_quarantine --older-than-days 30 --dry-run --json
python manage.py reap_quarantine --older-than-days 30 --approve-stale --decision-reason "retention period elapsed" --json
python manage.py reap_quarantine --older-than-days 30 --confirm --json

```

On hash errors, missing artifacts, or conflicting provenance, back up quarantine and ledger first. Do not delete files manually or modify database status to success.

## Monitoring, Capacity, and Alerting

Minimum required alerts:

* Hub configuration or mTLS material unavailable,
* `failed`/`inconsistent` transfers and exhausted retries,
* `ERROR`/`LOST` upload jobs and hung processing states,
* Growing or stale quarantine,
* Free storage below operational thresholds,
* Unverified audit ledger integrity,
* Repeated auth, mTLS, hash, or payload rejections.

Administration updates transfer monitoring automatically. For overall import and storage state, execute `python manage.py check_system_health --json`. Outbound hub transfer status is monitored separately:

```sh
python manage.py check_hub_export_health
python manage.py check_hub_export_health --no-fail-on-attention
systemctl status lx-annotate-hub-export-health.service
systemctl status lx-annotate-hub-export-health.timer
journalctl -u lx-annotate-hub-export-health.service

```

The first command outputs compact JSON and exits non-zero if terminal configuration/auth rejections, integrity inconsistencies, exhausted retries, or unclassified errors exist. Transient errors remain visible as non-critical until retry exhaustion. LuxNix site-node configurations run this check periodically via a systemd timer; failed units and structured `hub_export.health_snapshot` events form the local alarm state. `--no-fail-on-attention` is reserved for read-only diagnostics and does not suppress persisted error classes.

The export overview displays persisted classes per transfer in localized operator language alongside active, completed, and attention-requiring transfers. Health JSON and alert events exclude error traces, secrets, raw media, and absolute paths. HTTP `401` and `403` from the hub are terminal authorization rejections and are not retried as transient network issues. Capacity limits must accommodate expected concurrent uploads, temp copies, quarantine, processed derivatives, and backup windows.

For paired hub backups, LuxNix enforces `services.luxnix.lxAnnotateLocal.hub.backup.minimumFreeBytes` (default: 10 GiB reserved on the snapshot file system). The backup service verifies headroom prior to staging and again after database dump and media copy to the unpublished snapshot. If free space falls below headroom, the unit fails, purges only the incomplete pending snapshot, and leaves the existing `latest` restore point intact. Threshold increases require documented capacity planning; reductions require a justified operational decision and must not be performed in response to a full disk.

```sh
systemctl status lx-annotate-hub-backup.service
journalctl -u lx-annotate-hub-backup.service | grep -F minimum_free_bytes
df -B1 /var/lib/lx-annotate/data/hub/backup/snapshots

```

## Backup and Restore

A restore point couples a consistent native PostgreSQL backup with encrypted
media/quarantine, storage names and permissions. Database-only or unmatched
media/ledger backups are insufficient.

LuxNix starts `postgresqlBackup.service` before `lx-annotate-hub-backup.service`.
The unprivileged backup service receives the readable compressed dump as a private
credential, validates gzip, copies it to `database/all.sql.gz` in a pending media
snapshot, and adds checksums and a JSON manifest. Only then does it atomically
update `latest`. Missing, corrupt or failed dumps preserve the previous restore point.

Verify on the central hub:

```sh
sudo systemctl start lx-annotate-hub-backup.service
sudo systemctl status postgresqlBackup.service lx-annotate-hub-backup.service
sudo readlink -f /var/lib/lx-annotate/data/hub/backup/snapshots/latest
sudo jq '.database_dump' /var/lib/lx-annotate/data/hub/backup/manifests/*.json
sudo sh -c 'cd /var/lib/lx-annotate/data/hub/backup/snapshots/latest && sha256sum --check ../../manifests/$(basename "$(readlink -f .)").sha256'

```

Use the manifest and checksums for the exact `latest` timestamp, never mixed
snapshots. `database_dump.relative_path` must be `database/all.sql.gz`, its format
`postgresql-pg_dumpall-sql-gzip`, and its SHA-256 must match.

Restore drill:

1. Resolve `latest` and validate its matching manifest with `sha256sum --check`.
2. In isolation, restore the dump with PostgreSQL tools and the same timestamp's
   snapshot into protected media/quarantine storage.
3. Run migrations and central-hub production checks.
4. Refresh audit integrity and verify system health.
5. Compare `resource_hash`, transfer snapshot, stored medium and acknowledgment.
6. Mark missing/unreadable artifacts `LOST` or inconsistent and preserve logs;
   do not attempt unsafe automatic recovery.

## Incident Workflow

1. Identify affected correlations, time windows, nodes, and centers.
2. Halt transfers or deactivate the sender node if auth or integrity breaches are suspected.
3. Secure structured audit, proxy, and worker logs; do not attach secrets or clinical payloads to tickets.
4. Inspect quarantine and disk usage, but do not delete files manually.
5. Resolve root causes: certificates/proxy, center scope, hash mismatch, snapshot issues, capacity, storage, or worker errors.
6. Retry strictly idempotently using the same `transfer_key`. A new key is permitted only for a fundamentally new business transfer.
7. Document final remote `applied` status, media integrity, and cleanup decisions.

## Verification Checklist

* Central Hub startup fails without PostgreSQL, HTTPS proxy contract, or mTLS.
* Unauthenticated, foreign center, or raw media requests are rejected.
* Repeated registrations and media transfers remain idempotent.
* Tampered media and conflicting metadata result in inconsistent or quarantined states, never success.
* Sender and hub acknowledgments align on transfer, resource, and cleanup identities.
* Administration displays active, succeeded, and failed transfers alongside correlation IDs and cleanup states.
* Quarantine dry runs, explicit approvals, and reaping procedures have been tested.
* Database and media restores have been verified in isolation.

## References

* [`docs/wiki/hub_ingest_current_state.md`](wiki/hub_ingest_current_state.md)
* [`docs/deployment_note_hub_contract.md`](deployment_note_hub_contract.md)
* [`endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`](../endoreg_db/import_files/multi_centre_storage_hub_roadmap.md)
## Completed-stage import checkpoints

Imports persist `import_checkpoint` after preparation: anonymization and storage
normalization for video; redaction, extraction and sensitive metadata for reports.
The receipt identifies a separate immutable encrypted copy, plaintext digest,
source, sensitive metadata and preparation evidence.

Ordinary retry acquires the content/execution fence, validates source and metadata,
authenticates the checkpoint and skips preparation. Reports adopt extraction under
the new fence, retaining its original receipt until publication commits. All
publication, streaming and clinical approval gates still apply.

Failed publication keeps the checkpoint. Successful publication schedules deletion
after transaction commit. Missing bytes, failed authentication or identity mismatch
raise errors, never completion or fallback. Explicit reprocessing invalidates the
checkpoint; imports without one restart preparation. Interrupted preparation also
restarts: this is not frame-level video resumption.

`UploadJob.processing_stack` stores Rust-owned stage order, priority, normalized
source digest and an upload-job UUID checkpoint reference. Python validates
persisted JSON and saves native transitions under execution fences. The canonical
media row links to the encrypted identity receipt; stack completion commits with
fenced upload completion.

Worker startup and periodic maintenance rediscover retained stacks. Video recovery
uses upload leases. Report recovery uses normalized PDF identity (including text
imports), fences expired content owners and commits source binding with its lease.
After the handoff grace period it reuses persisted report jobs and Celery task
references. Live leases remain protected.

The overview exposes stage, completion time, `can_prioritize` and `prioritized`
(Axios converts keys to camelCase). **Priorisieren** posts `{"prioritize": true}`
to the upload-job retry endpoint; **Priorisiert** confirms acknowledgment.
Priority affects the next maintenance dispatch, never preempts running work or
raises retry limits. Automatic recovery needs no browser action.

Cancellation, active ownership/cleanup, missing sources, integrity/lost errors and
exhausted retry budgets block admission; rejected priority requests return HTTP 409.
Worker entry rechecks source bytes and ownership. Checkpoints confer no clinical
approval. Evidence: `import_reconciliation_and_recovery` in
[ImportPipelineRobustness.yml](../feature-tracking/ImportPipelineRobustness.yml).
