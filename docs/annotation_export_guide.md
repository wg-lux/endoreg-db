# Annotation Export Guide

This guide documents the current supported path for turning endoreg-db frame and
segment annotations into training data for image classification models, including
GastroNet-style multi-label frame models.

The recommended export unit is:

- one CSV or JSON annotation table
- optional copied frame images under the export directory
- optional copied anonymized processed videos for local audit only

## Safety Boundary

Export only anonymized processed media. Raw media export is prohibited.

`export_videos=true` is restricted to validated processed/anonymized artifacts;
the exporter must never fall back to raw media. Unvalidated, unavailable, failed,
or lost media fail closed. For most model-training runs, prefer
`export_frames=true` and `export_videos=false`.

API exports require authentication. Annotation data and anonymized processed
exports may span centers because annotation datasets can combine material from
multiple sites. Raw-video viewing remains independently center-scoped. In local
study-server mode, callers must still select exactly one center unless a staff
user explicitly requests all centers; that is an explicit export selection
rule, not an annotation ownership boundary.

## Data Model

- **Frame**: one database row per video frame, with `id`, `frame_number`,
  `timestamp`, and `relative_path`.
- **LabelVideoSegment**: a labeled video time/frame range created by a user or
  prediction workflow.
- **ImageClassificationAnnotation (ICA)**: the frame-level label row consumed by
  the export pipeline and image training code.
- **FrameBoxAnnotation**: a rectangular frame region with validated image bounds;
  it is managed by the annotation UI but is not part of the classification CSV.
- **Information source**: annotation origin. Manual user labels normally use
  `manual_annotation`; prediction-derived labels can use
  `prediction_annotation`.
- **Annotator**: reviewer-track identifier. Ordinary interactive writes are bound
  to the authenticated username; privileged overrides remain explicit tracks.
- **Export flags**:
  - `video_file.export_segments_by_video`: include all exportable segments for a
    video.
  - `label_video_segment.export_segment`: include selected segments only.

## Supported Workflow

1. Ingest and process videos so `Frame` rows exist.
2. Create or import `LabelVideoSegment` rows through the media segment API.
3. Ensure segment labels have matching `ImageClassificationAnnotation` rows.
4. Export the annotation table and optional frame images.
5. Train from the exported table and frame paths, or train inside endoreg-db from
   an `AIDataSet`.

Segment create/update/delete already keeps ICA rows in sync for the current API
flow. For older data or imported segments, run the idempotent ensure step before
export.

## EndoregDB Callers

For an endoreg-db caller, prefer the in-process exporter instead of shelling out:

```python
from pathlib import Path

from endoreg_db.export.frames.export_frames_with_labels import (
    annotation_exporter_client,
    export_config,
)

config = export_config(
    output_dir=Path("/data/endoreg-training/gastronet_run_001"),
    output_path="annotations.csv",
    output_format="csv",
    load_base_data=True,
    use_export_flags=True,
    information_source_name="manual_annotation",
    export_frames=True,
    export_videos=False,
    transcode_frames=False,
    use_frame_pk_paths=False,
    only_validated=True,
)

result = annotation_exporter_client().run_export(config)

annotation_table = result.output_path
frame_dir = result.frame_output_dir

```

## Ensure Segment Annotations

CLI examples:

```bash
devenv shell -- python manage.py ensure_segment_annotations --video-id 42
```

```bash
devenv shell -- python manage.py ensure_segment_annotations --all-videos
```

Dry run:

```bash
devenv shell -- python manage.py ensure_segment_annotations \
  --video-id 42 \
  --dry-run
```

API examples:

```http
POST /api/media/videos/42/ensure-segment-annotations/
```

```json
{
  "segment_ids": [101, 102, 103],
  "information_source_name": "manual_annotation"
}
```

For prediction segments, use:

- `POST /api/media/videos/<video_id>/ensure-prediction-segment-annotations/`
- `POST /api/media/videos/ensure-prediction-segment-annotations/`

## Recommended YAML Export

Prefer YAML configs for reproducible training exports.

```yaml
output_dir: /data/endoreg-training/gastronet_run_001
output_path: annotations.csv
output_format: csv
load_base_data: true

use_export_flags: true
information_source_name: manual_annotation

export_frames: true
export_videos: false
transcode_frames: false
use_frame_pk_paths: false
```

Run it with:

```bash
devenv shell -- python manage.py export_frame_annot \
  --config /data/endoreg-training/gastronet_run_001/export.yaml
```

`load_base_data: true` calls `load_base_db_data()` before export. Use it when the
target environment may not already have the base labels and information sources.

## Output Layout

With `output_dir: /data/endoreg-training/gastronet_run_001` and
`output_path: annotations.csv`, the exporter writes:

```text
/data/endoreg-training/gastronet_run_001/
  annotations.csv
  frames/
    video_<video_id>/
      frame_0000000.jpg
      frame_0000001.jpg
```

The annotation table columns are:

- `annotation_id`, `video_id`, `video_hash`
- `frame_id`, `frame_number`, `frame_relative_path`, `frame_timestamp`
- `label_id`, `label_name`, `value`, `float_value`
- `annotator`, `information_source_id`, `information_source_name`
- `model_meta_id`, `date_created`, `date_modified`

Training code should resolve image files as:

```python
image_path = export_dir / "frames" / f"video_{video_id}" / frame_relative_path
```

## Frame Extraction Modes

Use existing extracted frames when possible:

```yaml
export_frames: true
transcode_frames: false
use_frame_pk_paths: false
```

This keeps `frame_relative_path` aligned with the stable frame contract:
`frame_{frame_number:07d}.jpg`. See `docs/video_frame_extraction_contract.md`.

Use transcoding when frame image files must be created from the active video for
the exported annotations:

```yaml
export_frames: true
transcode_frames: true
transcode_fps: 50
transcode_quality: 2
transcode_ext: jpg
transcode_overwrite: false
```

When `transcode_frames=true`, `use_frame_pk_paths` defaults to true. The exported
table then references frame primary-key filenames such as `frame_12345.jpg`,
which are copied under `frames/video_<video_id>/`.

## Filtering What Gets Exported

Export selected segments:

```yaml
output_dir: /data/endoreg-training/selected_segments
output_path: annotations.json
output_format: json
segment_ids: [101, 102, 103]
export_frames: true
export_videos: false
```

Export all flagged segments:

```yaml
output_dir: /data/endoreg-training/flagged_segments
output_path: annotations.csv
output_format: csv
use_export_flags: true
export_frames: true
export_videos: false
```

Additional filters:

- `video_id`
- `label_id`
- `information_source_name`
- `only_true`; omit it for multi-label training when explicit negative
  annotations should be preserved
- `limit`

## API Export

Endpoint:

```http
POST /api/media/videos/export-annotated/
```

Recommended payload for model training:

```json
{
  "output_dir": "/data/endoreg-training/gastronet_run_001",
  "output_path": "annotations.csv",
  "output_format": "csv",
  "use_export_flags": true,
  "information_source_name": "manual_annotation",
  "export_frames": true,
  "export_videos": false,
  "transcode_frames": false,
  "use_frame_pk_paths": false
}
```

Response:

```json
{
  "success": true,
  "output_path": "/data/endoreg-training/gastronet_run_001/annotations.csv",
  "row_count": 12000,
  "exported_video_count": 0,
  "exported_frame_count": 12000,
  "video_output_dir": null,
  "frame_output_dir": "/data/endoreg-training/gastronet_run_001/frames"
}
```

If the API request omits `config_path`, the current implementation defaults to
`export_frames=true`, `export_videos=true`, and `use_export_flags=true`. Set
`export_videos=false` explicitly unless copied videos are required and confirmed
to be anonymized.

## Segment API Inputs

Create or update a segment. ICA is generated automatically:

```http
POST /api/media/videos/<video_id>/segments/
PATCH /api/media/videos/<video_id>/segments/<segment_id>/
```

Example:

```json
{
  "label_id": 12,
  "start_time": 1.2,
  "end_time": 3.8,
  "export_segment": true
}
```

Toggle the per-video export flag:

```http
PATCH /api/media/videos/<video_id>/
```

```json
{
  "export_segments_by_video": true
}
```

## Frame Annotation API

Manual frame-level annotation uses:

- `GET /api/media/annotations/frames/random-task/`
- `POST /api/media/annotations/frames/bulk-upsert/`
- `POST /api/media/annotations/frames/skip/`

Random-task responses include `frame_stream_path`, so clients can show a frame
without reading storage directly. Missing single-frame files are recreated by
on-demand range extraction through the stream endpoint.

Bulk upsert accepts either `label_id` or `choice_name`:

```json
{
  "video_id": 42,
  "annotations": [
    {
      "frame_id": 9001,
      "choice_name": "visible_vessel: present",
      "information_source_name": "manual_annotation",
      "annotator": "alice"
    }
  ]
}
```

## Training Usage

External PyTorch or TensorFlow training should consume the exported CSV/JSON and
resolve image paths from `output_dir/frames/video_<video_id>/`.

For in-process endoreg-db training, attach persisted annotations to an
`AIDataSet` and use the existing image multi-label loader:

- `AIDataSet.dataset_type = "image"`
- `AIDataSet.ai_model_type = "image_multilabel_classification"`
- `AIDataSet.image_annotations` contains the `ImageClassificationAnnotation`
  rows selected for training.

`endoreg_db.utils.ai.data_loader_for_model_training.build_dataset_for_training`
builds aligned `image_paths`, `label_vectors`, `label_masks`, `labels`,
`frame_ids`, and `old_examination_ids`. The GastroNet trainer consumes that
structure through `EndoMultiLabelDataset`.

## Notes and Caveats

- ICA does not currently have a direct foreign key to `LabelVideoSegment`.
  Segment filtering matches by frame range, label, information source, and model
  metadata.
- Overlapping segments with identical label/source/model metadata can collide in
  ICA matching.
- Short segments are supported; ICA is created for any valid frame range.
- Do not read `STORAGE_DIR` directly from external consumers. Use the API or
  controlled exports so access checks, stable frame paths, and protected-media
  path validation remain in force.
