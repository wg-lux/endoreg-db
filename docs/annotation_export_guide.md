# Annotation Export Guide

This guide covers how the frontend can generate exportable `ImageClassificationAnnotation` (ICA) from `LabelVideoSegment` and how to control exports using per-video and per-segment flags.

## Concepts

- **LabelVideoSegment**: Video time ranges labeled by users or predictions.
- **ImageClassificationAnnotation (ICA)**: Frame-level annotations derived from segments.
- **Information source**: The origin of annotations (frontend should use `manual_annotation`).
- **Export flags**:
  - `video_file.export_segments_by_video`: include all segments for a video.
  - `label_video_segment.export_segment`: include only selected segments.

## Behavior Summary

- **Segment create/update**: ICA is generated for the segment’s frames using the segment’s `information_source` (manual and prediction segments are both supported).
- **Segment delete**: ICA for the segment’s frame range is removed.
- **Exports**: You can filter ICA output by:
  - selected segment IDs (`segment_ids`), or
  - export flags (`use_export_flags=true`), which include all segments where:
    - `export_segment=true` OR
    - the segment’s `video_file.export_segments_by_video=true`

## Frontend Requirements

- Always set `information_source` to `manual_annotation` (the serializer already defaults to this).
- Set `export_segment=true` on segments the user explicitly selects.
- Set `export_segments_by_video=true` on the video when the user wants all segments exported.

## API

### Create/Update Segment (ICA generated automatically)

- POST `/api/media/videos/<video_id>/segments/`
- PATCH `/api/media/videos/<video_id>/segments/<segment_id>/`

Example (create):
```json
{
  "label_id": 12,
  "start_time": 1.2,
  "end_time": 3.8,
  "export_segment": true
}
```

### Toggle per-video export flag

- PATCH `/api/media/videos/<video_id>/`

Example:
```json
{
  "export_segments_by_video": true
}
```

### Export annotated data

- POST `/api/media/videos/export-annotated/`

Examples:
```json
{
  "output_dir": "/data/export/run_2026_01_27",
  "output_format": "csv",
  "use_export_flags": true
}
```

```json
{
  "output_dir": "/data/export/run_2026_01_27",
  "output_format": "json",
  "segment_ids": [101, 102, 103]
}
```

Optional parameters:
- `video_id`, `label_id`, `information_source_name`
- `export_videos`, `export_frames`
- `transcode_frames`, `transcode_fps`, `transcode_quality`, `transcode_ext`
- `use_frame_pk_paths`

## CLI

Example (export using flags):
```bash
python manage.py export_frame_annot \
  --output-dir /data/export/run_2026_01_27 \
  --output-path frames.csv \
  --use-export-flags \
  --export-frames \
  --export-videos
```

Example (export by segment IDs):
```bash
python manage.py export_frame_annot \
  --output-dir /data/export/run_2026_01_27 \
  --segment-ids 101 102 103 \
  --format json
```

## Notes and Caveats

- ICA does not currently have a direct FK to `LabelVideoSegment`. Filtering uses frame-range + label + information source + model meta.
- Overlapping segments with identical label/source/model_meta can still collide in ICA matching.
- Short segments are supported; ICA is created for any valid frame range.
