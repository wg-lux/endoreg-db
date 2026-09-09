# Annotation Source Scope For Image Multilabel Training

`annotation_source_scope` controls which annotation source classes are used for a
single image-multilabel training run. The selected `AIDataSet` remains the
canonical training boundary in every mode.

## Allowed Values

- `all`: use frame annotations and segment annotations attached to the selected
  `AIDataSet`.
- `frame_only`: use only `AIDataSet.image_annotations`.
- `segment_only`: use only `AIDataSet.video_annotations`.

The default is `all` when the field is omitted or blank.

## Dataset Builder Contract

The shared builder is
`endoreg_db.utils.ai.multilabel_dataset_builder.build_dataset_for_training`.

For image-multilabel datasets it returns aligned lists:

- `image_paths`
- `label_vectors`
- `label_masks`
- `labels`
- `labelset`
- `frame_ids`
- `video_ids`

Segment annotations are expanded in memory to existing `Frame` rows whose
`frame_number` is inside the half-open interval
`[start_frame_number, end_frame_number)`. This expansion never creates
`ImageClassificationAnnotation` rows.

If a selected scope has annotations but no matching frame samples, the builder
fails loudly with `ValueError`.

## Training Run API

`POST /api/settings/application/model_training/runs/` accepts
`annotation_source_scope` for image-multilabel runs:

```json
{
  "dataset_id": 123,
  "backbone_name": "resnet50_imagenet",
  "feature_mode": "freeze_backbone",
  "epochs": 3,
  "batch_size": 8,
  "labelset_version": 2,
  "annotation_source_scope": "frame_only"
}
```

Invalid values return HTTP 400 with a field error under
`errors.annotation_source_scope`.

Training run create, detail, and list payloads include
`annotation_source_scope` for image-multilabel runs so operators can see what
was trained.

## Management Commands

Both commands accept the same values:

```bash
python manage.py train_image_multilabel_model \
  --dataset-id 123 \
  --annotation-source-scope segment_only

python manage.py model_input \
  --dataset-id 123 \
  --annotation-source-scope frame_only
```

The selected value is passed through `TrainingConfig` and into
`build_dataset_for_training`.

## Frame Materialization

Before training starts, missing frame image files are materialized only for the
selected source scope:

- `all`: materialize missing frame files referenced by frame annotations and
  segment-expanded frames.
- `frame_only`: materialize only missing frame-annotation files.
- `segment_only`: materialize only missing segment-expanded frame files.

Materialization extracts from processed video only after the video readiness
flags are present. Raw media export is not part of this flow.
