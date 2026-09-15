# Dataloader Layers And Naming

> Status tracking was migrated to `feature-tracking/DataLoading.yml`. This
> document defines naming and migration context, not an independent completion
> status.

## Why Two Modules Exist
There are currently two similarly named modules with different responsibilities:

- `endoreg_db.utils.dataloader`
  - Core YAML-to-model loading engine.
  - Handles parsing, FK/M2M resolution, create/update logic, retry-on-lock behavior, and warning logging.
  - Used by management commands such as `load_*_data`.

- `endoreg_db.helpers.data_loader`
  - Convenience orchestration wrappers around Django `call_command(...)`.
  - Defines bootstrap flows such as `load_base_db_data()` and `load_data()`.
  - Used by app bootstrap/test/export entry points that need to trigger command sequences.

Short version: one module implements loading behavior; the other triggers load commands.

## Naming Problem
Both names read like they do the same thing (`dataloader` vs `data_loader`) and differ mainly by underscore and package path. This increases onboarding cost and import mistakes.

## Canonical Naming Schema
Use names that encode layer and responsibility directly.

### Layer 1: Engine (YAML -> ORM)
- Legacy path: `endoreg_db.utils.dataloader`
- Canonical path: `endoreg_db.utils.yaml_model_loader`

Function naming guideline:
- `load_model_data_from_yaml` can stay as-is (already explicit).
- Internal helpers should retain focused names (`resolve_foreign_keys`, `write_loader_warning`, etc.) as they evolve.

### Layer 2: Orchestration (command sequencing)
- Legacy path: `endoreg_db.helpers.data_loader`
- Canonical path: `endoreg_db.helpers.data_load_orchestrator`

Function naming guideline:
- Keep command wrappers in `load_*_data` snake_case.
- Keep flow entry points explicit:
  - `load_base_db_data`
  - `load_all_reference_data` (preferred successor to generic `load_data`)

## Current Migration State

The non-breaking module migration has established these canonical modules:

- `endoreg_db/utils/yaml_model_loader.py`
- `endoreg_db/helpers/data_load_orchestrator.py`

Application call sites use the canonical paths. The canonical modules still
delegate to `endoreg_db.utils.dataloader` and
`endoreg_db.helpers.data_loader`, which remain the compatibility
implementations. Do not remove those legacy modules until there are no external
consumers and the deprecation window has been explicitly completed.

Compatibility rule:
- No behavior changes during rename.
- Rename only module/function symbols and import paths.

## Import Style Convention
Prefer importing from canonical modules in new code:

```python
from endoreg_db.utils.yaml_model_loader import load_model_data_from_yaml
from endoreg_db.helpers.data_load_orchestrator import load_base_db_data
```

Avoid ambiguous imports like:
- `from ... import dataloader`
- new call sites to `helpers.data_loader`

## Scope Clarification
This naming schema is for `endoreg_db` only.

There are separate dataloader utilities in other packages (for example `tests.helpers` and `lx-data-models`) that should be evaluated independently, not renamed as part of this module-level cleanup.
