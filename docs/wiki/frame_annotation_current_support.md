# Frame Annotation: Current Supported Integration

## Scope
This page documents the currently supported frame-annotation flow between:

- frontend view: `lx-annotate/frontend/src/views/FrameAnnotation.vue`
- backend endpoints under `/api/media/annotations/frames/*`

Supported annotation records in this workflow are:

- `ImageClassificationAnnotation` for boolean or scored frame labels
- `FrameBoxAnnotation` for rectangular regions on a frame
- `LabelVideoSegment` for labeled temporal ranges
- segment-derived `ImageClassificationAnnotation` rows used by reconciliation and export

Frame and box annotations are mutable reviewer tracks. Segment validation is a
separate finalization gate: incomplete derived frame annotations are rejected,
and a video becomes final only after segment validation and required outside
frame cleanup have completed.

## Authentication, Dataset Scope, and Provenance

- Production routes require authentication and the configured write role.
- Annotation queues and datasets may contain media from multiple centers.
  Center ownership does not restrict annotation records, task selection, or
  processed annotation exports. Only raw-video viewing is center-scoped.
- The backend binds ordinary writes to the authenticated Django username.
- An explicit `annotator` override requires staff, superuser, or
  `center_scope:admin` privileges. The override selects a reviewer track; the
  authenticated actor remains available to server-side request and audit
  boundaries.
- Interactive endpoints accept manual sources only. Prediction provenance is
  created by prediction workflows, not by the frame annotation UI.
- Supported interactive sources are `annotation`, `default_annotation`,
  `frame_annotation_frontend`, `human_annotation`,
  `lx_anonymizer_evaluation`, and `manual_annotation`.

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

Annotation rows and their optional dataset attachment are committed in one
database transaction. A database failure returns `status="error"`, a stable
machine-readable `code`, `retryable`, and `write_committed=false`; the client
must keep the current task visible and may retry only when `retryable=true`.
Integrity conflicts return HTTP 409, temporary database failures return HTTP
503, and other database failures return HTTP 500. Internal database messages
are never included in the response.

The wire contract is snake_case: clients must send `choice_name`. Any frontend
camelCase-to-snake_case conversion must happen before the request reaches this
serializer.

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

### 4) Frame boxes

- `GET /api/media/annotations/frames/boxes/`
- `POST /api/media/annotations/frames/boxes/`

`GET` requires `frame_id` and returns only the authenticated or explicitly
authorized reviewer track. `POST` supports scoped replacement and validates
positive dimensions, image bounds, frame/video ownership, manual provenance,
and annotator privileges.

## Frontend Compatibility Notes

Current frontend files:
- `frontend/src/views/FrameAnnotation.vue`
- `frontend/src/stores/annotationQueue.ts`
- `frontend/src/types/api/endpoints.ts`

The frontend currently uses:
- `random-task`
- `bulk-upsert`
- `skip`
- `boxes`

The frontend sends one server-issued annotator principal consistently for task
loading, label submission, skip, box reads, and box writes. It offers an
annotator override only when the auth bootstrap explicitly returns
`can_override_annotation_principal=true`.

It does not depend on Label Studio webhook routes.

## Frame Image Availability

Frame image files are served through the media frame stream endpoint. Missing
single-frame files are recreated through on-demand range extraction; the request
path does not fall back to full video extraction.

Stable frame paths and extraction completeness rules are documented in
`docs/video_frame_extraction_contract.md`.

## Removed Legacy Piece

The Label Studio webhook endpoint was removed from `endoreg_db`:
- removed route: `/api/media/annotations/frames/label-studio-webhook/`
- removed webhook receiver view and exports

## Neutral Defaults

Frame annotation defaults now use neutral source naming:
- default information source fallback: `manual_annotation`

No Label Studio settings keys are used in active frame-annotation routes.

## Operations and Recovery

The reconciliation command is read-only by default and can emit a stable JSON
report for operational evidence:

```bash
devenv shell -- python manage.py reconcile_frame_segment_annotations --json
```

Repairs require both `--apply` and an explicit `--video-id` or `--segment-id`.
An unscoped apply fails closed. Missing segment-derived annotations are created;
only stale annotations carrying the managed segment-derived marker are deleted.
Suspicious unmarked annotations remain untouched and are reported for review.

Video post-validation exposes `queued`, `running`, `failed`, and completed
states through the segment validation status API. A dispatch or worker failure
keeps final validation flags cleared. Failed cleanup can be queued again through
the existing blacken-outside action, while duplicate active requests return
`already_queued` or `busy`. Stale pending/running histories are marked failed
before a replacement job is reserved, preserving the failed record for
diagnosis.

Before a production rehearsal, operators must ensure that migrations are
current and `CELERY_BROKER_URL` is configured. Missing schema or broker state is
an operational configuration failure and must not trigger a synchronous or
authentication fallback.
