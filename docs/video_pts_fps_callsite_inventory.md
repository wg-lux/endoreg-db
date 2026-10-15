# Frame rate, presentation timestamps and frame provenance

Backend ownership and contracts reviewed on 2026-10-15. Scope, follow-up work,
and verification evidence belong to
[VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml),
particularly `temporal_frame_contract`, `decoder_timestamp_authority`,
`video_examination_annotation_contract` and `frame_materialization_boundary`.

## Coordinate contract

Frames per second (FPS) expresses a rate. Clinical frame identity combines the
zero-based `Frame.frame_number`, persisted `Frame.timestamp`, timeline version
and concrete published canonical generation. Presentation timestamps (PTS) are
authoritative for variable-frame-rate media. Constant-frame-rate fallback uses
the configured rate and timeline origin.

An FFmpeg `fps` filter creates a new frame sequence. Output positions belong to
that sequence and must not inherit source frame identities by index. The
explicit `annotation_fps_resample_v1` workflow may convert media above 50 FPS to
50 FPS only before segments or extracted-frame coordinates are persisted.
Native import, reimport, blackening and storage normalization preserve cadence.
HTTP Live Streaming (HLS) remains a derivative bound to a canonical generation.

## Backend ownership

Paths in this table are relative to `endoreg_db/`.

| Owner | Responsibility |
| --- | --- |
| `services/video_files/timeline.py` | Resolve frame/time boundaries, preferring persisted PTS; nearest persisted ties select the lower frame number, while constant-rate fallback uses half-up rounding. Terminal boundaries use timeline origin plus duration. |
| `services/video_files/_time.py`, `services/video_files/metadata.py` | Load persisted neighboring coordinates and delegate to the shared resolver. |
| `models/label/label_video_segment/label_video_segment.py` | Expose model compatibility methods for timestamp-aware segment boundaries. |
| `serializers/label_video_segment/label_video_segment.py` | Canonicalize segment creation, updates and responses through the central resolver. |
| `services/annotations/segments_bulk_mutation.py`, `services/annotations/segment_sync.py` | Apply bulk mutations and synchronize segment boundaries using the central coordinate contract. |
| `services/video_files/temporal_inference.py` | Evaluate smoothing, minimum durations and gap closure against persisted timestamps and map results to original frame numbers. |
| `services/video_storage/timelines.py` | Persist versioned source coordinates and segment references for normalization evidence. |
| `services/video_storage/processed_timelines.py` | Validate complete decoded counts and bounded relative drift; atomically publish processed coordinates, retained raw history and segment remappings. |
| `utils/frame_stream.py:read_video_file_frame_jpeg` | Resolve `video.frame_number_to_s` before timestamp-selected in-memory JPEG decoding, using local plaintext or authenticated seekable loopback input. |
| `services/frames/training_images.py` | Authorize selected processed-frame identities and recheck clinical release gates before using the shared JPEG reader. |
| `services/video_files/frames.py:extract_video_frame_range_by_timestamps` | Resolve/log timestamp ranges before the retired export-only materialization boundary. |

The [frame extraction contract](video_frame_extraction_contract.md) describes
streaming consumers, export boundaries and retained cache diagnostics.

## Encoder ancestry

`services/video_storage/frame_provenance.py` records FFmpeg encoder-input decoder
indices, complete integer source/output PTS sequences, rational time bases and
content hashes. Each `source_frame_indices` position identifies the observed
source frame for that output position. Encoder statistics are checked against
independently decoded coordinates; packet order does not establish presentation
order. Constant decoder/container clock offsets remain explicit rational values.

Clinical boundary remapping in `processed_timelines.py` chooses nearest processed
PTS within validated temporal bounds. That operation updates clinical coordinates;
transformation ancestry comes from decoder observations. The typed receipt is
stored separately from pixels, and raw timestamp history survives replacement.
See the [canonical provenance runbook](video_storage_normalization.md#out-of-band-frame-provenance-and-independent-recording-clocks).

## Annotation export

| Owner | Contract |
| --- | --- |
| `utils/video/command_construction.py:_build_extract_frames_command` | Identity-preserving extraction uses `fps_mode=passthrough`; explicit sampling creates a separate sequence. |
| `export/frames/export_frames_with_labels.py` | Extract requested persisted frame ranges, verify requested primary keys and name images by those identities. Missing requested frames fail export. |
| `materialize_training_frames.py`, `export_frame_annot` | Legacy `transcode_fps` remains accepted at the compatibility boundary; annotation export preserves source identity independently of that field. |

## Backend and frontend mutation contract

1. lx-annotate sends `start_time` and `end_time` as media timestamps in seconds.
2. The backend resolves them against the current canonical timeline and returns
   canonical timestamps, frame numbers and timeline/generation evidence.
3. The frontend replaces optimistic boundaries with the backend response and
   uses backend-provided timestamp neighborhoods for frame stepping.
4. Copy/paste preserves timestamp duration. Mutation validation remains at the
   backend boundary.

Frontend ownership is in `frontend/src/utils/segmentTimeline.ts`,
`frontend/src/stores/videoStore.ts` and `Timeline.vue` in lx-annotate. Its
`ExportAnnotations.vue` requests identity-preserving exports. Hub payloads carry
persisted `frame.timestamp`. Cross-repository review evidence is maintained in
the feature tracker.

## Verification ownership

| Tests | Contract |
| --- | --- |
| `tests/services/test_video_temporal_mapping.py` | Persisted variable-rate coordinates, missing-PTS rejection and constant-rate fallback |
| `tests/services/test_frame_provenance.py` | Decoder ancestry, malformed receipts, electrical clock alignment and actual encoding paths |
| `tests/services/test_processed_timelines.py` | Nominal-rate metadata differences, nearest-boundary ties, drift rejection and retained annotations |
| `tests/utils/video/test_frame_stream.py` | Shared decoding and seekable processed input |
| `tests/services/test_export_frames_contract.py` | Identity-preserving export |
| `tests/import_files/test_video_import_normalization.py` | Import normalization boundary |

The feature tracker owns executable release gates, incident evidence and
remaining acceptance work.
