# Video Storage Normalization

This document is the operational and architecture runbook for the
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml)
feature. The feature definition in YAML Ain't Markup Language (YAML) format is
the only authoritative source for implementation and approval status.

## Import reservation and delivery recovery

Queued imports reserve a database lease with owner `queued-task:<Celery task ID>`.
The first delivery atomically claims that reservation as `execution-<UUID>`, using
a fresh universally unique identifier (UUID), and
increments its fencing epoch. A second delivery, including the same Celery task
identifier, cannot acquire that live execution lease. Only the heartbeat renews
an execution lease. Completion is checked under the same row lock as acquisition.
Celery retries a busy delivery at the configured lease heartbeat interval, bounded
between 10 and 60 seconds, without incrementing the upload's processing retry
counter. After worker loss, the delivery can reclaim an expired lease; the old
fencing epoch can no longer publish. A completed upload returns success idempotently.
Legacy unnamespaced task owners are potentially active execution owners and are
never treated as queued reservations. Recovery must wait for expiry or follow the
explicit operator recovery contract.

Raw-source import success still requires raw and processed HTTP Live Streaming
(HLS) artifacts to be ready. Verified reuse of a received processed Hub generation
follows the separate receipt-bound rule below. An import can claim queued HLS work inline under the
existing artifact row lock, replacing its attempt key so a delayed delivery is
fenced. It never displaces an active encoder. While joining active work it polls
only database ownership and status, retains the import heartbeat, checks its
execution guard, and fails if the configured encoding wait timeout expires.
Waiting does not repeatedly hash or decrypt the source, and a join does not
repeatedly force regeneration. Existing Celery task runtime limits still apply
to the whole import; this coordination does not extend those limits.

## Database recovery and version mismatches

Video import and HLS Celery deliveries retry database connection failures,
missing-table/column errors, and a PostgreSQL NOT NULL violation for a column
absent from the installed model. Retry delays grow from 60 seconds to a maximum
of 15 minutes, with no retry-count limit. Driver error text is not placed in
Celery retry payloads because it can contain protected row values. An ordinary
NOT NULL violation for a known model field or an unknown programming error is
not classified as a recoverable version mismatch.

Imports retain their source, lease, and processing retry budget during these
failures. Redelivery must wait for the old lease to expire and acquire a fresh
fencing epoch. HLS retains encrypted staged and published output when database
commit status is uncertain. Redelivery reuses READY output, resumes VALIDATED
publication, or waits for the existing MATERIALIZING attempt to become stale.
It does not mark an uncertain commit failed or delete its potentially committed
output. Existing source identity, encryption, validation, and ownership checks
remain mandatory.

Backfill reservation exceptions produce a redacted per-artifact failure and
allow the remaining batch to run; any failure still makes the command exit
nonzero. A subsequent invocation re-evaluates readiness and reservations. The
command itself does not install a scheduler: automatic redispatch requires the
deployment's recurring backfill invocation, and Celery retries require an
operational broker and worker. A database schema newer than the installed
runtime requires deployment of a compatible release; retries do not repair the
schema, downgrade it, or manufacture encoding-profile defaults. These changes
must be present in the deployed worker to protect its deliveries.

## Terms and Abbreviations

- **MPEG-4 Part 14 (MP4):** the Moving Picture Experts Group container format
  used for canonical and compatibility video files.
- **HTTP Live Streaming (HLS):** the playlist-and-segment streaming format used
  for raw and processed playback.
- **Hypertext Transfer Protocol (HTTP):** the request protocol used for local
  byte-range access and authenticated media delivery.
- **frames per second (FPS):** the frame-rate measure used by the source,
  normalized master, and annotation workflow.
- **presentation timestamp (PTS):** the persisted display time that defines
  clinical frame and segment identity.
- **variable frame rate (VFR):** video whose frame intervals are not constant.
- **constant frame rate (CFR):** video whose frames follow a constant interval.
- **JavaScript Object Notation (JSON):** the structured output and log format.
- **identifier (ID):** a stable reference to a video or migration batch.
- **bits per second (BPS):** the bitrate unit used in the corresponding
  configuration-variable suffix.
- **millisecond (ms):** one thousandth of a second.
- **mebibyte (MiB)** and **gibibyte (GiB):** binary storage units of 1,048,576
  bytes and 1,073,741,824 bytes respectively.
- **H.264:** the Advanced Video Coding compression standard used by the
  production profile.
- **YUV 4:2:0 planar (YUV420P):** a pixel format with one luma plane and two
  chroma planes sampled at 4:2:0.
- **Advanced Encryption Standard in Galois/Counter Mode (AES-GCM):** the
  authenticated-encryption algorithm used by the chunked protected-media
  storage format.
- **Python Global Interpreter Lock (GIL):** the interpreter lock released by
  native Rust file input/output and AES-GCM work so independent encrypted
  range readers can progress concurrently.
- **NVIDIA Encoder (NVENC):** the dedicated NVIDIA hardware video encoder used
  only by the explicitly selected HLS hardware profile.
- **graphics processing unit (GPU):** the isolated hardware device exposed to
  the dedicated HLS worker.
- **constant quality (CQ):** the NVENC quality target used with bounded
  variable-bitrate rate control; it is not the libx264 Constant Rate Factor.

Names such as `pts_v1`, configuration-variable suffixes, command options, and
profile identifiers are literal implementation names. Their meaning is
described where they are introduced.

## Artifact Contract

| Role | Creation | Retention | Deletion condition |
|---|---|---|---|
| Canonical unprocessed MP4 | Import or reimport | Until human anonymization validation and complete approval of every gate | Only when the normalized master, matching processed HLS, `pts_v1`, segment references, and clinical profile approval are complete, and a blackened video exists |
| Canonical anonymized MP4 | Import, reimport, or reanonymization | Permanent; exactly one published generation | The previous generation may be deleted only after atomic publication and integrity verification of the new generation and when no media lease is active |
| Raw HLS | Successful import or reimport | Reproducible cache until raw-media release | Together with the raw master, subject to the same cleanup gates and only when no media lease is active |
| Processed HLS | Successful import, reimport, or reanonymization | Reproducible cache; only the current, actively referenced generation is retained | A superseded generation may be deleted after atomic publication of the new generation, reference reconciliation, and expiry of every lease |
| Streamable MP4 | Compatibility materialization | Only while the associated master generation is published | With the superseded master generation, after reference reconciliation and expiry of every lease |
| Extracted frame | Segment or frame workflow | According to the case-specific retention policy; it is not a storage-normalization cache | Only through the responsible frame or case lifecycle, never as a side effect of this migration command |
| Temporary transcode artifact | Normalization inside the protected transcoding directory | Only for the duration of one attempt | Through the `finally` path after success or failure; it is never marked as a valid master |
| Quarantine artifact | Explicit fail-closed exception process | Until documented review | Only after separate quarantine approval; never through an automatic production fallback |

HLS eviction is generation- and reference-based, not time-based. An old
generation may be removed only after the new generation has been published
completely and atomically, the database and filesystem agree, and no playback
or segment-update lease is active. Unknown references block eviction.

## Production Profile

The `clinical_h264_bounded_v1` profile preserves source resolution and the
source timeline. It uses H.264 High Profile, YUV420P, full-range color,
Faststart, and frames-per-second passthrough. Its default limits are:

- maximum video bitrate: 12,000,000 bits per second;
- maximum total size budget: 1,600,000 bytes per video second plus 4 MiB fixed
  container overhead;
- maximum source and output resolution: 4096 × 2160 pixels;
- maximum source FPS: 120; the separate annotation profile normalizes videos
  above 50 FPS to exactly 50 FPS;
- no resolution reduction until a clinically approved frame-quality benchmark
  exists;
- identical rational FPS and identical frame count;
- maximum duration drift: 100 ms or one source frame when that frame is longer.

The limits are configurable through
`ENDOREG_VIDEO_STORAGE_MAX_BIT_RATE_BPS`,
`ENDOREG_VIDEO_STORAGE_MAX_BYTES_PER_SECOND`,
`ENDOREG_VIDEO_STORAGE_FIXED_OVERHEAD_BYTES`,
`ENDOREG_VIDEO_STORAGE_MAX_WIDTH`, `ENDOREG_VIDEO_STORAGE_MAX_HEIGHT`,
`ENDOREG_VIDEO_STORAGE_MAX_SOURCE_FPS`, and
`ENDOREG_VIDEO_STORAGE_ANNOTATION_MAX_FPS`. Invalid or non-positive values fail
loudly instead of falling back. Media outside the profile is rejected. Release
then requires either a new versioned profile or an explicit quarantine review.
Stream copy, upsampling, and unbounded source-quality encoding are prohibited.

HLS encoding has a separate versioned encoder-profile selector,
`ENDOREG_HLS_ENCODING_PROFILE`. The default
`clinical_h264_libx264_crf_v1` profile preserves the existing libx264 Constant
Rate Factor 18 output. `clinical_h264_nvenc_cq_v1` uses `h264_nvenc`, preset
`p6`, variable-bitrate rate control, constant-quality value 18, zero nominal
bitrate, and the same maximum bitrate and buffer limits. It is valid only in a
dedicated worker that exposes exactly one GPU through `CUDA_VISIBLE_DEVICES`
and systemd device controls. The application addresses that isolated device as
logical GPU zero and performs a one-frame encoder preflight before staging any
HLS publication. Missing or inaccessible NVENC support fails closed; it never
falls back to CPU.

The selected encoder-profile name is persisted on the HLS artifact. Changing
the declarative profile rematerializes only HLS and leaves the canonical master
unchanged. Existing queued work retains its persisted profile during a rolling
deployment; a later reservation creates the newly selected HLS generation.

## Transcoding Steps and Idempotency

All storage transcoding entry points use `clinical_h264_bounded_v1` as their
shared compliance contract:

1. Import probes the incoming video before canonical raw storage. A compliant
   source is copied atomically into attempt-scoped protected staging without
   re-encoding. A non-compliant source is transcoded there, probed again, and
   published only after codec, pixel format, dimensions, frame rate, duration,
   frame count, bitrate, byte budget, and timeline checks pass.
2. Reimport and reanonymization probe the fresh anonymized candidate against
   the validated raw-source timeline. A compliant candidate is retained
   unchanged. A non-compliant candidate is transcoded into an attempt-scoped
   sibling and atomically replaces the candidate only after the same complete
   gate passes.
3. HLS materialization decrypts the selected raw or processed source only into
   its attempt-scoped directory inside the protected transcoding boundary. The
   source dimensions, frame rate, and timeline metadata must satisfy the shared
   source-admission checks. A single HLS encoding pass produces H.264 High
   Profile, YUV420P, full-range color, bounded bitrate, and source-timeline
   frame-rate passthrough directly from that unchanged staged source. There is
   no intermediate normalized MP4 encode. Source codec, pixel format, bitrate,
   and byte size may require conversion; the final HLS output must satisfy all
   storage-profile limits. Unsupported dimensions, frame rate, or timeline
   metadata fail closed rather than invoking resize or resampling. Before atomic
   publication, a temporary local playlist resolves the encrypted segments
   with the attempt key and probes the complete result again for codec, pixel
   format, dimensions, frame rate, duration, frame count, bitrate, byte budget,
   and timeline equivalence. The complete relative presentation-timestamp
   sequence must match the original staged source within the shared time-base
   resolution. Every positive, contiguous `EXTINF` boundary must resolve to an
   output presentation timestamp within one frame duration, and the playlist
   segment count and total duration must match the staged files and probed
   timeline. The validation playlist is removed immediately.
   A complete current HLS generation is returned idempotently without starting
   FFmpeg.

This removes an encoding pass and its temporary MP4 only when HLS previously
needed intermediate normalization. Already compliant sources previously skipped
that encode. Canonical raw and anonymized master normalization remains mandatory
at import boundaries, and HLS remains a separate encrypted derivative. This
change does not establish a single shared encode for canonical masters and HLS,
change encoder settings, or authorize clinical-quality approval or deployment.

Raw and processed HLS readiness is the required import, reimport, and
reanonymization contract, but this is not yet enforced consistently by every
caller. The materializer has typed results and atomically publishes complete
generations; however, the feature tracker currently records caller/result
mismatches that can allow false import success or cause a correction job to
reject a successful publication. Completed-duplicate and repair paths must not
be treated as production-ready until the `hls_callers_require_terminal_readiness`
criterion is verified.

The authenticated playlist boundary retains its idempotent demand fallback for
legacy, evicted, or reconciled records whose HLS is unexpectedly absent. While
that bounded worker task is queued or materializing, the endpoint returns a
private, non-cacheable `202 Accepted` response with `Retry-After`; broker
failure returns `503 Service Unavailable`. It never performs synchronous
transcoding in the web process.

Legacy HLS rows without a persisted source-content SHA-256 identity are never
served as READY. Playlist lookup treats them as stale and uses the same bounded
demand fallback to reserve an identity-bound replacement. Direct key and segment
lookups reject the legacy generation. The schema migration deliberately does not
decrypt and hash the full production corpus. Enabled LuxNix hosts can dispatch
automatic raw and processed replacement work, while the demand fallback covers
requests arriving before replacement. This repository does not yet contain
production evidence that the full corpus has converged, and the tracker records
open backfill admission and accounting defects; therefore automatic dispatch
must not be described as completed production backfill.

Playlist admission, materialization, and reconciliation check the playlist and
every referenced segment. Subsequent key and segment requests check the current
source identity, encoder profile, generation, playlist, and segment directory
without enumerating unrelated segments while holding the video row lock. Each
segment request also resolves and checks its requested file inside the protected
directory. Missing requested files fail closed immediately; missing sibling files
are detected on their own request or the next complete readiness check. These
checks do not use a readiness cache.

Repeated source-content checks reuse a fully computed plaintext Secure Hash
Algorithm, 256-bit (SHA-256) digest within the current process only. The bounded
cache holds at most 128 encrypted-file generations. Each entry belongs to the
same key-owning encrypted storage instance and local file path, device, inode,
size, mode, and nanosecond modification/change timestamps. Metadata is checked
before and after both hashing and reuse. Same-name replacement, in-place writes
(including restored modification times), missing files, and changes during
verification invalidate reuse or fail loudly. Concurrent checks of the same
source share verification; the cache and its locks are reset after process fork.

This optimization relies on the approved local filesystem reporting reliable
inode and change-time metadata. Other storage backends retain full hashing.
Restarted processes and distinct storage/key instances must verify again; no
cached digest is persisted or transmitted. Legacy database hashes alone never
authorize reuse. Playback, reservation, worker startup, and publication use the
same source verification. Artifact generation, encoder profile, media leases,
and output checks remain independent mandatory gates. The encrypted reader's
layout cache also includes device, inode, and change time so a replaced source
cannot inherit stale decryption metadata.

The separate `annotation_fps_resample_v1` workflow is the only storage workflow
that intentionally changes a video above 50 frames per second to exactly 50
frames per second. Import, reimport, storage normalization, and HLS
materialization do not use that limit as an implicit fallback.

Watcher handoff and transcoding durations are fail-closed configuration
boundaries. `WATCHER_POLL_INTERVAL_SECONDS` must be a positive finite number,
`WATCHER_STABLE_AFTER_SECONDS` must be a non-negative finite number, and
`FFMPEG_TRANSCODE_TIMEOUT_SECONDS` must be a positive integer. Invalid values
stop startup or the first responsible workflow instead of being silently
clamped. The handoff service observes the complete configured stability window;
it does not shorten a ten-second operator setting to an internal test-oriented
cap. A zero stability window remains available for explicit test and already
atomic handoff callers, while production watcher profiles use a positive
window. The effective readiness deadline is never shorter than the stability
window plus one polling interval.

Watcher readiness events and propagated handoff errors never include the raw
source path or filename. They use the shared structured-logging reference with
a SHA-256 path digest and a non-identifying suffix, alongside size, modification
time, and workflow-stage fields required for diagnosis. Operators correlate
repeated events by digest; enabling verbose logging must not reveal intake
directory topology or patient-derived filenames.

## Timeline and Frame-Quality Gate

During import, the source timeline is persisted in
`VideoFile.meta.source_timeline` with presentation-timestamp timeline version
`pts_v1`. For variable-frame-rate media, `Frame.timestamp` contains
presentation timestamps read by ffprobe; calculating timestamps as
`frame / fps` is prohibited for variable frame rate. For constant-frame-rate
media, the rational frames-per-second mapping is persisted. Before an existing
master is replaced, segment start and end positions are resolved from these
persisted coordinates and stored with the source and output probes in
`VideoFile.meta.storage_normalization`.

Variable frame rate is allowed only when source and output time bases are
available. Divergent frame rate, frame count, duration, or resolution prevents
publication. Resolution remains unchanged so that high-quality frames can be
extracted at persisted segment timestamps. Any later resolution reduction first
requires a versioned benchmark with representative clinical videos and reviewer
approval.

The annotation contract for videos above 50 frames per second is separate.
Before the first segment row exists, the `annotation_fps_resample_v1` profile
creates a constant-frame-rate master at 50 frames per second, updates the frame
rate, duration, frame count, and frame timestamps, and stores
source/output provenance in `VideoFile.meta.fps_normalization`. Existing
segments or extracted frames block this coordinate-changing operation.

Playlist, key, and segment requests renew a stream lease. Transcoding, HLS
regeneration, and cleanup are deferred in a resumable state while a stream or
segment-update lease is active.

### On-Demand Single-Frame Decode

Single-frame annotation requests must not materialize the complete encrypted
video as a temporary plaintext file. The backend resolves the requested frame
through the authoritative persisted presentation timestamp and gives FFmpeg a
short-lived, seekable Hypertext Transfer Protocol (HTTP) byte-range input bound
exclusively to the local loopback interface. A cryptographically random path
limits that input to the requesting FFmpeg process lifetime.

FFmpeg remains responsible for Moving Picture Experts Group container seeking:
it reads the container metadata and requests the byte ranges needed to seek to
the preceding keyframe and decode through the target presentation timestamp.
The backend decrypts only the encrypted chunks intersecting those requested
ranges. It does not construct an invalid standalone slice beginning at a media
packet offset, and it does not persist a second frame-to-timestamp mapping.

Storage without local plaintext access or authenticated random-access
decryption fails loudly. The master key remains inside the storage backend and
is never included in the loopback address, process arguments, or response
payload. Reimport, normalization, and reanonymization therefore continue to use
the published artifact generation and the existing `pts_v1` coordinate
contract without introducing another independently versioned seek index.

Application-served encrypted byte ranges prefer the native Rust reader. Each
native call is capped at 4 MiB by the Python storage boundary and at 8 MiB by
the Rust boundary; file input/output and AES-GCM authentication run without the
Python GIL. Independent requests use separate file handles. A missing native
extension at process start uses the byte-identical Python reference reader,
while native key, authentication, geometry, or length failures stop the
response instead of falling back.

## Inventory and Migration

### Playback Backfill Order

`materialize_video_hls` selects videos with a processed-file reference first.
Within that group, confirmed anonymization or created/validated segment
annotations give priority; remaining processed videos follow, then raw-only
videos. Each group uses ascending video identifiers, and this ordering applies
before `--limit`. These state flags influence scheduling only; existing source,
generation, lease, and encoding validation remains authoritative.

With the default `--artifact-kind both`, all selected processed artifacts are
handled before any selected raw artifact. Dry runs, synchronous `--inline`
execution, and queue dispatch share that order. Explicit `--video-id` selection
and `--artifact-kind` filters still apply; raw-only selection keeps ascending
video identifiers. Both artifact kinds remain required where applicable.
This orders newly submitted work only: running tasks are not interrupted and
already queued tasks are not reordered. Worker capacity is unchanged.

### Storage Inventory

The default mode is always read-only:

```bash
devenv shell -- python manage.py normalize_video_storage --json
devenv shell -- python manage.py normalize_video_storage --video-id 123 --json
```

Inventory is bounded before any filesystem traversal. The default batch is 100
videos and `--limit` cannot exceed 1,000. JSON output includes
`inventory_cursor.after_video_id`, `next_after_video_id`, and `batch_limit`.
Within a selected video, traversal is also capped at 20,000 filesystem entries
per HLS artifact. Exceeding that cap stops only that artifact scan and marks it
as `incomplete_hls_inventory_artifacts` and unreconciled. This limit never
truncates, rewrites, moves, or partially stores video or HLS media; it only
limits read-only metadata traversal. The reported byte count is explicitly
incomplete, reclaimable bytes are forced to zero, and every apply or cleanup
action is rejected before mutation. Read-only inventory continues so operators
can identify other pressure sources without risking data loss or an unbounded
walk.
Resume the next stable primary-key batch with the returned non-null cursor:

```bash
devenv shell -- python manage.py normalize_video_storage \
  --after-video-id 1234 \
  --limit 100 \
  --json
```

An empty batch returns a null next cursor and ends the scan. The database batch
is selected in ascending video-ID order before inspecting files; reclaimable
byte ordering is applied only within that bounded batch. Do not raise the hard
limit or combine output from different database snapshots as one reconciliation
receipt.

The JavaScript Object Notation output reports canonical raw and processed
media, raw and processed HLS, and both streamable full-video variants
separately. `reclaimable_raw_bytes`
counts only validated videos with verified normalization evidence. The output
also reports normalized, pending, failed, and unreconciled videos, free storage,
projected temporary demand, and bytes by artifact role. Every run and video has
a structured `batch_id`.

Capacity thresholds are configurable through
`ENDOREG_VIDEO_STORAGE_WARNING_FREE_BYTES` (default 2 GiB) and
`ENDOREG_VIDEO_STORAGE_STOP_FREE_BYTES` (default 1 GiB). The stop threshold must
be lower than the warning threshold. Before the batch begins, the largest
sequential temporary output is projected with a 10 percent safety margin. No
destructive step starts below the stop threshold or when a database reference
is missing from the filesystem. Inventory is reconciled again after every
video; a mismatch stops the batch before the next video is changed.

Migration is technically disabled by default. After clinical approval of the
quality limits and verification of the timeline tests, the operator must enable
the gate deliberately and begin with a small batch:

```bash
export ENDOREG_VIDEO_STORAGE_DESTRUCTIVE_MIGRATION_ENABLED=true
devenv shell -- python manage.py normalize_video_storage --limit 5 --apply --json
```

Validated raw artifacts are deleted only with the additional
`--cleanup-validated-raw` option. Within each primary-key cursor batch, the
command orders candidates by reclaimable bytes, verifies temporary storage
headroom, and preserves the previous source after transcode, timeline, quality,
or HLS failure.

Raw cleanup additionally requires reviewed
`VideoFile.meta.clinical_frame_quality` evidence with `approved: true`, a
matching `profile_name`, reviewer, timestamp, and benchmark reference. The
`pts_v1` timeline, every segment boundary, and processed HLS belonging to the
current `processed_file` generation must also be ready. Normal human
anonymization validation remains successful when one of these cleanup gates is
missing; it logs the blockers and preserves every raw artifact.

## Abort and Recovery

The import success boundary is identical across this runbook, the hub ingest
runbook, and the feature tracker: a video is successful only after the
canonical master, required HLS generations (both raw and processed for regular
raw imports; processed for verified received-generation reuse), durable media
state, and successful processing history all refer to the same validated
generation under the current fencing token. A failure in any one of those
steps is not a degraded success. It preserves the previous valid generation
and leaves the new attempt retryable, failed, lost, or quarantined according to
the ownership and integrity evidence.

After an error:

1. stop further batches and isolate the affected video record;
2. run `normalize_video_storage --video-id <id> --json` again in read-only mode;
3. inspect the source master, normalization evidence, processed HLS, and segment
   references;
4. repeat the same video-identifier batch idempotently only after the error is
   fixed;
5. never delete raw files manually while `normalization_verified` or
   `anonymization_validated` is false.

Disk-full, missing metadata, variable frame rate without a time base, and
inconsistent filesystem/database state are fail-closed conditions. They never
justify stream copy or a manual deletion fallback.

### Pause and Resume

The command processes videos sequentially. Pause it by stopping the current
foreground process, then inventory the same selection without `--apply`.
Resume explicit video IDs or use the last completely reported
`next_after_video_id`; never advance past a partially completed apply batch
without reconciling its result rows. Validation and hash checks keep already
compliant videos idempotent. Before resuming, active media leases must have
expired, capacity must be above the stop threshold, and every reference must be
reconciled.

### Quarantine and Release

A managed import source remains in protected ingest storage when capacity,
transcoding, worker, dispatch, or other ordinary processing fails. The upload
job uses bounded, observable retries and retains the source for an explicit
operator decision after retry exhaustion. These operational failures do not
create quarantine copies.

Quarantine is reserved for rare trust-boundary or integrity cases where a
source cannot safely continue as a managed import, especially a rejected
pre-anonymized drop. A profile, timeline, hash, quality, or reference failure
must still fail closed and must not be bypassed through a weaker codec path.
The operator records the video ID, `batch_id`, error, and affected generation
and retains the previous valid source. A quarantine release may return media
to migration only under a versioned, reviewed profile or after the source has
been corrected. Quarantine files are reviewed through the existing quarantine
workflow and are not deleted by `normalize_video_storage`.

### Duplicate Sources and Received Hub Generations

Source identity and legacy duplicate reconciliation are governed by
[`source_identity_deduplication_and_legacy_reconciliation`](../feature-tracking/ImportPipelineRobustness.yml).
The incoming plaintext Secure Hash Algorithm, 256-bit (SHA-256) digest identifies
the source; a processed generation has a separate digest. A matching filename,
duration, or frame count does not authorize consolidation.

A duplicate import must never delete an existing video row merely because its
raw source is absent or unreadable. Direct creation checks existing identity
before normalization. Reuse and failed-history paths reject an unusable raw
source before resetting the existing record. Preserve its annotations, accepted
master, processing history, and incoming source for manual reconciliation.
An absent raw file can reflect an intentional lifecycle decision; it is not
evidence that a record is an orphan.

A previously received Hub transfer can establish completed-import reuse for
the same original source digest even when no raw-file reference exists. This
requires an applied processed-media transfer bound to the current video and
center, a validated envelope receipt matching the transfer and node identities,
and a current accepted anonymized generation whose plaintext size and digest
match that receipt. The incoming center must match the retained video. Missing
receipts, changed generations, unreadable output, and dangling raw references
do not qualify. Forced raw reprocessing still requires a usable raw source.

For this verified received-generation path, duplicate import requires ready
processed HTTP Live Streaming and does not attempt to reconstruct raw media.
It retains the incoming raw source even in the managed import directory; the
processed-only receipt does not authorize deleting the only local raw copy.
Regular raw imports continue to require both raw and processed renditions.
This exception does not alias arbitrary processed-input hashes to raw identities,
merge legacy records, or transfer acceptance to a different generation.

### Publication Rollback

Before raw cleanup, rollback means retaining the previous valid master
generation and removing the incomplete new generation through typed cleanup
paths. After raw cleanup, rollback is allowed only from an approved encrypted
backup. Manual renaming, copying to public mounts, or direct database repair is
prohibited. The master hash, `pts_v1`, segment boundaries, processed-HLS
generation, and inventory reconciliation must then be verified again.

### Disk Full and Process Termination

No further apply run starts after a disk-full condition. Temporary `.part.mp4`
artifacts are removed inside the protected transcoding directory through the
central filesystem operations; the previous master reference remains valid.
After process termination, first inspect free capacity, remaining staging
artifacts, and database/filesystem references. Only then run a read-only
inventory followed by a small resume batch.

## Release Gates

Destructive legacy migration remains disabled until all of the following
evidence exists:

1. `temporal_frame_contract` is verified by stable tests.
2. A clinical benchmark based on persisted segment presentation timestamps has
   selected the smallest acceptable profile, and a clinical reviewer has
   approved it.
3. Operations and storage owners have reviewed the artifact matrix, capacity
   thresholds, backup, and recovery procedure.
4. Security has reviewed protected staging paths, atomic publication,
   structured filesystem logs, and failure-injection results.
5. A dry run and a small apply batch have been demonstrated and fully reconciled
   in a production-like encrypted copy.

Reviewer names and approval timestamps are recorded only as evidence in the
feature YAML. This runbook explains the process but does not maintain a separate
completion status.
