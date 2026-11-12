# Frame rate, presentation timestamps and frame provenance

Backend and lx-annotate frame-operation contracts reviewed on 2026-11-11.
Scope, follow-up work and verification evidence belong to
[VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml),
particularly `temporal_frame_contract`, `decoder_timestamp_authority`,
`video_examination_annotation_contract` and `frame_materialization_boundary`.

## Coordinate contract

Frames per second (FPS) expresses a rate. Clinical frame identity combines the
zero-based `Frame.frame_number`, exact integer `Frame.presentation_timestamp`,
the stream's rational time base, persisted seconds in `Frame.timestamp`, timeline
version and concrete published canonical generation. Presentation timestamps
(PTS) are authoritative. A rate-derived constant-frame-rate fallback requires
explicit persisted `rational_cfr` evidence and includes the timeline origin;
a nominal or default rate alone cannot establish that contract. A timeline
marked `ffprobe_pts` requires persisted coordinates even for constant-rate media.

An FFmpeg `fps` filter creates a new frame sequence. Output positions belong to
that sequence and must not inherit source frame identities by index. The
explicit `annotation_fps_resample_v1` workflow may convert media above 50 FPS to
50 FPS only before segments or extracted-frame coordinates are persisted.
Native import, reimport, blackening and storage normalization preserve cadence.
HTTP Live Streaming (HLS) remains a derivative bound to a canonical generation.

## Frame skipping and frame-based export remain valid

Frame numbers remain useful for counting and selecting frames. A jump of N
frames selects ordinal `i + N` on the current canonical generation, then seeks
to that frame's persisted PTS. It must not add `N / fps` seconds or N timestamp
ticks. For example, timestamps `[2.00, 2.04, 2.11, 2.16, 2.24]` give a two-frame
jump from frame 1 to frame 3 at 2.16 seconds; every-second-frame selection gives
frames `[0, 2, 4]` at `[2.00, 2.11, 2.24]`. Selection preserves those identities
and timestamps without changing the master or renumbering stored annotations.

The current frontend exposes adjacent-frame steps (`-1` or `1`), including
keyboard shortcuts, using fresh backend timestamp neighborhoods. The shared
store/helper also supports signed offsets up to 50 frames using the existing
neighborhood radius; larger offsets fail explicitly. No configurable N-frame
control is added. A time-based skip remains a time operation;
when an exact frame is required, resolve the destination through the canonical
mapper. Equal frame strides do not imply equal elapsed durations for
variable-frame-rate (VFR) video.

Frame-based exports retain frame primary keys and canonical frame numbers.
The `pts_dataset_v1` profile assigns a separate one-based `export_frame_index`
per unique exported frame in PTS order; repeated labels share that index.
Sparse selection can omit any intermediate frames while preserving each
selected frame's original PTS, time base and processed-content hash. Export
numbering must never become a replacement clinical coordinate.

## Frame-operation inventory

Paths below are relative to `endoreg_db/`, except paths explicitly prefixed
`lx-annotate:`. These are behavior and ownership contracts, not a separate
readiness checklist; assessments remain in the feature YAML linked above.

| Frame-related operation | Where PTS is required; frame operations that remain valid | Principal call sites |
| --- | --- | --- |
| Import, metadata probing and frame-row initialization | Observe decoded PTS, integer ticks, time base, order and count; initialize rows from that sequence. Never synthesize clinical coordinates from average FPS. | `services/video_storage/canonical_timelines.py`, `services/video_files/frame_coordinates.py`, `services/video_files/_frames/_initialize_frames.py` |
| Missing-timeline recovery | Decode the active canonical artifact; preserve existing annotated identities and reject partial/conflicting coordinates. Count reconciliation is restricted by the recovery contract. | `services/video_files/timestamp_backfill.py`, `services/video_files/timeline_backfill.py` |
| Frame number ↔ time and bulk boundary lookup | Resolve against persisted PTS, including origin, lower-frame nearest ties and the exclusive terminal boundary. The terminal boundary is not a display frame. | `services/video_files/timeline.py`, `_time.py`, `timeline_index.py` |
| Previous/next frame, repeated steps and N-frame skipping | Count destination frames, then use their PTS. Refill bounded neighborhoods when needed; never extrapolate outside them from FPS. | `views/video/video_timeline.py`; `lx-annotate: frontend/src/stores/videoStore.ts`, `frontend/src/utils/segmentTimeline.ts`, `frontend/src/components/VideoExamination/Timeline.vue` |
| Playback seeks, time skips and jumpmarks | Preserve the mapping between the player's clock and canonical media PTS, including nonzero origins and HLS offsets. Seek a returned frame timestamp for exact-frame navigation. | `lx-annotate: frontend/src/components/VideoExamination/VideoExaminationAnnotation.vue`, `Timeline.vue`, `segmentNavigation.ts` |
| Segment create, resize, drag, copy/paste, bulk edit and validation | Submit timestamp boundaries and adopt canonical backend responses. Preserve half-open stored frame ranges `[start, end)`; export the last included frame as `end - 1`. | `services/annotations/segments_bulk_mutation.py`, `segment_sync.py`, `serializers/label_video_segment/label_video_segment.py`; `lx-annotate: frontend/src/utils/segmentTimeline.ts`, `frontend/src/stores/videoStore.ts` |
| Segment-to-frame labels, overlap buckets and retention membership | Ordinal range queries remain valid after PTS boundary resolution on the same generation. Do not rederive boundaries from FPS or rewrite frame IDs. | `services/annotations/segment_frame_annotations.py`, `services/datasets/frame_buckets.py`, `services/frames/frame_segment_reconciliation.py`, `frame_retention.py` |
| Single-frame previews, bounding boxes and streamed training images | Resolve the selected frame to its canonical PTS before decoding; labels and boxes stay attached to the selected frame row. Raw review and processed training remain distinct. | `utils/frame_stream.py`, `views/media/frame_media.py`, `views/video/ai/frame_box_annotations.py`, `services/frames/training_images.py` |
| Annotation tasks, random/balanced sampling and skip-task actions | Select/exclude stable frame IDs and decode their PTS. Skipping a task does not remove a video frame or alter its coordinates. Random frame sampling need not become uniform-time sampling. | `services/annotations/sampling.py`, `workflow.py`, `views/video/ai/frame_annotations.py`, `services/datasets/active_learning.py` |
| Inference batches, partial runs and temporal postprocessing | Carry observed frame numbers and PTS alongside scores; smoothing, minimum duration and gap closure use presentation-time windows. Retain the observed exclusive end after partial inference. | `services/video_files/_ai.py`, `temporal_inference.py`, `frame_coordinates.py` |
| Optical character recognition samples and retired cached inference | Select raw canonical ordinals by stride, then decode their exact generation-bound ticks losslessly. Cached inference remains explicitly rejected; cached list positions cannot become clinical coordinates. | `services/video_files/_ai.py:_extract_text_from_video_frames`, `_resolve_frame_source_mode`, `utils/frame_stream.py:read_video_file_frame_sample` |
| Contiguous, sparse and every-Nth-frame image export | Resolve selected rows to exact processed-stream ticks; sparse extraction uses `select=eq(pts, ...)` and passthrough timing. Preserve primary-key filenames and reject missing requested images. | `export/frames/export_frames_with_labels.py`, `utils/video/frame_extraction.py:extract_frames_by_presentation_timestamp`, `services/frames/materialize_training_frames.py` |
| Export tables, manifests and browser/job downloads | Carry exact ticks, rational time base, seconds, timeline version and processed-artifact hash. Keep export indices separate from canonical frame numbers; lock the selected generation. | `export/frames/export_frames_with_labels.py`, `services/media/export_downloads.py`, `frame_annotation_export_jobs.py`; `lx-annotate: frontend/src/components/VideoExamination/annotationExport.ts` |
| Report image selection and document rendering | Select a persisted processed frame and decode the same PTS; reject disagreement with the selected row. Ordinal preference ranking remains valid. | `services/reports/frame_selection.py`, `frame_export.py` |
| Anonymization, blackening, frame removal, reimport and normalization | Preserve retained timestamps and time gaps; bind output indices to observed source ancestry. Intentional resampling creates a new sequence only under its explicit pre-annotation workflow. | `services/video_files/post_validation_blackening.py`, `services/video_storage/processed_timelines.py`, `frame_provenance.py`, `services/jobs/video_fps_normalization_jobs.py` |
| Post-validation pixel/quality samples | Decode the intended processed frame through the shared exact-tick lossless reader; pixel checks do not perform independent OpenCV index seeking or introduce JPEG loss. | `services/jobs/video_post_validation_jobs.py:_capture_frame`, `utils/frame_stream.py:read_video_file_frame_sample` |
| HLS playback derivatives and generation replacement | Validate complete relative PTS correspondence to the canonical source; rebuilding HLS cannot redefine annotations or create clinical frame identities. Invalidate navigation/image caches when the source changes. | `services/streaming/hls_media.py`, `services/video_storage/processed_timelines.py`; `lx-annotate: frontend/src/stores/videoStore.ts` |
| Hub, ledger and dataset segment transfer | Export exact first/last included processed-frame ticks and generation identity; compose original-recording indices through provenance, never nearest-time inference. | `services/video_files/segment_coordinates.py`, `clinical_contracts.py`, `services/datasets/exports.py`, `services/hub/transfers.py` |

## Review limits and remaining compatibility boundaries

The lx-annotate frame-step control, store and timestamp adapter now share strict
neighborhood validation and offset selection. The store requests a fresh
neighborhood for every operation instead of retaining coordinates across an
unobserved same-video generation replacement.

Browser playback uses `HTMLVideoElement.currentTime` directly. Backend tests
for nonzero origins do not prove the browser/HLS clock mapping; verify displayed
pixels and step destinations in an actual player with an offset VFR fixture.
The current tests cover adjacent steps, bounded helper offsets and sparse export,
not a configurable N-frame control or an every-Nth-frame export option.

The canonical decoder now shares exact tick selection for JPEG and lossless
images. Selected processed Frame rows use `read_processed_frame_jpeg`, which
checks the stored ordinal, tick and seconds against that generation before
decoding. Clinical inference selects its annotation artifact centrally and
checks its generation again under the publication lock. Optical character
recognition deliberately uses retained raw coordinates; it does not create
clinical frame labels.

`utils/video/frame_extraction.py:assemble_video_from_frames` writes at a fixed
FPS without source timestamps; repository references are compatibility exports,
not a canonical timeline-preserving workflow. It must not be introduced into
annotation-preserving reconstruction without a timestamp/ancestry contract.

## Central helper boundaries

`utils/video/timeline.py` owns exact rational tick conversion,
microsecond serialization consistency, display versus terminal bounds and
observed adjacent-frame durations; `services/video_files/timeline.py` preserves
the existing service imports without duplicating those rules. `_time.py` owns validated source evidence,
read-only optional lookup and canonical mapping construction; absent-coordinate
recovery is reserved for persisted videos with an available canonical artifact.
`frame_coordinates.py` binds complete artifact sequences and selected row
coordinates to the authenticated source, selects the annotation artifact and
resolves complete prediction boundaries. `utils/frame_stream.py` owns pixel
decoding. Export, hub receipt application, training, reports, comparisons and
clinical inference use these helpers at their respective input boundaries.

Native boundary resolution mirrors the scalar helper invariants. Provenance
ancestry retains exact rational/cross-product calculations; storage remapping
retains its explicit nearest-boundary tolerance. Ordinal counting, range
membership and frame-ID sampling remain ordinal operations.

## Backend ownership

Paths in this table are relative to `endoreg_db/`.

| Owner | Responsibility |
| --- | --- |
| `services/video_files/timeline.py` | Resolve frame/time boundaries, preferring persisted PTS; nearest persisted ties select the lower frame number, while constant-rate fallback uses half-up rounding. Terminal boundaries use timeline origin plus duration. |
| `services/video_files/_time.py`, `services/video_files/metadata.py` | Load persisted neighboring coordinates and delegate to the shared resolver. |
| `services/video_files/timeline_index.py` | Resolve many boundaries of one video generation per request through the native `TimelineIndex`, which applies the same rules after one metadata validation and one frame query. Segment views and serializers use it. |
| `services/video_files/segment_coordinates.py` | Bind segment frames to exact integer ticks on the processed generation and, with a verified provenance bundle, to original-recording frames composed natively from `source_frame_indices`. Unresolved receipts report an explicit unresolved mapping. |
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
| `services/frames/materialize_training_frames.py`, `management/commands/export_frame_annot.py` | Legacy `transcode_fps` remains accepted at the compatibility boundary; annotation export preserves source identity independently of that field. |

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
| `tests/utils/video/test_ffmpeg_wrapper.py::test_real_presentation_timestamp_extraction_matches_decoded_frames` | Real sparse variable-rate extraction across a rate transition, including nonzero container origins, compared to decoded pixel identities |
| `tests/views/video/test_video_timeline_view.py` | Persisted timestamp neighborhoods, invalid/missing coordinates and scoped access |
| `lx-annotate: frontend/src/utils/__tests__/segmentTimeline.test.ts` | Adjacent-frame cache reuse, irregular timestamps and canonical mutation responses |
| `lx-annotate: frontend/src/components/VideoExamination/__tests__/Timeline.pts-navigation.test.ts` | Frame-step control seeks the backend's timestamp |
| `lx-annotate: frontend/src/components/VideoExamination/__tests__/ExportAnnotations.pts-contract.test.ts` | Export requests preserve frame identity without FPS resampling |
| `tests/import_files/test_video_import_normalization.py` | Import normalization boundary |

The feature tracker owns executable release gates, incident evidence and
remaining acceptance work.
