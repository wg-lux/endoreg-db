# Test Errors

## Findings
- tests/media/video/test_video_file_extracted.py::VideoFileModelExtractedTest::test_pipeline_real_operations – VideoFile.pipe_1 now bails out with False; the new _pipe_1 logic exits when prerequisites fail (likely the VideoPredictionMeta lookup inside the transaction). Need to inspect logs/DB state to confirm why that get(...) misses right after predict_video’s get_or_create, or whether earlier steps (frame extraction/text metadata) still leave state flags unset.

- tests/models/... delete/validation suites – _get_raw_file_path now raises FileNotFoundError whenever the backing file is absent. Callers like VideoFile.delete() expect a tolerant helper and don’t guard; propagated exceptions break metadata and processing-history cascade tests. Same stricter path lookup also feeds frame extraction/validation, so it probably underpins other regressions.

- test_validation_deletion.py – guess_name_gender currently hits Gender.objects.get(...), so fresh test DBs without seeded genders raise Gender.DoesNotExist. Downstream logic expects a plain name string ("male", "unknown") to resolve later; the function should return a slug/fallback instead of touching the DB directly.

- tests/models/video/test_video_processing_history.py & tests/serializers/video/... – VideoProcessingHistory.__str__ now returns a debug dump and the serializer exposes raw enums by setting operation_display/status_display to the same field. Both regressions violate the human-readable API contract.

- test_whitenoise_file_serving.py – VideoFile lost its active_file_url convenience property; file-server tests instantiate real objects and expect to call it for signed/static URLs.

- test_requirement_lookup.py – LookupViewSet.init always passes user_tags= into create_lookup_token_for_pe. Test stubs that only accept pe_id now fail with unexpected kwarg; we should preserve backward compatibility by only passing the keyword when tags are provided.

## Plan
- Patch _get_raw_file_path to log-and-return None instead of raising; review every caller to ensure they handle None gracefully and adjust any new call sites accordingly.

- Fix guess_name_gender to return a detected slug with safe fallbacks (unknown), avoiding DB lookups; confirm SensitiveMeta creation tolerates missing gender rows (lazily create default if still absent).

- Restore user-friendly strings: revert VideoProcessingHistory.__str__ to the get_*_display() format and update the serializer to source those display methods.

- Reintroduce VideoFile.active_file_url (wrapping self.active_file.url with proper error handling) so WhiteNoise tests and downstream code regain the expected API.

- Update LookupViewSet.init (and any other entry points) to only include user_tags in the service call when non-empty, keeping mocks without that parameter working.

- Investigate the Pipe 1 regression once the foundational path/metadata fixes land—rerun the integration test with logging to see whether the failure stems from state flags, prediction metadata creation, or another new guard—and address whatever condition remains.

# To-Do

## RequirementSet based Generator
- [x] Draft recursive RequirementSet planner (`RequirementPlan`/`RequirementSetPlan`) and pretty-printer in `scripts/case_generator/prototype.py` for exploratory runs.
- [ ] Flesh out artefact builders for lab values, medications, events, examinations, and findings leveraging existing factories under `endoreg_db/factories/`.
- [ ] Implement `generate_patient_for_requirement_set` orchestrator that applies artefact builders, re-evaluates requirements, and honors RequirementSet type semantics.
- [ ] Add validation/reporting hooks (vacuous success detection, retry diagnostics) plus optional dry-run mode.
- [ ] Create pytest coverage in `tests/case_generator/test_requirement_generator.py` for representative sets (lab baseline, high bleed risk, timeframe operators, exactly/at least/at most variants).


# For Later
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

The colonoscopy_default_segmentation model is available on Hugging Face here: https://huggingface.co/wg-lux/colo_segmentation_RegNetX800MF_base/resolve/main/colo_segmentation_RegNetX800MF_base.safetensors

# Establish CI/CD Best-Practices
to be done