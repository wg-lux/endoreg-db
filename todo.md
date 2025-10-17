# Test Suite Stabilization Plan

1. Restore legacy `center` support when instantiating `EndoscopyProcessor` instances, keeping many-to-many data while remaining backwards compatible.
2. Ensure `VideoFile.get_or_create_state()` persists the `VideoState` relation (prevents refresh failures in frame extraction flows).
3. Make `generate_patient()` deterministic by default so requirement-set evaluations don’t fail on random “unknown” gender picks.
4. Provide a safe helper/default for `VideoFile` creation and update test setup to reuse it, preventing NOT NULL constraint errors in segment update tests.
5. Consolidate video/PDF test fixtures (shared helpers/pytest fixtures) to eliminate redundant setup and improve DRY adherence.
6. After implementing the above, run `uv run python runtests.py` and tighten coverage where regressions were found.
