# Video Frame Extraction Contract

## Scope

This document describes how `VideoFile` frame extraction is expected to behave
for full pipeline runs, on-demand frame streaming, and post-validation video
rebuilds.

The key invariant is:

> A full extraction may be reused only when completeness is verified. The
> presence of any frame file, or a stale `Frame.is_extracted=True` flag, is not
> enough.

This matters because `pipe_1` depends on a complete, stable frame set before OCR,
prediction, and segment creation. A single on-demand frame in the frame directory
must not cause `pipe_1` to skip full extraction.

## Stable Frame Path Contract

Frame files and DB rows must use zero-based frame numbers:

- frame `0` -> `frame_0000000.jpg`
- frame `1` -> `frame_0000001.jpg`
- frame `n` -> `frame_{n:07d}.jpg`

The stable DB path is stored on `Frame.relative_path`. Callers outside the model
boundary should fetch frames through the `Frame` row and its stable path. They
should not infer alternate one-based names or depend on temporary extraction
output.

Full ffmpeg extraction is invoked with `-start_number 0` so emitted filenames
match DB frame numbering. Range extraction starts at the requested `start_frame`,
so it already produces names aligned with DB numbering.

## Full Extraction Reuse Rule

`VideoFile.extract_frames(overwrite=False)` may skip ffmpeg only when all of the
following are true:

- The expected frame count is known from `video.frame_count` or the associated
  `VideoState.frame_count`.
- The frame directory contains exactly the expected frame filenames for the
  range `0..expected_count-1`.
- Every expected file exists as a regular file.

If this exact check fails and any old frame files or extracted state exist, the
partial frame set is treated as inconsistent. The extractor clears the partial
frame files and DB extraction flags, then runs full extraction again.

After ffmpeg completes, full extraction normalizes output to the stable zero-based
paths and verifies the extracted frame-number set against the expected count when
the expected count is known. Missing or extra frame numbers fail loudly.

## Range Extraction Reuse Rule

`VideoFile.extract_specific_frame_range(start_frame, end_frame, overwrite=False)`
is used for on-demand frame streaming and other small range requests.

Range extraction may skip ffmpeg only when the stable files for every requested
frame exist. Stale `Frame.is_extracted=True` rows are not trusted by themselves.
If DB rows say extracted but the stable files are missing, the range is marked
unextracted, stale files in the range are removed, and the requested range is
extracted again.

Range extraction does not mark `VideoState.frames_extracted=True`, because a
range is not a complete video extraction.

## Trigger Matrix

| Trigger | Entry point | Extraction mode | Source video | Expected behavior |
| --- | --- | --- | --- | --- |
| `pipe_1` initial processing | `endoreg_db/models/media/video/pipe_1.py` -> `video_file.extract_frames(overwrite=False)` | Full | Raw | Reuse only a complete frame set. Partial/on-demand files force full re-extraction. |
| Frame stream request | `endoreg_db/views/media/frame_media.py` -> `video.extract_specific_frame_range(frame, frame + 1)` | Range | Raw | Recreate the requested stable frame file if missing. Never fall back to full extraction on the request path. |
| Outside-frame video rebuild | `VideoFile.create_video_without_outside_frames(...)` | Full | Processed | Force processed-video extraction with `overwrite=True`, censor outside frames, then reassemble. Extracted frames remain available afterward. |
| Post-validation rebuild job | `endoreg_db/services/video_post_validation_jobs.py` -> `VideoFile.create_video_without_outside_frames(...)` | Full | Processed | After rebuild, verify exact stable frame rows and files are still present. Fail if frames cannot be fetched/recreated by stable DB paths. |
| Segment CRUD post-processing | `endoreg_db/views/video/segments_crud.py` -> `dispatch_video_post_validation_rebuild(...)` | Job dispatch | Processed via job | Trigger post-validation rebuild after outside-segment changes. |
| Direct model/API use | `VideoFile.extract_frames(...)` or `VideoFile.extract_specific_frame_range(...)` | Full or range | Raw/processed depending on arguments | Must obey the same completeness and stable-path rules. |

## Pipe 1 Failure Mode This Prevents

Before the completeness check, full extraction could skip when any file existed
in the frame directory. A frame stream request could create one file such as
`frame_0000007.jpg`; later `pipe_1` would see a non-empty frame directory and
skip ffmpeg, leaving OCR and prediction with an incomplete frame set.

The current rule prevents that:

- A lone frame file does not satisfy the exact expected filename set.
- A stale `frames_extracted=True` state does not satisfy the file completeness
  check.
- Partial state is cleared before the full extraction attempt.
- `pipe_1` only proceeds after `state.frames_extracted` is true for the complete
  extraction.

## Post-Validation Frame Availability

The post-validation rebuild must leave extracted frames available after the full
annotation/rebuild flow. `video_post_validation_jobs._verify_extracted_frame_contract`
checks:

- `state.frames_extracted` is true.
- A positive expected frame count is known.
- The frame directory exists.
- DB rows exist for exactly `0..expected_count-1`.
- Every row uses `frame_{frame_number:07d}.jpg`.
- Every row is marked `is_extracted=True`.
- Every stable file exists on disk.

If any of those checks fail, the job raises a `RuntimeError`. This is intentional:
missing or unstable frame rows would break outside callers that fetch frames by
stable DB paths.

## Implementation References

- Full extraction wrapper:
  `endoreg_db/models/media/video/video_file_frames/_extract_frames.py`
- Range extraction wrapper:
  `endoreg_db/models/media/video/video_file_frames/_manage_frame_range.py`
- ffmpeg command builder:
  `endoreg_db/utils/video/ffmpeg_wrapper.py`
- Frame streaming view:
  `endoreg_db/views/media/frame_media.py`
- `pipe_1` caller:
  `endoreg_db/models/media/video/pipe_1.py`
- Post-validation rebuild:
  `endoreg_db/services/video_post_validation_jobs.py`
- Outside-frame rebuild:
  `endoreg_db/models/media/video/video_file.py`

## Maintenance Checklist

When changing frame extraction or frame streaming:

- Do not skip full extraction because the frame directory is non-empty.
- Do not trust `Frame.is_extracted=True` without verifying the file exists.
- Keep DB numbering and filenames zero-based.
- Keep range extraction from marking the whole video as fully extracted.
- Keep post-validation rebuild frames available after the rebuild completes.
- Use typed filesystem wrappers from `endoreg_db.utils.file_operations` for
  production filesystem mutations.
- Add or update tests that cover partial files, stale DB flags, and stable
  `Frame.relative_path` values.
