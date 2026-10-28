# Hub Ingest Operations Manual

This document is the operations and incident runbook for controlled Site-Node-to-Central-Hub ingestion. Completion status is maintained exclusively in [`feature-tracking/done/HubIngest.yml`](https://www.google.com/search?q=../feature-tracking/done/HubIngest.yml).

For sender implementers and reviewers, [`hub_transfer_typed_contract.md`](https://www.google.com/search?q=hub_transfer_typed_contract.md) describes the currently binding, typed Wire Contract `3.0`, full video and report examples, the mTLS transport type, and how to port older `data-transfer-nginx-mtls` approaches. A complete [`English version`](https://www.google.com/search?q=hub_transfer_typed_contract.en.md) is also available.

Production-critical import monitoring—including transient errors, quarantine, and HTTP Live Streaming (HLS) materialization—is evaluated exclusively in [`feature-tracking/ImportMonitoring.yml`](https://www.google.com/search?q=../feature-tracking/ImportMonitoring.yml). This runbook describes operations, but does not maintain a parallel completion status.

## Continuous folder import

### Native frame rates and interrupted ownership

Directory transcoding accepts any valid positive source frame rate and preserves
source timestamps by default. The annotation frame-rate target is not an intake
limit. Codec, pixel format, integrity and complete timestamp-equivalence checks
still apply; a high or low frame rate alone must not reject usable media. No
automatic frame-rate conversion is introduced.

The LuxNix mover never quarantines media because of its frame rate, including
legacy FPS-rejection reports. Successful files complete independently, with an
atomic source claim protecting later arrivals and preexisting outputs. Changed
sources and failures are retained; retries have a bounded delay. Explicit
pre-annotation resampling remains separate and requires its coordinate
prerequisites; it is not a condition for importing the original media.

The periodic upload retry dispatcher also reconciles a bounded batch of expired
raw-video processing ownership. It rechecks the expiry under a row lock, advances the
fencing token and records `import_interrupted` without deleting media or claiming
success. Broker redelivery cannot reacquire that interruption automatically.
Cancellation intent is retained; an expired owner does not prove that plaintext
cleanup completed, so this case is an explicit error requiring review, not a
successful cancellation. Ordinary retry and reimport do not clear this marker.
Inspect the previous process, attempt artifacts and retained source before a
separately reviewed recovery action; this change does not provide an automatic
reset or assert that abandoned workspaces were cleaned.

For the 2026-10-15 incident, the recorded mover stop was temporary: reboot or
activation can restart enabled units. Ship the backend native-rate acceptance and
lease migration together with the mover changes before restoring intake through
the documented deployment workflow. A local test run does not reconcile the
reported live jobs or attest the encryption of the staging filesystem.

The binding criteria are in
[`ImportPipelineRobustness.yml`](../feature-tracking/ImportPipelineRobustness.yml),
criterion `continuous_format_folder_ingestion`. Four input folders suffice:
reports, videos, preanonymized files, and SAP exports. Extensions determine the
format within an input; separate folders for each extension are unnecessary.
Existing storage paths and the file mover remain unchanged.

The file mover atomically publishes complete report batches under a unique
subdirectory. Repeated folder names therefore do not overwrite earlier batches.
Source files changed since copying are retained for a later run. Reports are
handed off before the potentially long-running video branch.

| Desktop shortcut | Destination | Processing |
| --- | --- | --- |
| `Report_Input` | Configured report input of the file mover → `report_import` | Anonymize PDF and TXT |
| `Video_Input` | Configured video input of the file mover → `video_import` | MP4, AVI, MOV, MKV, WEBM, and M4V through existing video import |
| `preanonymized_import` | `preanonymized_import` | PDF, TXT, and MP4 with an associated metadata file |
| `sap_import` | `sap_import` | Convert SAP ZIP to TXT reports with metadata files |

`Report_Input` replaces the desktop name `PDF_Input` without moving its target
path. Existing subdirectories remain readable and are not deleted. Preanonymized
files remain separate from raw import because the same extensions have a different
metadata and approval contract here. SAP exports require their own converter.
Subdirectories are read recursively. Preanonymized payload files and their JSON
or YAML metadata files must be together in the same directory. TXT tables from
a SAP export are not ordinary TXT reports: provide related tables as a SAP ZIP
or explicitly use the existing `import_sap_ish_txt` command. General CSV, FHIR,
and individual-image folder imports are not implemented here; corresponding
export or table functions do not constitute such import contracts.

Initially write files under temporary names, then atomically rename them to
their final names after writing completes. Temporary files, temporary directories,
and symbolic links are not imported. Systemd responds to input folder changes.
The existing watcher timer picks up missed or not-yet-stable files; SAP has a
separate timer five minutes after the previous run completes, plus up to
30 seconds of randomized delay. Changes deeper inside existing subdirectories
are detected by the next periodic run at the latest.

Deploy the backend, LX-Annotate watcher, and LuxNix configuration together.
NixOS `tmpfiles` creates directories with mode `0770` and the service group;
source directories belong to `root`, internal inputs to the service user.
Handoff to encrypted UploadJob storage can remove the source file before
asynchronous anonymization completes; check completion on the UploadJob.

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
| Quarantine | Protected source isolated from active import flow | No automatic deletion | Reconcile ledger and document review decision |

Allowed main transitions are `pending -> processing -> anonymized`, `processing -> retrying -> processing`, and `retrying -> error` once attempts are exhausted. `lost` is terminal. A quarantine entry has its own review lifecycle and does not overwrite an upload job status.

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

Raw and processed HLS are displayed separately as `queued`, `materializing`, `ready`, or `failed`. Each entry contains the upload job correlation, an opaque source generation, target generation, segment count, and timestamps. `ready` is only permissible following validated, atomic publication of a complete playlist, key, and segment generation. On failure, a stable code is emitted (`dispatch_failed`, `materialization_failed`, `inconsistent_artifact`, or `stale_attempt`); a previous valid generation is restored and preserved. Playback and segment update leases, as well as atomic publication details, are defined in canonical [`video_storage_normalization.md`].

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

The following identities ensure complete traceability:

* `outbound_job_id` on sender,
* `transfer_key` as an idempotent business transfer identity,
* `TransferJob.id` / `remote_transfer_id` on the hub,
* `resource_hash` as content identity,
* `source_center_key`, `source_node_key`, and `target_node_key`,
* `local_cleanup_status` and hub `cleanup_status`.

The lx-annotate admin panel displays local and remote correlations along with cleanup states. Sender logs (`lx_annotate.hub_export.audit`), hub logs (`endoreg_db.hub.audit`), and structured file system events (`endoreg_db.utils.file_operations`) must correlate using these IDs.

A successful transfer completes only upon receiving a hub acknowledgment with `transfer_status=applied`. `awaiting_media` is an intermediate state; `failed`, `inconsistent`, hash mismatches, and conflicting snapshots are incident states that must not be treated as success.

The acknowledgment includes the expected hash of the anonymized processed medium. The sender must validate transfer ID, transfer key, source/target nodes, source center, resource type, resource hash, processed media hash, transfer mode, and payload schema version against its immutable local job. Only a fully matching `applied` status permits completing the local job or marking it eligible for cleanup. Mismatches represent terminal integrity errors and are not retried automatically.

## Cleanup and Quarantine

* Upload sources require explicit cleanup eligibility, including the verified repeated-import rule below; interrupted `deleting` receipts can resume.
* `preserve_source` is respected; `delete_after_success` becomes cleanup-eligible only after verified media integrity.
* Outbound processed media are retained by default. A policy of `eligible_after_verified_apply` merely signals local release after confirmed `applied`; it does not un-controlledly delete the sole copy.
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
generation receipts per invocation. It remains a dry run unless the existing
`UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED` gate is enabled. Generation retirement
also removes temporary key, plaintext-source, and output directories of matching
failed or superseded HTTP Live Streaming (HLS) attempts. Current masters,
unmatched attempts, active leases, and unrecorded historical files remain
protected. Scope is recorded in the source-reaper YAML above and
[`VideoStorageNormalization.yml`](../feature-tracking/VideoStorageNormalization.yml).

```sh
python manage.py reap_upload_job_sources --upload-job-id <uuid> --json
python manage.py reap_upload_job_sources --limit 25 --json

```

Before applying, set `UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED=true` in runtime configuration and run the same limited dry run. The apply flag is explicitly required:

```sh
python manage.py reap_upload_job_sources --limit 25 --apply --json
python manage.py reap_upload_job_sources --limit 25 --repeat-until-empty --apply --json

```

Invalid or conflicting selectors, disabled apply mode, or non-positive limits cause non-zero exits. Safely blocked candidates are valid diagnostic results and remain preserved with their blocking code. Applying authorizes deletions under database lock, persisting a receipt containing DB timestamp, fencing token, opaque source identity, and size. Immediately before mutation, the system re-verifies status, retention, due date, retry, processing lease, fencing, target integrity, `ProcessingHistory`, video HLS generations, media operation leases, and storage boundary. Additional source ownership and content-identity evidence is required only for undocumented legacy files. File deletion occurs exclusively via audited file operation wrappers.

### Central file inventory

`UploadJobFile` associates an UploadJob with its sources, sidecars, work files,
quarantine, and retained media artifacts through `files`. Apply migration
`0091_upload_job_file` before using the new workers. The inventory is separate
from `processing_provenance`; later metadata updates and repeated attempts do
not overwrite earlier file associations.

`services/hub/upload_job_files.py` is the shared access point for
`import_files/file_storage/cleanup.py` and `services/hub/cleanup.py`. Registered
associations are authoritative: their cleanup neither seeks additional provenance
evidence nor hashes file contents again. Only undocumented legacy paths undergo
this evidence check. Known sidecars and work outputs in particular need not have
the same content hash as the original input file.
Already registered sources are adopted without individual existence queries per
inventory entry. Staging cleanup loads only work directories belonging to the
supplied paths and reads the inventory once per UploadJob. Scans of explicitly
registered external work directories remain necessary to find outputs from
interrupted tools that have not yet been documented individually.

Import execution registers atomic file writers, planned temporary files, and
explicit work directories of external anonymizers. The latter constrain byproduct
association to their own workspace even after an interrupted attempt. Removed
files remain traceable in the inventory. Registration alone overrides neither
retention policy, active leases, nor retained canonical media protection. Shared
paths, symbolic links, and paths outside the permitted cleanup boundary are not
removed.

Requirements and verification evidence:
[`UploadJobSourceReaperSafety.yml`](../feature-tracking/UploadJobSourceReaperSafety.yml).

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

A Hub backup always comprises two coupled components:

1. Consistent, native PostgreSQL database backup,
2. Backup of the encrypted, protected media/quarantine boundary including storage names and permissions.

LuxNix publishes these as a single restore point. Invoking `lx-annotate-hub-backup.service` (manually or via timer) first executes `postgresqlBackup.service`. Upon successful creation of a readable compressed PostgreSQL dump, systemd passes it as a private credential to the unprivileged hub backup service. The service validates gzip formatting, copies the dump as `database/all.sql.gz` into the pending media snapshot, and registers it in the checksum list and JSON manifest. Only then does the atomic `latest` symlink update to the new restore point. Missing, corrupt, or failed database dumps leave the existing `latest` restore point unchanged.

Operator verification on the Central Hub:

```sh
sudo systemctl start lx-annotate-hub-backup.service
sudo systemctl status postgresqlBackup.service lx-annotate-hub-backup.service
sudo readlink -f /var/lib/lx-annotate/data/hub/backup/snapshots/latest
sudo jq '.database_dump' /var/lib/lx-annotate/data/hub/backup/manifests/*.json
sudo sh -c 'cd /var/lib/lx-annotate/data/hub/backup/snapshots/latest && sha256sum --check ../../manifests/$(basename "$(readlink -f .)").sha256'

```

The selected manifest entry must specify `database/all.sql.gz` for `database_dump.relative_path`, `postgresql-pg_dumpall-sql-gzip` for `format`, and a matching SHA-256 hash. Manifests and checksum files belong strictly to the timestamp referenced by `latest`—do not combine multiple timestamps.

Restore Dry-Run Procedure:

1. Select the target path from `latest`, along with its matching JSON manifest and checksum file, validating completely via `sha256sum --check` prior to restore.
2. In an isolated environment, restore `database/all.sql.gz` using PostgreSQL utilities, then extract the snapshot into the protected media/quarantine boundary for that exact timestamp.
3. Run migrations and execute Central Hub production checks.
4. Refresh audit ledger integrity and verify system health.
5. Cross-verify `resource_hash`, transfer snapshot, stored medium, and acknowledgment.
6. Treat missing or unreadable artifacts as `LOST`/inconsistent and preserve logs; avoid unsafe auto-recovery.

A database without corresponding media, or media lacking a consistent ledger, does not constitute a successful restore.

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

* [`docs/wiki/hub_ingest_current_state.md`](https://www.google.com/search?q=wiki/hub_ingest_current_state.md)
* [`docs/deployment_note_hub_contract.md`](https://www.google.com/search?q=deployment_note_hub_contract.md)
* [`endoreg_db/import_files/multi_centre_storage_hub_roadmap.md`](https://www.google.com/search?q=../endoreg_db/import_files/multi_centre_storage_hub_roadmap.md)
## Completed-stage import checkpoints

Video and report imports persist `import_checkpoint` in the owning media's
validated metadata after artifact preparation. Video preparation includes
anonymization and storage normalization; report preparation includes redaction,
extraction evidence and sensitive metadata. The receipt references a separate
immutable copy written through encrypted storage, its plaintext content digest,
source identity, sensitive metadata identity and preparation evidence.

An ordinary retry acquires the existing content/execution fence, validates the
receipt against the current source and metadata, authenticates and materializes
the checkpoint only for downstream publication, and skips preparation. Report
extraction is explicitly adopted under the new fence while the original
extraction receipt remains in the checkpoint until publication commits. Every
normal publication, streaming and clinical approval gate still applies.

After successful publication, receipt retirement schedules storage deletion only
after the database transaction commits. Failed publication retains the encrypted
checkpoint. A missing artifact, authentication failure or identity mismatch
raises an error; it never implies completion or falls back to unverified bytes.
Explicit reprocessing invalidates the old checkpoint before preparing again.
Imports created before this change have no intermediate checkpoint and restart
preparation through the existing path. Interrupted work inside preparation is
also restarted; this is not frame-level video resumption.

`UploadJob.processing_stack` stores the versioned Rust-owned remaining stages,
priority, normalized source digest and a fixed checkpoint reference equal to the
upload-job UUID. Rust alone defines and validates stage order. Python validates
persisted JSON at the model boundary and persists native transitions under the
existing execution fences. The checkpoint references the canonical media row;
its encrypted receipt remains the authority for artifact identity. Completion
advances the stack in the fenced upload completion transaction.

Celery worker startup requests the existing maintenance retry task; periodic
reconciliation also rediscovers retained stacks after broker or worker loss.
Video recovery uses the upload lease. Report recovery uses the persisted
normalized PDF identity, including text imports, and fences expired content
owners. The normalized source binding and report lease commit together. Lost
report handoffs are retried after the existing handoff grace period and reuse
the persisted report job and Celery task reference. Live leases are preserved.

The overview exposes stage, completion timestamp, `can_prioritize` and
`prioritized`; Axios converts these keys to camelCase. Operators use
**Priorisieren**, which posts `{"prioritize": true}` to the existing upload-job
retry endpoint and prefers the job during the next maintenance dispatch. The
button becomes disabled **Priorisiert** after acknowledgement. Running imports
are not preempted. Automatic recovery requires no browser action.

Admission rejects cancellation, active ownership, cleanup, missing sources,
integrity/lost errors and exhausted retry budgets. Priority and automatic
interruption recovery use the same fenced admission and never raise the
configured retry limit; rejected priority requests return HTTP 409. Worker
entry revalidates source bytes and ownership. No clinical approval is inferred
from a checkpoint. Readiness belongs to `import_reconciliation_and_recovery`
in `feature-tracking/ImportPipelineRobustness.yml`.
