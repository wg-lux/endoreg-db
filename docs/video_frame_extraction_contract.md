# Video Frame Extraction Contract

Reviewed against repository code on 2026-10-15. Scope and readiness belong to
[VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml).
The [video storage runbook](video_storage_normalization.md) owns publication,
encrypted staging, media leases and cleanup gates.

## Current consumers

| Consumer | Entry point | Behavior |
| --- | --- | --- |
| Annotation image and legacy stream routes | `endoreg_db/views/media/frame_media.py` | Decode one frame in memory; processed media is the default and the legacy stream route accepts only processed media. |
| Local training | `endoreg_db/services/frames/training_images.py` | Recheck authorized frame membership and processed-media release gates on every read; decode through the shared image reader. |
| Temporal prediction | `endoreg_db/services/video_files/temporal_inference.py` | Use streaming; requesting `frame_source_mode="cache"` raises an error. |
| Outside-frame rebuild | `endoreg_db/services/video_files/post_validation_blackening.py` | Encode a staged video from the reviewed intervals; preserve existing frame rows, annotations and legacy files on failure. |
| Post-validation job | `endoreg_db/services/jobs/video_post_validation_jobs.py` | `_verify_processed_video_contract` checks the processed video and blackening samples, without creating or requiring a frame cache. |
| Explicit annotation export | `endoreg_db/export/frames/export_frames_with_labels.py` | Materialize selected frame images for export while preserving source-frame identity. |

## Frame identity and presentation timestamps

Frame numbers are zero-based within a concrete canonical generation. The
persisted presentation timestamp (PTS), timeline version and content identity
bind a frame to that generation. The [call-site inventory](video_pts_fps_callsite_inventory.md)
distinguishes current consumers from legacy rate-based decoder helpers.

`read_video_file_frame_jpeg` in `endoreg_db/utils/frame_stream.py` asks
`video.frame_number_to_s` for the authoritative timestamp before decoding.
`endoreg_db/services/video_files/timeline.py` prefers persisted timestamps and
rejects missing timestamps for variable-frame-rate media. Constant-frame-rate
fallback includes the timeline origin. Terminal segment boundaries use origin
plus duration; a terminal boundary is not a decodable frame.

For encrypted media, the image reader gives FFmpeg a short-lived authenticated
loopback byte-range source. It does not materialize a complete plaintext video
or read a legacy frame cache. Storage and decoder failures propagate. Raw
images require the separate authorized anonymization-review path; local
training accepts only approved processed media.

Timestamp-selected decoding does not itself prove which original frame entered
an encoder. Transformation ancestry is recorded separately by
`endoreg_db/services/video_storage/frame_provenance.py`, using actual decoder
indices and complete source/output PTS sequences. Nearest-timestamp clinical
boundary mapping must never be used to invent that ancestry.

## Retired materialization entry points

`extract_video_frames` and `extract_video_frame_range` in
`endoreg_db/services/video_files/frames.py` retain compatibility signatures, but
their internal extraction helpers raise `RuntimeError` explaining that frame
file materialization is export-only. The timestamp-range wrapper resolves
coordinates first and then reaches the same retired helper. It does not provide
a cache-repair workflow. Use an explicit export when image files are required.

Existing `Frame.relative_path`, extraction flags and legacy files remain
historical state. An image request or rebuild must not recreate or delete them
as a side effect.

Frame identity is the persisted presentation timestamp of each frame row, not a
file on disk. The former frame-cache diagnostics (`build_frame_cache_manifest`,
`validate_video_frame_cache`, `classify_frame_cache` and the
`reconcile_media_integrity --check-frames/--repair-frames` options) are removed;
legacy frame directories are neither validated nor repaired.

## Verification references

- `tests/utils/video/test_frame_stream.py`: shared decoder and seekable input.
- `tests/views/media/test_frame_stream.py`: image routes and source selection.
- `tests/models/video/test_frame_extraction_completeness.py`: retired
  materialization behavior.
- `tests/services/test_video_post_validation_jobs.py`: processed-video validation.
- `tests/services/test_post_validation_generation_cleanup.py`: retained state on
  rebuild failure.
- `tests/services/test_video_temporal_mapping.py`: irregular timestamps, terminal
  boundaries and constant-rate fallback.
- `tests/services/test_frame_provenance.py`: actual ancestry and invalid receipts.
