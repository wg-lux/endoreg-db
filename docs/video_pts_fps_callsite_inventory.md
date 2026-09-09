# Frames-per-Second and Presentation-Timestamp Call-Site Inventory

Last reviewed: 2026-09-03. This inventory is the technical reference for
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml).
The feature YAML is the only authoritative source for maturity and release
status.

## Scope and Status Terms

The inventory covers `endoreg_db` and its tests plus the lx-annotate frontend,
backend, and tests. Generated Vue JavaScript mirrors and database migrations
are excluded. Searches include `fps`, frame and time fields, `currentTime`,
`seek`, `timestamp`, `pts`, `normalize-fps`, multiplication by frame rate, and
division by frame rate. The classification below is manual; display timestamps
and audit timestamps are not automatically frame-identity risks.

- **migrated:** uses the authoritative presentation-timestamp contract;
- **open:** can produce incorrect boundaries for variable-frame-rate media or
  a changed video generation;
- **conditionally safe:** correct only while its documented precondition holds;
- **intentional:** explicitly creates a new versioned timeline.

## Binding Rule

Frames per second (FPS) is a rate, not a frame identity. Clinical frame
identity consists of `Frame.frame_number`, `Frame.timestamp`,
`timeline_version`, and the concrete published video generation.

- Variable frame rate must never be resolved through `time * fps` or
  `frame / fps`.
- Constant frame rate may use the rational FPS mapping only as an explicit
  fallback.
- An FFmpeg `fps` filter creates a new frame sequence. Output indices must not
  inherit source-frame primary keys or source-frame indices.
- The backend owns timestamp-to-frame conversion. Frontend
  `HTMLMediaElement.currentTime` values remain timestamps until the backend
  resolves them against the published timeline.

## Priority 0: Open Cross-Repository Risk

| Repository | Call site | Current finding | Required change |
| --- | --- | --- | --- |
| lx-annotate | `frontend/src/utils/segmentTimeline.ts`, `frontend/src/stores/videoStore.ts` | **Migrated.** Create and update send `start_time` and `end_time` without client-side FPS quantization and accept canonical boundaries from the backend response. | Retain the irregular-timestamp regression tests. |
| lx-annotate / endoreg-db | `Timeline.vue:stepFrame`, `media/videos/<pk>/timeline/frame-neighborhood/` | **Migrated.** Frame stepping requests a bounded backend-computed presentation-timestamp neighborhood. Missing variable-frame-rate timestamps fail closed. | Preserve the backend query budget and authoritative response cache. |
| lx-annotate | `Timeline.vue:copySelectedSegment,pasteSegment` | **Migrated for mutation.** Copy and paste preserve timestamp duration without deriving minimum duration from FPS. | Keep final validation and canonicalization at the central store and backend boundary. |
| endoreg-db | `endoreg_db/utils/frame_stream.py` | **Open.** Selection by `select=eq(n,...)` is frame-index stable for one generation, but `FrameSample.timestamp` is still constructed as `frame_number / fps` for path decoders. | For `VideoFile`, use persisted `Frame.timestamp`. Path-only decoders must read presentation timestamps from FFmpeg or ffprobe, or explicitly mark the timestamp non-authoritative. |

The open `frame_stream.py` path prevents a complete cross-repository proof of
timestamp-accurate decoding. Segment create, update, and stepping are migrated.

## Resampled 50 FPS HLS Incident

On 2026-07-28 and 2026-07-29, the gc-10 `ffmpeg_media` worker repeatedly
materialized processed HLS for video 44 and then rejected it with
`Output FPS drifted from 49.8549 to 50`. Immediate redelivery kept the work at
the front of the queue. Staged artifacts were cleaned after each failure, so
the incident did not by itself establish corruption of the canonical import.

The repository now implements a narrowly scoped exception in
`endoreg_db/services/video_storage/validation.py` for a proven
`annotation_fps_resample_v1` generation. It distinguishes nominal rate,
measured average rate, and the presentation-timestamp timeline, and requires:

- matching current source-generation content identity;
- nominal 50/1 FPS source and output provenance;
- equal frame counts;
- constant, strictly monotonic 50 FPS presentation-timestamp cadence;
- equivalent timeline span and duration within the time-base tolerance;
- matching persisted segment and extracted-frame boundaries.

The global FPS tolerance is not broadened. Missing or foreign provenance,
generation mismatch, frame loss or duplication, real timeline drift, and
variable frame rate without authoritative timestamps still fail loudly.
Deterministic validation-failure suppression exists in the HLS lifecycle, but
the broader feature remains blocked pending production-like cross-repository,
broker-loss, worker-loss, and packaged-runtime evidence recorded in the feature
tracker. Do not infer deployment on gc-10 from repository code alone.

## Annotation Export

| Call site | Status | Assessment |
| --- | --- | --- |
| `endoreg_db/utils/video/command_construction.py:_build_extract_frames_command` | **Migrated.** | Identity-preserving extraction uses `fps_mode=passthrough`; an explicit sampling rate is reserved for an independent new sequence. |
| `endoreg_db/export/frames/export_frames_with_labels.py:_extract_and_move_transcoded_frames` | **Migrated.** | Selected annotations are extracted over their persisted source-frame range without an FPS filter and are checked against all requested frame primary keys. |
| `endoreg_db/export/frames/export_frames_with_labels.py:_move_extracted_frames_to_pk_names` | **Migrated.** | The unsafe `frame_number - 1` fallback has been removed; a missing frame fails export. |
| lx-annotate `ExportAnnotations.vue` | **Migrated.** | Identity-preserving exports do not send `transcode_fps`; `ExportAnnotations.pts-contract.test.ts` protects the contract. |
| `materialize_training_frames.py` and `export_frame_annot` | **Conditionally safe.** | Legacy `transcode_fps` can still pass through, but the annotation exporter does not use it for frame identity. |

## Endoreg Clinical Coordinates

| Call site | Status and remaining work |
| --- | --- |
| `endoreg_db/services/video_timeline.py` | **Migrated.** Typed mapping gives presentation timestamps priority, uses the lower frame on a nearest-boundary tie, fails for variable frame rate without timestamps, and uses half-up rounding for constant frame rate. |
| `endoreg_db/services/video_files/_time.py`, `metadata.py` | **Migrated.** Persisted neighboring timestamps are passed to the central mapping service. Model methods are compatibility wrappers. |
| `models/label/label_video_segment/label_video_segment.py` | **Migrated.** Segment times use the shared frame-to-timestamp resolver. |
| `serializers/label_video_segment/label_video_segment.py` | **Migrated.** Create, update, and response use the central resolver and do not hide variable-frame-rate failures behind default FPS. |
| `services/video_segments_bulk_mutation.py` | **Migrated.** Bulk mutation delegates to the timestamp-aware serializer; consumers must supply timestamps. |
| `serializers/video/video_file.py` | **Mostly migrated.** Segment times use boundary timestamps. A separate `total_frames / fps` metadata-duration fallback remains lower priority. |
| `services/segment_sync.py` | **Migrated.** Creation and change comparison use persisted timestamps. |
| `services/video_storage/timelines.py` | **Migrated.** Normalization evidence materializes `pts_v1` boundaries and fails for incomplete variable-frame-rate boundary timestamps. |
| `services/video_files/frames.py:extract_video_frame_range_by_timestamps` | **Migrated.** Timestamp ranges are resolved centrally and logged with timestamp and frame index. |

`utils/extract_specific_frames.py` and
`serializers/Frames_NICE_and_PARIS_classifications.py` were removed on
2026-08-04 as unmounted legacy experiments. The authoritative services and
persisted coordinate contract were not changed by that removal.

Primary regression coverage includes
`tests/services/test_video_temporal_mapping.py`,
`tests/services/test_segment_frame_extraction.py`,
`tests/import_files/test_video_import_normalization.py`, and
`tests/services/test_video_storage_normalization.py`.

## lx-annotate Classification

| Call site | Status and remaining work |
| --- | --- |
| `VideoExaminationAnnotation.vue` | **Conditionally safe.** Playback and seeking use seconds and segment mutations use the timestamp-first store. A complete proof that the loaded HLS generation matches the segment timeline remains open. |
| `VideoExaminationAnnotation.vue:ensureSegmentationFpsReady` | **Intentional.** Before the first segment row, `annotation_fps_resample_v1` may create a new 50 FPS constant-frame-rate timeline. The user interface blocks until ready. Atomic HLS publication and generation binding still need production-like cross-repository evidence. |
| `videoStore.ts:backendSegmentToSegment` | **Migrated consumer.** It prefers backend timestamps and canonical frame boundaries and must not reconstruct them locally from FPS. |
| `utils/timeHelpers.ts`, `timeUtils.ts` | **Open, latent.** Generic seconds/frame conversions remain exported, including a default of 50 FPS. Restrict them to explicitly proven constant-frame-rate use or remove them. |
| `OutsideSegmentComponent.vue` | **Conditionally safe.** It uses backend segment times and `currentTime`; safety depends on segment data and video belonging to the same generation. |
| `AnonymizationValidationComponent.vue` | **Conditionally safe.** Raw and processed playback synchronize by media timestamp, which is not itself a frame-identity proof. Quality review needs presentation-timestamp-selected comparison frames. |
| `FrameSelectorPage.vue` | **Conditionally safe.** It uses backend frame indices and stream URLs; displayed timestamps inherit the open `frame_stream.py` limitation. |
| `lx_annotate/hub/hub_export_payloads.py` | **Migrated transport.** It transports `frame.timestamp` without deriving it from FPS. |

## Lower-Priority Derived Time Uses

The following FPS uses do not currently persist clinical frame identity, but
can produce uneven windows or imprecise derived duration for variable frame
rate:

- `endoreg_db/utils/ai/predict.py` and `utils/ai/postprocess.py`;
- `models/metadata/video_prediction_logic.py` and
  `video_prediction_meta.py`;
- `utils/calc_duration_seconds.py`;
- the OpenCV duration fallback in `serializers/video/video_file.py`;
- `management/commands/profile_segment_updates.py`.

`services/video_temporal_inference.py` is migrated: smoothing, minimum duration,
and gap closure are evaluated against persisted timestamps, invalid sequences
fail loudly, and output boundaries map back to original frame numbers.

## Backend and Frontend Mutation Contract

1. lx-annotate sends `start_time` and `end_time` as media timestamps in seconds
   without local FPS quantization.
2. Endoreg validates them against the current video generation and resolves
   them through persisted timestamps or an explicit constant-frame-rate
   fallback.
3. The response returns canonical timestamps, frame numbers, and timeline or
   generation evidence.
4. lx-annotate replaces optimistic boundaries with the response.
5. Missing variable-frame-rate timestamps, mixed generations, and running FPS
   normalization block mutation.

## Remaining Regression Evidence

- Verify `X-Frame-Timestamp` and `FrameSample.timestamp` against persisted
  presentation timestamps for variable-frame-rate video.
- Run a cross-repository video-above-50-FPS path through
  `required -> queued -> running -> ready/failed`, authenticated processed HLS,
  atomic generation publication, lease contention, and stable segment
  timestamps after reload.
- Reject edits that combine segment data from an old generation with newly
  published HLS.
- Exercise the proven 50 FPS measured-rate exception and its negative cases in
  production-like broker and worker-failure lanes.

## Legacy `transcode_fps` Retirement

The application programming interface and `lx_dtypes` temporarily continue to
accept `transcode_fps`. The annotation exporter ignores the value for frame
identity, current lx-annotate does not send it, telemetry should identify old
clients, and only a later versioned API may remove the field. It must not be
given a new ambiguous meaning.
