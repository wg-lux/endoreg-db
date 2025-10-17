# Test Suite Stabilization Plan
1. Ensure `VideoFile.get_or_create_state()` persists the `VideoState` relation (prevents refresh failures in frame extraction flows).
2. Make `generate_patient()` deterministic by default so requirement-set evaluations don’t fail on random “unknown” gender picks.
3. Provide a safe helper/default for `VideoFile` creation and update test setup to reuse it, preventing NOT NULL constraint errors in segment-adjacent suites.
4. Consolidate video/PDF test fixtures (shared helpers/pytest fixtures) to eliminate redundant setup and improve DRY adherence.
5. After implementing the above, run `uv run python runtests.py` and tighten coverage where regressions were found.

# ColoSegmentation Model Supply
To consolidate our framework, we should create a fixed model_meta .yaml file for our current colonoscopy segmentation model. For this, we should implement a new feature which allows a model_meta entry to store a hugging face url. The current model should be supplied as default model when we run load_base_db_data. On first usage, the model should be downloaded if not already available.

The colonoscopy_default_segmentation model is available on huggingface here: https://huggingface.co/wg-lux/colo_segmentation_RegNetX800MF_base/resolve/main/colo_segmentation_RegNetX800MF_base.ckpt
