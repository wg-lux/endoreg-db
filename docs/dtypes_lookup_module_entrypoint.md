# Dtypes Lookup Module Entrypoint

Last updated: 2026-03-05

## Purpose
This is the fastest entrypoint for understanding the dtypes-backed lookup module:
- where contracts live
- how `DataDict` types move through the API lifecycle
- where to implement changes safely

## Read Order
1. `endoreg_db/schemas/lookup_state.py`
2. `lx-data-models/lx_dtypes/models/knowledge_base/report_template/LookupState.py`
3. `lx-data-models/lx_dtypes/models/knowledge_base/report_template/LookupStateDataDict.py`
4. `endoreg_db/views/requirement/lookup.py`
5. `endoreg_db/services/lookup_service.py`
6. `endoreg_db/services/dtypes_requirement_service.py`
7. `endoreg_db/services/lookup_store.py`

## Module Boundaries
- `schemas/lookup_state.py`
  - Adapter boundary to `lx_dtypes` lookup contracts.
  - Re-exports pydantic models + validators + typed dicts (`LookupStateDataDict`, `LookupDerivedUpdatesDataDict`, `LookupRecomputeResponseDataDict`).
  - Prefers repo-local `lx-data-models` import path to avoid site-packages drift.
- `views/requirement/lookup.py`
  - REST lifecycle and user-facing error payloads.
  - Enforces contract `init -> all/parts -> recompute`.
  - Supports token-less recompute via `patient_examination_id`.
- `services/lookup_service.py`
  - Orchestrates loading PE state, runtime source selection, recompute, and cache updates.
  - Keeps typed payloads as `DataDict` through recompute/store paths.
- `services/dtypes_requirement_service.py`
  - Evaluates dtypes validators (`findings_validator`, `examination_validator`) and builds normalized lookup payloads.
- `services/lookup_store.py`
  - Cache persistence + normalization + schema validation on writes.

## DataDict Primer
Source of truth: `lx_dtypes .../LookupStateDataDict.py`

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
- `LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=false` (default; explicit emergency fallback only)

## Where To Extend Safely
- Add/adjust lookup payload keys in `lx_dtypes` first (`LookupState.py`, `LookupStateDataDict.py`).
- Re-export in `endoreg_db/schemas/lookup_state.py`.
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

