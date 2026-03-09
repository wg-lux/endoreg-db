# Dtypes Requirement Migration Tracker

Last updated: 2026-03-04

## Goal
Migrate requirement evaluation from legacy DB/YAML requirement graph (`Requirement`, `RequirementSet`, `RequirementOperator`) to dtypes report-template validators (`findings_validator`, `examination_validator`) while preserving current lookup/report API contracts.

Companion entrypoint:
- `docs/dtypes_lookup_module_entrypoint.md`

## Current State
- Lookup schema contract is already dtypes-backed:
  - `endoreg_db/schemas/lookup_state.py`
- Lookup/runtime now has a dtypes execution path (feature-flagged) for:
  - findings validator evaluation
  - recursive examination validator evaluation
  - normalized lookup updates payload mapping
  - files: `endoreg_db/services/dtypes_requirement_service.py`, `endoreg_db/services/lookup_service.py`
- Legacy runtime is still active as a compatibility mode:
  - `LOOKUP_REQUIREMENT_SOURCE=legacy_db` (compatibility mode only)
  - `endoreg_db/models/requirement/*`
  - `endoreg_db/management/commands/load_requirement_data.py` (opt-in)
- dtypes report-template runtime validator execution is not yet implemented upstream:
  - `lx-data-models/docs/guides/report-template-infrastructure.md` ("Not implemented yet")

## Migration Phases

### Phase 0: Safety Rails and Scaffolding
Status: completed

- [x] Add runtime source switch setting:
  - `LOOKUP_REQUIREMENT_SOURCE` = `legacy_db | dtypes | hybrid_compare`
- [x] Add dtypes module setting:
  - `LOOKUP_DTYPES_MODULE_NAME`
- [x] Add dedicated adapter scaffold:
  - `endoreg_db/services/dtypes_requirement_service.py`
- [x] Wire non-breaking hooks into lookup recompute/guidance paths with fallback to legacy DB.

### Phase 1: Dtypes Runtime Evaluator
Status: in_progress

- [x] Implement findings-validator query execution against `PatientExamination` state.
  - current supported operators:
    - `exists` / `present`
    - `not_exists` / `absent`
    - conditional rules with `condition.any`/`condition.all` + `then_requires`
    - fallback behavior for unknown operators uses deterministic existence semantics
- [x] Implement recursive examination-validator execution and cycle guards.
- [x] Produce normalized evaluation results matching lookup response keys:
  - `requirements_by_set`, `requirement_status`, `requirement_set_status`
  - `suggested_actions`, `requirement_defaults`, `classification_choices`
- [x] Harden runtime safety:
  - deterministic collision-safe lookup ID allocation independent of iteration order
  - explicit warning/exception paths instead of silent swallowing in core extractors
  - optional `LOOKUP_DTYPES_DATA_ROOT` override for less brittle data-root discovery
- [x] Align static typing with `lx_dtypes` report-template models:
  - `FindingsValidator`, `ExaminationValidator`, `ReportTemplate`, `ReportTemplateSection`, `ReportFinding`
  - use `TYPE_CHECKING`-guarded imports and protocol aliases in service layer to avoid runtime import coupling
- [x] Extend `lx_dtypes` report-template query datatypes for findings validators:
  - add typed `condition` structure (`any` / `all` / `then_requires`) in `report_template.common`
  - add strict pydantic query models (`FindingsValidatorQuery`, `FindingsValidatorCondition*`) with `extra=\"forbid\"`
  - switch `FindingsValidator.query` to pydantic query model and keep `FindingsValidatorDataDict.query` aligned to typed datadict shape
  - adapt `endoreg_db/services/dtypes_requirement_service.py` to consume pydantic query models via `model_dump` bridging (not dict-only parsing)
  - enforce canonical operator/comparator enums with compatibility aliases and migration warnings
- [ ] Expand comparator/operator support beyond current parity subset and formalize semantics with lx_dtypes owners.
- [ ] Add parity fixtures comparing legacy and dtypes outputs for representative clinical templates.

### Phase 2: Hybrid Compare
Status: completed

- [x] In `hybrid_compare`, run legacy and dtypes engines in parallel.
- [x] Emit structured divergence logs/metrics.
- [x] Add test fixtures for expected parity and known intentional differences.

### Phase 3: Endpoint Cutover
Status: completed

- [x] Switch `/api/lookup/*` recompute/guidance to dtypes engine as primary.
- [x] Switch `/api/evaluate-requirements/` to dtypes engine.
- [x] Keep legacy fallback behind explicit emergency flag only.
  - `LOOKUP_REQUIREMENT_SOURCE` default is now `dtypes`.
  - emergency fallback flag: `LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=false` (default)

### Phase 4: Legacy Retirement
Status: in_progress

- [x] Stop loading `load_requirement_data` by default in base bootstrap.
  - `load_base_db_data` now requires `--include-legacy-requirements` to load legacy graph data.
- [x] Mark DB requirement models and loaders as legacy compatibility layer.
  - `docs/lookup_legacy_status.md` is the current boundary declaration.
- [ ] Remove compatibility once consumer paths are fully migrated.

## Acceptance Criteria
- Lookup and report persistence contracts stay stable for frontend:
  - no payload key renames
  - no lifecycle behavior regressions
- Parity for core guidance paths is validated in automated tests.
- `LOOKUP_REQUIREMENT_SOURCE=dtypes` works in CI without falling back to legacy.

## Test Coverage (Current Slice)
- `tests/services/test_dtypes_requirement_engine.py`
  - template selection by examination
  - findings `exists` evaluation + suggestion generation
  - conditional validator (`any` + `then_requires`) pass/fail paths
  - recursive examination validator evaluation
  - derived lookup updates payload shape
  - selected-set filtering behavior
- `tests/services/test_lookup_service_requirement_source.py`
  - dtypes guidance short-circuit path
  - strict dtypes mode errors when dtypes payload is unavailable
  - emergency fallback behavior (`LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED`)
  - dtypes recompute short-circuit path
  - hybrid compare divergence + fallback behavior
- `tests/views/requirement/test_evaluate_requirements_view.py`
  - `/api/evaluate-requirements/` dtypes-backed response shaping and filtering
- `tests/dataloader/test_load_base_db_data_command.py`
  - legacy requirement loader opt-in (`--include-legacy-requirements`)

## Implementation Notes
- Keep all API field names in `snake_case`.
- Prefer deterministic behavior over heuristic matching during migration.
- Default mode is `dtypes`; legacy fallback is explicit and opt-in.
