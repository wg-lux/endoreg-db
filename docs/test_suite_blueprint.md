# Test Suite Standardization Blueprint (Draft)

_Date: 2025-10-18_

## Goals
- Consistent test layout and naming across all domains.
- Predictable fixture hierarchy that makes scope explicit and minimises global state.
- Single cache interface with explicit lifecycle for expensive resources (DB seeds, video assets, ML models).
- Clear integration guidance for new tests and suites.

## Directory & Naming Conventions
- **Top-level folders:** maintain domain grouping (`tests/<domain>/`) but enforce suffixes: `test_*.py` files only.
- **Class naming:** use `Test<Subject>` for unit-style suites, `<Feature>Tests` reserved for integration/system cases.
- **Function naming:** `test_<scenario>_<expected>` pattern; leverage parametrization for variant coverage instead of bespoke fixtures.
- **Helper modules:** move shared builders/factories under `tests/helpers/` with explicit exports through `__all__`.

## Fixture Hierarchy
- **Session scope (`conftest.py` or plugin):**
  - `base_data`: loads taxonomy/seed data exactly once, returns immutable context object.
  - `cache`: provides namespaced cache manager (see Caching Layer) with `get/set/clear` API.
  - `env_settings`: applies test-specific overrides (MEDIA_ROOT, Celery eager) in a context manager.
- **Module scope:** domain-specific heavy resources (e.g., processed video file) keyed via cache fixture to avoid duplication.
- **Function scope:** lightweight factories, API clients, data builders; avoid mutating session objects directly.
- Use `pytest_plugins = ["tests.plugins.cache", "tests.plugins.fixtures"]` to centralise definitions.

## Parametrization & Markers
- Prefer `@pytest.mark.parametrize` over hand-written loops.
- Define common markers: `slow`, `video`, `integration`, `requires_gpu`. Document skip conditions in `pytest.ini`.
- Implement marker-aware skip logic in fixtures rather than environment variable spaghetti.

## Caching Layer Design
- Introduce `tests/plugins/cache.py` defining a `CacheManager` with:
  - `get(namespace, key, default=None)` / `set(namespace, key, value)` APIs.
  - Lazy initialisation backed by `functools.lru_cache` for in-memory usage plus optional `diskcache` fallback if available.
  - `invalidate(namespace, key=None)` hooks invoked via pytest `request.addfinalizer` to avoid cross-test leaks.
- Session-scoped `cache` fixture returns a manager instance; module/functional fixtures request namespaces (`cache.namespace("video")`).
- Provide helper `cache.memoize(namespace)` decorator to wrap expensive factory functions (e.g., processed video creation).
- Replace module-level globals (`_base_data_loaded`, `_session_video_file`) by storing state under deterministic keys, e.g. `cache.set("video", "processed_default", value)`.
- Emit debug logging when cache hits/misses occur during `-vv` runs to aid troubleshooting.

## Documentation & Governance
- Document expectations in `docs/testing/TEST_GUIDELINES.md` (to be created).
- Add pre-commit lint to fail if non-conforming test module names detected.
- Establish codeowners for `tests/` to review adherence.

## Next Steps
1. Implement prototype cache manager and fixtures (Step 3 of action plan).
  - Create `tests/plugins/cache.py` and `tests/plugins/__init__.py` exporting `pytest_plugins`.
  - Update `pytest.ini` to register default markers (`slow`, `video`, `integration`, `requires_gpu`).
  - Refactor `tests/conftest.py` to consume the new `cache` fixture and remove global caches.
2. Migrate `tests/media/video` to new fixture hierarchy as pilot (Step 4).
  - Introduce module-scoped fixtures leveraging `cache.namespace("video")` and ensure cleanup via invalidation hooks.
  - Wire helper modules such as `tests/helpers/optimized_video_fixtures.py` into dedicated namespaces via `configure_cache` to eliminate legacy globals.
  - ✅ Segment CRUD suite now runs against a lightweight cached video stub; validate similar patterns for services tests next.
  - ✅ Base data loader now provisions stub AI model metadata (behind `USE_STUB_MODEL_META=true`) so `load_default_ai_model` no longer copies large checkpoints during fast suites.
  - Current end-to-end runtime still ~660 s; profile durations to locate the next hotspots.
  - Measure runtime improvements and document findings in `docs/testing/TEST_GUIDELINES.md`.
3. Update developer guide with blueprint summary once pilot validated.
