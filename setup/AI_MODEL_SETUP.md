# AI model metadata setup

The canonical setup entry point is the Django management command below. Run
migrations before setup so the loader can access the required database tables.

```bash
uv run python manage.py migrate
uv run python manage.py setup_endoreg_db
```

`setup_endoreg_db` loads the base seed data, creates a cache table only when a
database cache backend is configured, loads artificial intelligence (AI) model
and label definitions, prepares model metadata, and runs its built-in checks.
Use `--skip-ai-setup` when AI processing is intentionally disabled,
`--force-recreate` to create a new metadata version, or `--yaml-only` to disable
automatic metadata generation.

## Model weights

The search order and filename patterns are defined in
`endoreg_db/data/setup_config.yaml`. The current configured directories are:

1. `${STORAGE_DIR}/model_weights`
2. `tests/assets`
3. `assets`
4. `model_weights`

If no local file matches, setup attempts the configured Hugging Face fallback.
If that also fails, the command warns and may leave generated metadata without
weights; that state is not ready for AI inference.

## Manual commands

Use these commands for targeted recovery or diagnosis:

```bash
uv run python manage.py load_ai_model_data
uv run python manage.py load_ai_model_label_data
uv run python manage.py create_multilabel_model_meta \
    --model_name image_multilabel_classification_colonoscopy_default \
    --model_meta_version 1 \
    --image_classification_labelset_name multilabel_classification_colonoscopy_default \
    --model_path /absolute/path/to/model.safetensors
```

The metadata command requires an existing AI model and label set and accepts
only a non-empty `.safetensors` file. On success it reports `ModelMeta ready`
with the metadata name, version, and model name.

Create the Django cache table only for a database-backed cache:

```bash
uv run python manage.py createcachetable
```

The default settings use Django's in-memory cache and do not require this
table.

## Hugging Face command

The dedicated download command is also available:

```bash
uv run python manage.py create_model_meta_from_huggingface \
    --model_name image_multilabel_classification_colonoscopy_default \
    --labelset_name multilabel_classification_colonoscopy_default \
    --meta_version 1
```

This requires network access and an available model repository on Hugging Face.

## Verification

```bash
uv run python manage.py shell -c "
from pathlib import Path
from endoreg_db.models import AiModel, ModelMeta
from endoreg_db.helpers.default_objects import get_latest_segmentation_model

print('AI models:', AiModel.objects.count())
print('Model metadata:', ModelMeta.objects.count())
model_meta = get_latest_segmentation_model()
print('Latest model metadata:', model_meta)
print('Weights exist:', bool(model_meta.weights and Path(model_meta.weights.path).is_file()))
"
```

The built-in `setup_endoreg_db` table verification currently queries
SQLite's `sqlite_master`. It is therefore not a database-independent production
readiness check and will fail on PostgreSQL until the implementation uses
Django database introspection.

## Troubleshooting

- `Model file not found`: pass `--model_path` or place weights in a directory
  and filename pattern configured by `endoreg_db/data/setup_config.yaml`.
- `No model metadata found`: run `setup_endoreg_db`, or run the model, label,
  and metadata commands above in order.
- Cache-table errors: confirm that the configured cache backend is database
  backed before running `createcachetable`.
