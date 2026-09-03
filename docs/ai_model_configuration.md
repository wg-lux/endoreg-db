# AI Model Configuration

This is the canonical description of the AI model bootstrap currently present
in `endoreg-db`. It describes implementation state, not production approval.

## Current configuration sources

The repository contains:

- `endoreg_db/data/setup_config.yaml`, with the primary model and label set,
  local weight search patterns, a Hugging Face fallback, and metadata defaults;
- `endoreg_db/data/ai_model_meta/default_multilabel_classification.yaml`, with
  one `ModelMeta` fixture and setup hints;
- YAML fixtures for model types, models, labels, and label sets under
  `endoreg_db/data/`.

The runtime API is `endoreg_db.utils.setup_config.SetupConfig`. It validates
configuration with the typed `lx_dtypes.models.contracts.setup_config` payloads
and provides:

```python
from endoreg_db.utils.setup_config import setup_config

primary_model = setup_config.get_primary_model_name()
primary_labelset = setup_config.get_primary_labelset_name()
huggingface_config = setup_config.get_huggingface_config()
weight_files = setup_config.find_model_weights_files()
defaults = setup_config.get_auto_generation_defaults()
```

## Bootstrap commands

`python manage.py load_ai_model_data` loads `ModelType`,
`VideoSegmentationLabel`, `VideoSegmentationLabelSet`, and `AiModel`. Although
`IMPORT_METADATA` contains a `ModelMeta` entry, `ModelMeta` is deliberately
absent from `IMPORT_MODELS`, so this command does **not** load model metadata.

`python manage.py setup_endoreg_db` performs the following sequence:

1. calls `load_base_db_data`;
2. creates the database cache table when a database cache backend is selected;
3. calls `load_ai_model_data` and `load_ai_model_label_data`, unless
   `--skip-ai-setup` is supplied;
4. creates primary metadata when weights are found;
5. validates active metadata and may create missing metadata;
6. runs its built-in setup verification.

The supported flags are:

```bash
python manage.py setup_endoreg_db --skip-ai-setup
python manage.py setup_endoreg_db --yaml-only
python manage.py setup_endoreg_db --force-recreate
```

`--yaml-only` prevents missing metadata from being auto-generated during the
repair pass. `--force-recreate` requests a new metadata version for the primary
model. There is no standalone `validate_ai_models` management command in this
repository; validation is currently private to `setup_endoreg_db`.

## Weight resolution

`SetupConfig.find_model_weights_files()` expands environment variables in the
configured directories and evaluates each configured glob. The current file
search order starts with `${STORAGE_DIR}/model_weights`, followed by
`tests/assets`, `assets`, and `model_weights`.

When no local weight file is found and the Hugging Face fallback is enabled,
`setup_endoreg_db` attempts `ModelMeta.setup_default_from_huggingface(...)` only
when no `ModelMeta` row exists. A failed download is printed as a warning. If no
weight file is ultimately found, setup also prints a warning and continues.
Consequently, successful command completion does not by itself prove that AI
inference is ready.

## Known implementation limits

These constraints are important when interpreting setup output:

- `SetupConfig` currently derives its default path with
  `Path(__file__).resolve().parents[2] / "data" / "setup_config.yaml"`, which
  resolves to repository-root `data/setup_config.yaml`, not the bundled
  `endoreg_db/data/setup_config.yaml`. Unless a caller supplies the path
  explicitly, the loader logs that the file is absent and uses its built-in
  defaults. Editing the bundled setup file therefore does not currently prove
  that runtime behavior changed.
- `_verify_setup()` queries SQLite's `sqlite_master` directly. The command's
  final verification is therefore SQLite-specific and is not production
  readiness evidence for a PostgreSQL deployment.
- Missing weights and Hugging Face download failures are non-fatal in this
  command. Operators must independently verify that the selected
  `AiModel.active_meta` has an accessible weight file before enabling inference.
- `SetupConfig.get_model_specific_config()` parses model-specific YAML hints,
  but no production call site currently consumes that method.

These are descriptions of the current code, not recommended end states. Fixes
belong in application code and tests and should be tracked before this document
is changed to claim stronger behavior.

## Focused verification

The narrow repository tests for this workflow are:

```bash
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/management/commands/test_setup_endoreg_db.py \
  tests/dataloader/test_dataloader.py -q
```

For an actual deployment, also verify the configured runtime storage path, the
resolved weight file, the active metadata row, inference initialization, and
the database backend. Do not use the presence of a repository fixture, a
warning-only setup run, or a source checkout of `lx_dtypes` as production
evidence.
