# Test suite standardization and efficiency

## Test Suite Standardization & Caching Plan
1. Catalogue the current pytest suite structure (module layout, shared fixtures, caching usage) and record pain points from full-suite runs.
*Result (Main messages):* documented in `docs/test_suite_review.md` — sprawling module layout, ad-hoc global caches lacking invalidation, inconsistent `RUN_VIDEO_TESTS` defaults causing flaky behaviour, and redundant fixture setup across domains.

2. Draft a standardized test blueprint (naming conventions, fixture hierarchy, parametrization rules) and circulate for review.
*Result (Main messages):* Draft captured in `docs/test_suite_blueprint.md`; awaiting reviewer feedback prior to enforcement.

3. Design a centralized caching layer (shared pytest fixture + backing store) with clear invalidation rules and integration points.
*Result (Main messages):* Implemented session-scoped `CacheManager` via `tests/plugins/cache.py` and migrated `tests/conftest.py` fixtures/mocks to use namespaced caches; ready for suite pilots.
4. Pilot the standard blueprint and caching fixture in the `tests/media/video` and `tests/services` suites, measuring runtime impact.
		*Progress:* Optimized video helpers now share the session cache and record build timings; `test_video_file_extracted.py`, `test_video_import.py`, and `test_video_segment_crud.py` all consume cached assets (segment suite now uses a lightweight stub video with cached payload rebuilds). Base data loading seeds stub `ModelMeta` entries with tiny checkpoint placeholders (toggled via `USE_STUB_MODEL_META`), so fast runs no longer trigger `create_multilabel_model_meta`. Next up: migrate `tests/services` and extend timing capture.
5. Roll the standardization out across remaining domains and update CI/test documentation.

# Test Suite Stabilization Plan
2. ✅ Make `generate_patient()` deterministic by default so requirement-set evaluations don’t fail on random “unknown” gender picks.
3. Provide a safe helper/default for `VideoFile` creation and update test setup to reuse it, preventing NOT NULL constraint errors in segment-adjacent suites.
	*Progress:* Lightweight cached `VideoFile` stub now backs segment CRUD tests, with cache payload rebuilds surviving transactional flushes.
4. Consolidate video/PDF test fixtures (shared helpers/pytest fixtures) to eliminate redundant setup and improve DRY adherence.
5. After implementing the above, run `uv run python runtests.py` and tighten coverage where regressions were found. Current full-suite runtime ~660 s; capture `--durations=20` to pick the next optimization target.

# ColoSegmentation Model Supply
To consolidate our framework, we should create a fixed model_meta .yaml file for our current colonoscopy segmentation model. For this, we should implement a new feature which allows a model_meta entry to store a hugging face url. The current model should be supplied as default model when we run load_base_db_data. On first usage, the model should be downloaded if not already available.

The colonoscopy_default_segmentation model is available on huggingface here: https://huggingface.co/wg-lux/colo_segmentation_RegNetX800MF_base/resolve/main/colo_segmentation_RegNetX800MF_base.ckpt

# Establish CI/CD Best-Practices
to be done