# AI dataset attachment contract

The AI dataset attachment endpoint is the write boundary used by LX-Annotate to
attach selected annotations or to backfill all existing annotations into one
dataset.

## Ownership and wire format

`lx_dtypes.models.contracts.ai_dataset.AIDataSetAttachVideoContract` owns the
snake_case request shape. Despite its retained compatibility name, it covers
both a single-video selection and a bulk backfill. Bulk requests omit
`video_id`, set `include_all_annotations`, and select at least one of
`include_frame_annotations` and `include_video_annotations`. Bulk selection is
mutually exclusive with explicit video, frame-annotation, and segment IDs.

`AIDataSetAttachmentResultContract` owns the response shape. Its `video_id` is
nullable because a bulk backfill has no single video identity. IDs are positive
integers, counts are non-negative integers, booleans are not coerced, and
unknown fields are rejected. `endoreg_db` validates once at request ingress and
once when constructing the database-derived result. LX-Annotate sends camelCase
application objects through its existing Axios snake_case adapter and validates
the transformed camelCase response before returning it to components.

## Persistence and release boundary

The operation mutates only the existing `AIDataSet.image_annotations` and
`AIDataSet.video_annotations` relations inside the endpoint transaction. The
shared contract is not a second persistence store. Repeating the same request
is idempotent at the relation boundary, while returned attached counts describe
the rows selected by that request.

The source-coordinated backend tests run with
`PYTHONPATH=/home/admin/lx-data-models`. Production adoption requires publishing
the updated `lx-dtypes` contract and raising the minimum compatible version in
`endoreg_db`; that release step is intentionally tracked separately and must be
completed before the backend source change is deployed.
