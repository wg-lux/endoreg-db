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
using the shared bounded YAML parser (duplicate keys and unsafe tags are rejected).
The default path is the bundled `endoreg_db/data/setup_config.yaml`; an explicit
path replaces it. Missing files, invalid YAML and contract violations raise
instead of selecting a different model through built-in defaults. The YAML is
the single source of bootstrap defaults. The API provides:

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

`SetupConfig.find_model_weights_files()` searches `get_runtime_paths().weights`
first, followed by the YAML's additional directories (`tests/assets`, `assets`,
and `model_weights` in the bundled configuration). Duplicate directories are
removed while preserving order. Environment variables in additional directories
must resolve; an unresolved variable raises. Runtime storage always comes from
`endoreg_db.utils.paths`, without a second `STORAGE_DIR` setting.

When no local weight file is found and the Hugging Face fallback is enabled,
`setup_endoreg_db` attempts `ModelMeta.setup_default_from_huggingface(...)` only
when no `ModelMeta` row exists. A failed download is printed as a warning. If no
weight file is ultimately found, setup also prints a warning and continues.
Consequently, successful command completion does not by itself prove that AI
inference is ready.

## Known implementation limits

These constraints are important when interpreting setup output:

- Missing weights and Hugging Face download failures are non-fatal in this
  command. Operators must independently verify that the selected
  `AiModel.active_meta` has an accessible weight file before enabling inference.
- `SetupConfig.get_model_specific_config()` parses model-specific YAML hints,
  but no production call site currently consumes that method. It validates JSON
  structure and projects only the model selectors and `setup_config` into their
  shared contracts; those contracts do not describe the surrounding ORM fixture.
  Invalid hints raise; a missing matching model returns `None`.

The stricter configuration boundary does not change the command's treatment of
missing weight artifacts. Setup completion still requires the operational checks
below before enabling inference.

## Focused verification

The narrow repository tests for this workflow are:

```bash
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/management/commands/test_setup_endoreg_db.py \
  tests/utils/test_setup_config.py tests/dataloader/test_dataloader.py -q
```

For an actual deployment, also verify the configured runtime storage path, the
resolved weight file, the active metadata row, inference initialization, and
the database backend. Do not use the presence of a repository fixture, a
warning-only setup run, or a source checkout of `lx_dtypes` as production
evidence.
