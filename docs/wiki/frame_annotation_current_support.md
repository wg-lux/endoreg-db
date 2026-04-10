# Frame Annotation: Current Supported Integration

## Scope
This page documents the currently supported frame-annotation flow between:

- frontend view: `lx-annotate/frontend/src/views/FrameAnnotation.vue`
- backend endpoints under `/api/media/annotations/frames/*`

## Supported Endpoints

### 1) Random task fetch
- `GET /api/media/annotations/frames/random-task/`

Supported query params:
- `label_group_id` (optional, integer)
- `limit` (optional, integer, default `1`)
- `task_mode` (`random` or `filtered`, default `random`)
- `target_label` (optional, label name)
- `filter_label` (or `previous_label`) required when `task_mode=filtered`
- `video_id` (optional, integer)
- `information_source_name` (optional, default `manual_annotation`)
- `annotator` (optional)
- `exclude_annotated` (optional bool, default `true`)

Response shape:
- `status`, `task`, `tasks`, `count`, `task_mode`
- optional echo fields: `label_group_id`, `target_label`, `filter_label`

### 2) Bulk upsert
- `POST /api/media/annotations/frames/bulk-upsert/`

Supported payload formats:
- list form: `[{...}, {...}]`
- object form: `{"video_id": <int>, "annotations": [{...}]}`

Each annotation item supports:
- `frame_id` (required)
- `information_source_name` (required)
- `label_id` (optional if `choice_name` is present)
- `choice_name` (optional if `label_id` is present)
- `value` (optional, default `true`)
- `float_value` (optional)
- `annotator` (optional)
- `external_annotation_id` (optional)
- `model_meta_id` (optional)

`choice_name` support:
- If `label_id` is omitted, label is resolved by `choice_name`.
- Case-insensitive label-name match is supported.
- Suffix parsing is supported:
  - `"<label>: present"` => `value=true`
  - `"<label>: absent"` => `value=false`

This is compatible with the current Vue submit format that sends `choiceName`.

### 3) Skip
- `POST /api/media/annotations/frames/skip/`

Supported payload fields:
- `frame_id` (required)
- `video_id` (optional)
- `annotator` (optional)
- `reason` (optional)
- `information_source_name` (optional, default `manual_annotation`)
- `exclude_annotated` (optional bool, default `true`)

Response:
- `status`, `skipped_frame_id`, `video_id`, `annotator`, `reason`
- optional `next_task`

## Frontend Compatibility Notes

Current frontend files:
- `frontend/src/views/FrameAnnotation.vue`
- `frontend/src/stores/annotationQueue.ts`
- `frontend/src/types/api/endpoints.ts`

The frontend currently uses:
- `random-task`
- `bulk-upsert`
- `skip`

It does not depend on Label Studio webhook routes.

## Removed Legacy Piece

The Label Studio webhook endpoint was removed from `endoreg_db`:
- removed route: `/api/media/annotations/frames/label-studio-webhook/`
- removed webhook receiver view and exports

## Neutral Defaults

Frame annotation defaults now use neutral source naming:
- default information source fallback: `manual_annotation`

No Label Studio settings keys are used in active frame-annotation routes.
