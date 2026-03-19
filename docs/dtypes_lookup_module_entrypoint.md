# Dtypes Lookup Module Entrypoint

Last updated: 2026-03-19

## Purpose
This is the fastest entrypoint for understanding the dtypes-backed lookup module:
- where contracts live
- how `DataDict` types move through the API lifecycle
- where to implement changes safely

This is a maintainer document.

It describes lookup contracts and runtime behavior for engineers. It does not
mean that raw report-template YAML is suitable for non-technical self-service
editing.

## Read Order
1. `endoreg_db/schemas/lookup_state.py`
2. `endoreg_db/views/requirement/lookup.py`
3. `endoreg_db/services/lookup_service.py`
4. `endoreg_db/services/dtypes_requirement_service.py`
5. `endoreg_db/services/lookup_store.py`

## Module Boundaries
- `schemas/lookup_state.py`
  - Repo-local lookup-state contract for cache/session payloads.
  - Owns pydantic models + validators + typed dicts (`LookupStateDataDict`, `LookupDerivedUpdatesDataDict`, `LookupRecomputeResponseDataDict`).
  - No longer imports `LookupState` from `lx_dtypes`.
- `views/requirement/lookup.py`
  - REST lifecycle and user-facing error payloads.
  - Enforces contract `init -> all/parts -> recompute`.
  - Supports token-less recompute via `patient_examination_id`.
- `services/lookup_service.py`
  - Orchestrates loading PE state, runtime source selection, recompute, and cache updates.
  - Keeps typed payloads as `DataDict` through recompute/store paths.
- `services/dtypes_requirement_service.py`
  - Evaluates dtypes validators (`findings_validator`, `classification_validator`, `intervention_validator`, `unit_validator`, `examination_validator`) and builds normalized lookup payloads.
  - Resolves the effective knowledge base from persisted `PatientExamination.knowledge_base_module` / `knowledge_base_version` when available.
- `services/lookup_store.py`
  - Cache persistence + normalization + schema validation on writes.

## Historical KB Resolution
Current state:

- `PatientExamination` now persists `knowledge_base_module` and `knowledge_base_version`.
- New records are stamped on save from configured defaults when those fields are still empty.
- `lx_dtypes` runtime validation also accepts `knowledge_base_module` / `knowledge_base_version` on typed `PExamination` payloads.
- `endoreg_db/services/dtypes_requirement_service.py` resolves the effective KB in this order:
  - persisted `PatientExamination` identity
  - configured defaults from settings
  - unversioned current module data only when no version pin exists
- Pinned historical versions fail closed if they are not provisioned locally.

Operational meaning:

- a historical record is no longer implicitly re-evaluated against whichever KB version is live today
- the record must point to an explicitly available `(module, version)` pair
- missing historical artifacts are now an integration/deployment issue, not something the evaluator guesses around

## Resolver Boundary
Version-aware KB loading now lives in:

- `lx-data-models/lx_dtypes/models/interface/KnowledgeBaseResolver.py`

The resolver supports:

- current-version loading from normal package/repo data roots
- version-pinned loading from a registry file exposed through `LX_DTYPES_KB_REGISTRY`
- process-local caching by `(module, version)`
- specific exceptions for:
  - malformed registry: `KnowledgeBaseRegistryError`
  - unavailable version: `KnowledgeBaseVersionNotFoundError`

Expected registry shape:

```json
{
  "modules": {
    "report_template_examples": {
      "0.1.0": "/nix/store/.../lx_dtypes/data",
      "0.2.0": {
        "input_dirs": [
          "/nix/store/.../lx_dtypes/data"
        ]
      }
    }
  }
}
```

## DataDict Primer
Source of truth: `endoreg_db/schemas/lookup_state.py`

### `LookupStateDataDict`
Full cache/session state (init + user selections + derived guidance).

Typical fields:
- identity: `patient_examination_id`
- static context: `requirement_sets`, `available_findings`, `required_findings`
- user input: `selected_requirement_set_ids`, `selected_choices`
- derived guidance: `requirements_by_set`, `requirement_status`, `requirement_set_status`, `suggested_actions`
- recommendation metadata: `candidate_requirement_set_ids`, `candidate_requirement_set_confidence`

### `LookupDerivedUpdatesDataDict`
Derived-only payload returned by recompute and persisted back to cache.

Contains:
- `requirements_by_set`
- `requirement_status`
- `requirement_set_status`
- `requirement_defaults`
- `classification_choices`
- `suggested_actions`
- `candidate_requirement_set_ids`
- `candidate_requirement_set_confidence`

### `LookupRecomputeResponseDataDict`
HTTP response payload for recompute endpoints:
- `ok`
- `token`
- `updates` (`LookupDerivedUpdatesDataDict`)

### Why this matters
- `DataDict` types keep payload keys explicit and stable for frontend consumers.
- Mypy catches accidental widening to generic `dict[str, Any]` where typed dict contracts are expected.
- Validation functions (`validate_lookup_state`, `validate_lookup_updates`, `build_lookup_recompute_response`) enforce contract correctness at runtime.

Boundary note:

- `lx-data-models` no longer owns the lookup session-state schema.
- `lx-data-models` still owns validator contracts, KB loading, and requirement-evaluation request/response contracts.
- `endoreg_db` now owns the lookup cache/session contract because that state is an application workflow concern rather than a shared medical terminology contract.

## Lifecycle Contract (Implemented)
`init -> all/parts -> recompute`

### 1) `POST /api/lookup/init/`
- Validates `LookupInitRequest`.
- Builds initial state from current `PatientExamination`.
- Stores validated state in `LookupStore`.
- Returns `{ "token": "..." }`.

### 2) `GET /api/lookup/{token}/all/` and `GET|PATCH /api/lookup/{token}/parts/`
- `all`: returns validated `LookupStateDataDict`.
- `parts GET`: returns requested keys validated through `validate_lookup_parts_response`.
- `parts PATCH`:
  - Validates `LookupPartsPatchRequest`.
  - Writes normalized keys.
  - Triggers recompute when input keys changed (`patient_examination_id`, `selected_requirement_set_ids`, `selected_choices`).

### 3) `POST /api/lookup/{token}/recompute/`
- Runs `lookup_service.recompute_lookup(token)`.
- Validates derived payload as `LookupDerivedUpdatesDataDict`.
- Returns typed recompute response via `build_lookup_recompute_response`.

### Token-less recompute
`POST /api/lookup/recompute/` with `patient_examination_id`
- Backend creates a fresh session token server-side.
- Runs normal recompute flow.
- Returns standard recompute response including new `token`.

## Runtime Source Modes
Configured in settings:
- `LOOKUP_REQUIREMENT_SOURCE=dtypes` (default, primary path)
- `LOOKUP_REQUIREMENT_SOURCE=hybrid_compare` (run both; dtypes primary + divergence logs)
- `LOOKUP_REQUIREMENT_SOURCE=legacy_db` (compatibility mode)
- `LOOKUP_DTYPES_MODULE_NAME=...` (default module when no persisted module pin exists)
- `LOOKUP_DTYPES_MODULE_VERSION=...` (optional default version pin for new/unpinned records)
- `LX_DTYPES_KB_REGISTRY=/path/to/kb_registry.json` (required for pinned historical version resolution)
- `LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=false` (default; explicit emergency fallback only)

API/runtime behavior:

- `lx_dtypes` runtime validation endpoints still take `module_name` in the route for compatibility
- payload `knowledge_base_module` is authoritative when present
- route/payload module mismatch returns HTTP `409`
- payload `knowledge_base_version` triggers version-aware loading
- unavailable historical versions return HTTP `409`
- semantic admissibility failures still return HTTP `422`

`endoreg_db` behavior:

- `try_build_dtypes_requirement_guidance(...)` raises `DtypesKnowledgeBaseResolutionError` when a pinned version is missing or the registry is malformed
- `/api/evaluate-requirements/` currently returns a failed evaluation response in that case
- report persistence currently catches the exception and degrades to an advisory warning so clinician workflow is not blocked

## Where To Extend Safely
- Add/adjust lookup payload keys in `endoreg_db/schemas/lookup_state.py`.
- Thread the new key through:
  - dtypes builder (`dtypes_requirement_service.py`)
  - recompute orchestration (`lookup_service.py`)
  - endpoint response shaping (`views/requirement/lookup.py`) only if needed
- Add/update tests in:
  - `tests/services/test_dtypes_requirement_engine.py`
  - `tests/services/test_lookup_service_requirement_source.py`
  - `tests/views/requirement/test_lookup_viewset.py`

## Legacy Boundary
Use this rule:
- Active logic: `views/requirement/*`, `services/lookup_*`, `services/dtypes_requirement_service.py`, `schemas/lookup_state.py`
- Legacy compatibility only: `models/requirement/*`, optional legacy data loader paths

Canonical boundary doc:
- `docs/lookup_legacy_status.md`

## Remaining Integration Work
Python-side resolver logic is in place. The remaining work is integration and deployment:

- generate and ship a build-time KB registry file from Nix or another immutable release process
- expose that file path through `LX_DTYPES_KB_REGISTRY`
- provision the historical KB revisions that persisted `PatientExamination` rows refer to
- backfill `knowledge_base_module` / `knowledge_base_version` on older rows where those fields are blank
- propagate KB identity in client-side `PExamination` payloads when calling historical runtime validation endpoints directly
- decide whether report persistence should keep advisory degradation or become hard-fail once infrastructure is fully provisioned
