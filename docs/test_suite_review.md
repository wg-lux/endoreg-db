# Test Suite Review (2025-10-18)

## Module Inventory Snapshot
- `tests/administration/*`: CRUD/admin scenarios for centers, shifts, qualifications.
- `tests/assets/*`: Large artefact fixtures (models, JSON) consumed by video/import suites.
- `tests/dataloader/test_dataloader.py`: Exercises custom dataset ingestion helpers.
- `tests/environment/test_env.py`: Validates env wiring and settings overrides.
- `tests/examination/*`: Domain tests for examination models and serializers.
- `tests/fileserver/*`: Covers WhiteNoise static file handling.
- `tests/finding/*`: Disease/finding taxonomy loading and serializer behaviour.
- `tests/helpers/*`: Fixture factories plus integration smoke tests for helper modules.
- `tests/legacy/*`: Regression coverage for legacy data migration utilities.
- `tests/media/video/*`: Heavy video import/anonymization flows (long runtime, GPU/ffmpeg dependencies).
- `tests/models/*`: ORM and state machine regression coverage.
- `tests/pipelines/*`: Orchestration pipelines and process directory flows.
- `tests/requirement/*`: Requirement rule matrix validation; high combinatorial count.
- `tests/services/*`: Service-layer orchestration smoke tests (RQ, anonymizer, imports).

## Shared Fixture & Caching Landscape
- `tests/conftest.py` defines global caches (`_session_cache`, `_session_video_file`, `_base_data_loaded`) implemented via module-level globals; invalidation is implicit and hard to reason about during parametrised runs.
- Base taxonomy loaders (`load_*`) execute inside `base_db_data` fixture with a function scope guard backed by `global _base_data_loaded`, mixing fixture scopes and custom caching.
- Video fixtures (`sample_video_file`, `processed_video_file`) key off environment flags (`RUN_VIDEO_TESTS`, `SKIP_EXPENSIVE_TESTS`) with ad-hoc globals; repeated suite invocations in the same process can leak cached state.
- Session-level DB optimisations (`optimize_database_queries`) execute per-test (autouse) rather than establishing a single shared connection configuration.
- `_session_cache` remains unused across the suite, and several modules re-create expensive assets (e.g., YAML loads, JSON fixtures) without a shared cache hook.

## Pain Points Observed During Full-Suite Runs
- Environment flag handling (`RUN_VIDEO_TESTS`) is inconsistent: default expression evaluates to `False` even when explicitly enabling, leading to skipped media tests locally but executed in CI.
- Global caches are not namespaced by test parameters; when parametrised tests mutate cached video objects, downstream tests see dirty state, causing non-deterministic failures in full runs.
- Multiple suites duplicate fixture logic (e.g., default center/processor creation) instead of relying on `tests.helpers.default_objects`, increasing setup time and risking drift.
- Several long-running suites (`tests/media/video`, `tests/pipelines`) hit the filesystem repeatedly because cached paths live outside a central storage coordinator; temp directories are re-created per module.
- Lack of a documented test layout encourages bespoke fixtures per module, making it difficult to standardise teardown behaviour and to introduce suite-wide caching.
