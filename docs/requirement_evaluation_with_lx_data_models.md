# Requirement Evaluation and `lx-data-models`

Last updated: 2026-03-16

## Purpose

This note explains how requirement evaluation works today in `endoreg_db` and how it cooperates with `lx-data-models`.

It is intentionally focused on the live runtime path:

- the API entrypoint
- the `PatientExamination` loader
- the dtypes-backed evaluator
- the typed response contracts
- the query-hardening boundary

Use this together with:

- `docs/dtypes_lookup_module_entrypoint.md`
- `docs/dtypes_requirement_migration.md`
- `docs/pydantic_django_hardening_plan.md`

## Short Version

Requirement evaluation is now a dtypes-backed flow.

At a high level:

1. the API view validates the request with `lx_dtypes` contract models
2. `endoreg_db` loads a `PatientExamination` with a prefetched object graph
3. the evaluation service reads report-template validators from `lx-data-models`
4. the service evaluates those validators against live Django state
5. the response is shaped back into stable typed payloads

The important architectural split is:

- `lx-data-models` owns the strict validator and payload contracts
- `endoreg_db` owns ORM loading, orchestration, and runtime traversal

## Primary Code Paths

Read these files in order:

1. `endoreg_db/views/requirement/evaluate.py`
2. `endoreg_db/services/lookup_service.py`
3. `endoreg_db/services/dtypes_requirement_service.py`
4. `endoreg_db/schemas/lookup_state.py`
5. `lx-data-models/lx_dtypes/models/contracts/requirement_evaluation.py`
6. `lx-data-models/lx_dtypes/models/knowledge_base/report_template/*`

## Step 1: API Contract Validation

The direct evaluation endpoint is:

- `POST /api/evaluate-requirements/`

Implementation:

- `endoreg_db/views/requirement/evaluate.py`

The request is validated using:

- `RequirementEvaluationRequest`
- `RequirementEvaluationResponse`
- `RequirementEvaluationResult`

from:

- `lx-data-models/lx_dtypes/models/contracts/requirement_evaluation.py`

This is a strict contract boundary. The view does not accept arbitrary payloads and does not expose raw Pydantic error strings anymore. Validation errors are flattened into the simpler API message format expected by the existing endpoint tests.

Example:

- missing `patient_examination_id` becomes `patient_examination_id is required`

## Step 2: Load the Django Evaluation Graph

After request validation, the view calls:

- `lookup_service.load_patient_exam_for_eval(patient_examination_id)`

Implementation:

- `endoreg_db/services/lookup_service.py`

This function is the main Django-side evaluation loader. It loads one `PatientExamination` and prefetches the relations that the downstream evaluation path actually traverses.

Current prefetch plan is organized into private bundles:

- `_indication_prefetch_bundle()`
- `_lab_value_prefetch_bundle()`
- `_finding_prefetch_bundle()`
- `_requirement_set_prefetch_bundle()`

This was split deliberately so future changes can modify one traversal domain without rewriting one monolithic `prefetch_related(...)` block.

## Step 3: Query-Hardening Boundary

The main query-hardening rule is:

- once `load_patient_exam_for_eval()` returns, the hot-path evaluation helpers should not issue additional SQL for the prefetched graph they traverse

This matters because several helpers operate behind model properties and can look in-memory while still causing ORM work.

The main properties involved are:

- `PatientExamination.links`
- `PatientFinding.active_classifications`
- `PatientFinding.active_interventions`
- `PatientFinding.links`

Relevant model files:

- `endoreg_db/models/medical/patient/patient_examination.py`
- `endoreg_db/models/medical/patient/patient_finding.py`

The current hardening changes are:

- `PatientExamination.links` now reads lab values through `self.patient.lab_values.all()` so the prefetched cache can be reused
- `PatientFinding.active_classifications` uses `_prefetched_objects_cache["classifications"]` when available
- `PatientFinding.active_interventions` uses `_prefetched_objects_cache["interventions"]` when available

Regression coverage:

- `tests/services/test_lookup_service_query_hardening.py`

That test asserts that after `load_patient_exam_for_eval()`:

- `loaded.links`
- `loaded.patient_findings.all()[0].active_classifications`
- `loaded.patient_findings.all()[0].active_interventions`

run with `0` extra SQL queries.

## Step 4: Requirement Source Selection

The evaluation flow in `lookup_service.py` supports runtime source selection:

- `legacy_db`
- `dtypes`
- `hybrid_compare`

Current default:

- `dtypes`

Selection and fallback behavior live in:

- `endoreg_db/services/dtypes_requirement_service.py`
- `endoreg_db/services/lookup_service.py`

Important settings:

- `LOOKUP_REQUIREMENT_SOURCE`
- `LOOKUP_DTYPES_MODULE_NAME`
- `LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED`

Today the `/api/evaluate-requirements/` path is dtypes-backed by default.

## Step 5: Load Validators from `lx-data-models`

The dtypes evaluation service resolves report-template data from `lx-data-models`.

Main entrypoint:

- `_load_dtypes_kb(module_name)` in `endoreg_db/services/dtypes_requirement_service.py`

That loader uses:

- `lx_dtypes.models.interface.DataLoader`

to load knowledge-base modules from:

- the repo-local `lx-data-models/lx_dtypes/data` directory when available
- otherwise the installed `lx_dtypes` package data

This is important because it keeps evaluation aligned with the checked-out repository data during development and CI.

The loaded knowledge base contains typed report-template objects such as:

- `ReportTemplate`
- `ReportTemplateSection`
- `ReportFinding`
- `FindingsValidator`
- `ExaminationValidator`

Those live in:

- `lx-data-models/lx_dtypes/models/knowledge_base/report_template/`

## Step 6: Evaluate Validators Against Django State

`endoreg_db/services/dtypes_requirement_service.py` performs runtime evaluation by combining:

- typed validator definitions from `lx-data-models`
- live Django state from the prefetched `PatientExamination`

This is the core contract split:

- `lx-data-models` defines what should be evaluated
- `endoreg_db` defines how to read actual patient/report state and satisfy those validators

In practice, the service:

- loads the configured report-template module
- selects applicable templates/validators for the current examination
- evaluates findings validators against available findings, classifications, interventions, and examination context
- evaluates examination validators recursively
- builds normalized lookup updates such as:
  - `requirements_by_set`
  - `requirement_status`
  - `requirement_set_status`
  - `requirement_defaults`
  - `classification_choices`
  - `suggested_actions`

The shape of those payloads is then validated again against the typed `lx_dtypes` lookup contracts.

## Step 7: Typed State and Response Shaping

The lookup/evaluation payloads are validated through:

- `LookupState`
- `LookupStateDataDict`
- `LookupDerivedUpdatesDataDict`

from:

- `lx-data-models/lx_dtypes/models/knowledge_base/report_template/LookupState.py`
- `lx-data-models/lx_dtypes/models/knowledge_base/report_template/LookupStateDataDict.py`

Repo-local adapter boundary:

- `endoreg_db/schemas/lookup_state.py`

This layer exists so `endoreg_db` can keep using typed `DataDict` payloads without hardcoding the schema locally.

For the direct evaluation endpoint, `evaluate.py` finally maps the guidance payload into the stable response contract:

- top-level `ok`
- `errors`
- `meta`
- `results`

The top-level API response is intentionally stable even though the internals are now dtypes-backed.

## Where Django Ends and `lx-data-models` Begins

The simplest rule is:

- Django objects are runtime input
- `lx-data-models` models are contract and validator definitions

More concretely:

### Owned by `endoreg_db`

- database models
- ORM loading and prefetch strategy
- cache/session orchestration
- translating live patient state into evaluator inputs
- HTTP response behavior

### Owned by `lx-data-models`

- request/response contract models
- lookup `DataDict` schema definitions
- report-template validator schema
- YAML-backed knowledge-base content
- normalization/validation of report-template structures

## Why This Split Exists

`lx-data-models` is not just a generated mirror of Django models.

A large part of the report-template and lookup contract surface is:

- YAML-backed
- knowledge-base driven
- intended to be portable across services

That is why evaluation is not modeled as "generate everything from the ORM". The ORM is the runtime state source, but the validator definitions and payload contracts live above it.

## Practical Rules for Future Changes

When changing evaluation behavior, ask:

1. Does the validator or payload schema change?
   - update `lx-data-models` first
2. Does the runtime need additional Django relations?
   - update a prefetch bundle in `load_patient_exam_for_eval()`
3. Does a model property now traverse a new relation?
   - assume it can create queries and add/update a regression test
4. Does the response shape change?
   - update `lx_dtypes` contracts and the endpoint mapping together

## Current Limitations

The current query hardening is a correctness improvement, not the final architecture.

Known open points:

- `_lab_value_prefetch_bundle()` currently prefetches all related lab values for the patient
- lab-heavy evaluation may eventually require bounded or context-specific lab loading
- `load_patient_exam_for_eval()` is now better structured, but it is still the central contract for hot-path evaluation traversal

That means the current rule is:

- if evaluation starts traversing a new relation, update the relevant prefetch bundle and the query regression tests in the same change

## Recommended Companion Tests

For changes in this area, the most relevant tests are:

- `tests/services/test_lookup_service_query_hardening.py`
- `tests/services/test_dtypes_requirement_engine.py`
- `tests/services/test_lookup_service_requirement_source.py`
- `tests/views/requirement/test_evaluate_requirements_view.py`

## One-Sentence Mental Model

Think of the evaluation system as:

- `lx-data-models` defines the rule language and typed payloads
- `endoreg_db` loads a query-safe Django object graph and executes those rules against live patient state
