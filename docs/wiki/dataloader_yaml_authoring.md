# Dataloader YAML Authoring Guide

## Purpose
This page explains how to create YAML files that can be loaded into `endoreg_db` through the dataloader commands.

Project guidance:
- Prefer YAML config.
- Reference `load_base_db_data` for base bootstrap flows.

For module/layer naming and architecture context, see:
- `docs/wiki/dataloader_layers_and_naming.md`

## Where YAML Is Used
Many management commands load model data from YAML files via:
- canonical: `endoreg_db.utils.data_loading.yaml_model_loader.load_model_data_from_yaml`
- legacy compatibility path: `endoreg_db.utils.data_loading.dataloader.load_model_data_from_yaml`

Base bootstrap is typically triggered by:
- `load_base_db_data`
- canonical helper wrapper: `endoreg_db.helpers.data_load_orchestrator.load_base_db_data`
- legacy compatibility path: `endoreg_db.helpers.data_loader.load_base_db_data`

## Loader Behavior (Important)
The shared loader reads all `*.yaml` files in a configured directory and processes entries.

At a high level it:
- parses YAML with `yaml.safe_load`
- reads `fields` from each entry
- resolves configured foreign keys (and some many-to-many lists)
- creates or updates records (primarily keyed by `name`, with model-specific exceptions)
- logs warnings to a timestamped warning log file

## Recommended Authoring Rules
- Use `snake_case` field names.
- Keep one domain/topic per YAML file where possible.
- Prefer small, reviewable files over one giant file.
- Use stable natural-key values for referenced objects (names expected by loader).
- Avoid duplicate `name` entries in the same dataset unless intentional update behavior is desired.

## Generic YAML Entry Shape
Most loader datasets use a list of entries with a `fields` object.

Example pattern:
```yaml
- fields:
    name: example_entry
    is_active: true
    description: Example text
```

## Foreign Key and M2M References
The exact FK fields depend on the specific management command metadata.

Common pattern:
- Single FK fields are specified by natural key value (often a `name`)
- M2M fields are specified as lists of natural key values

Example (generic):
```yaml
- fields:
    name: example_requirement_set
    tags:
      - screening
      - gastro
    organ: colon
```

Notes:
- The loader only resolves FK/M2M fields that the command declares in its metadata.
- Unknown/unresolvable references are logged as warnings and may be skipped.

## Validation and Constraints
Some datasets add validators before save.

Also note:
- Translation helper fields like `name_de`, `name_en`, `description_de`, `description_en` are currently stripped by the loader in shared code (temporary compatibility behavior).

## How To Add A New YAML Dataset Safely
1. Identify the existing management command for the target model (or add one).
2. Inspect the command metadata:
   - target model
   - source directory
   - foreign keys / foreign key models
   - validators
3. Create a YAML file in the command's source directory.
4. Load only that dataset command in a dev environment first.
5. Run `load_base_db_data` if the dataset depends on base reference records.
6. Verify results in DB/admin/API.
7. Check dataloader warning logs for skipped references.

## Example Workflow (Dev)
1. Create/update YAML file under the model's configured data directory.
2. Run the specific loader command.
3. If this is a fresh environment, run base bootstrap first:
   - `load_base_db_data`
4. Re-run the target loader command.
5. Confirm records and relationships.

## Common Failure Modes

### Missing FK target records
Symptom:
- warnings logged like "Model with key X not found"

Fix:
- load prerequisite datasets first (or run `load_base_db_data`)
- verify natural key spelling in YAML

### Duplicate names causing unexpected updates
Symptom:
- existing records change instead of new rows being created

Fix:
- review `name` uniqueness and version fields
- split versioned entries clearly (where supported)

### Wrong field names / casing
Symptom:
- fields ignored or model save errors

Fix:
- use model field names in `snake_case`
- avoid camelCase in YAML

## Review Checklist Before Commit
- YAML parses cleanly.
- Fields use `snake_case`.
- References point to existing natural keys.
- Dataset can load after `load_base_db_data`.
- No unexpected warnings in dataloader log.
- Changes are limited to intended domain records.

## Notes For Future Expansion
This page is intentionally generic. Add model-specific pages as needed with:
- exact directory locations
- command names
- field-by-field examples
- known validator rules
