# Video Storage Normalization

This document is the operational and architecture runbook for the
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml)
feature. The feature definition in YAML Ain't Markup Language (YAML) format is
the only authoritative source for implementation and approval status.

## Terms and Abbreviations

- **MPEG-4 Part 14 (MP4):** the Moving Picture Experts Group container format
  used for canonical and compatibility video files.
- **HTTP Live Streaming (HLS):** the playlist-and-segment streaming format used
  for raw and processed playback.
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

Names such as `pts_v1`, configuration-variable suffixes, command options, and
profile identifiers are literal implementation names. Their meaning is
described where they are introduced.

## Artifact Contract

| Role | Creation | Retention | Deletion condition |
|---|---|---|---|
| Canonical unprocessed MP4 | Import or reimport | Until human anonymization validation and complete approval of every gate | Only when the normalized master, matching processed HLS, `pts_v1`, segment references, and clinical profile approval are complete, and a blackened video exists |
| Canonical anonymized MP4 | Import, reimport, or reanonymization | Permanent; exactly one published generation | The previous generation may be deleted only after atomic publication and integrity verification of the new generation and when no media lease is active |
| Raw HLS | Raw playback | Reproducible cache until raw-media release | Together with the raw master, subject to the same cleanup gates and only when no media lease is active |
| Processed HLS | After publication of the anonymized master | Reproducible cache; only the current, actively referenced generation is retained | A superseded generation may be deleted after atomic publication of the new generation, reference reconciliation, and expiry of every lease |
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

## Inventory and Migration

The default mode is always read-only:

```bash
devenv shell -- python manage.py normalize_video_storage --json
devenv shell -- python manage.py normalize_video_storage --video-id 123 --json
```

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
`--cleanup-validated-raw` option. The command orders candidates by reclaimable
bytes, verifies temporary storage headroom, and preserves the previous source
after transcode, timeline, quality, or HLS failure.

Raw cleanup additionally requires reviewed
`VideoFile.meta.clinical_frame_quality` evidence with `approved: true`, a
matching `profile_name`, reviewer, timestamp, and benchmark reference. The
`pts_v1` timeline, every segment boundary, and processed HLS belonging to the
current `processed_file` generation must also be ready. Normal human
anonymization validation remains successful when one of these cleanup gates is
missing; it logs the blockers and preserves every raw artifact.

## Abort and Recovery

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
Resume with the same video IDs or bounded batch. Validation and hash checks keep
already compliant videos idempotent. Before resuming, active media leases must
have expired, capacity must be above the stop threshold, and every reference
must be reconciled.

### Quarantine and Release

A profile, timeline, hash, quality, or reference failure must not be bypassed
through a weaker codec path. The operator records the video ID, `batch_id`,
error, and affected generation and retains the previous source. A quarantine
release may return media to migration only under a versioned, reviewed profile
or after the source has been corrected. Quarantine files are reviewed through
the existing quarantine workflow and are not deleted by
`normalize_video_storage`.

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
