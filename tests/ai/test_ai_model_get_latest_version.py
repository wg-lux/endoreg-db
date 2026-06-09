# tests/models/test_ai_model_get_latest_version.py

import pytest
from unittest.mock import patch
from endoreg_db.models import AiModel, ModelMeta, LabelSet
from endoreg_db.models.administration.ai.ai_model import (
    DEFAULT_HF_MODEL_ID,
    DEFAULT_PREDICTION_LABELSET_NAME,
    DEFAULT_PREDICTION_MODEL_NAME,
)


@pytest.mark.django_db
def test_get_latest_version_returns_active_meta():
    """
    Scenario 1: Active meta is set. It should be returned immediately.
    """
    labelset = LabelSet.objects.create(
        name="labelset_1",
        version=1,
    )
    ai_model = AiModel.objects.create(name="model_active_check", description="test")

    active_meta = ModelMeta.objects.create(
        name="meta_active",
        model=ai_model,
        version="2",
        labelset=labelset,
    )
    ai_model.active_meta = active_meta
    ai_model.save()

    result = ai_model.get_latest_version()
    assert result == active_meta


@pytest.mark.django_db
def test_get_latest_version_returns_latest_metadata_when_no_active_meta():
    """
    Scenario 2: No active meta, but local versions exist. Should return highest version.
    """
    labelset = LabelSet.objects.create(
        name="labelset_2",
        version=1,
    )
    ai_model = AiModel.objects.create(name="model_version_check", description="test")

    meta_v1 = ModelMeta.objects.create(
        name="meta_v1",
        model=ai_model,
        version="1",
        labelset=labelset,
    )
    meta_v2 = ModelMeta.objects.create(
        name="meta_v2",
        model=ai_model,
        version="2",
        labelset=labelset,
    )

    # Ensure no active meta is set
    assert ai_model.active_meta is None

    result = ai_model.get_latest_version()

    # Should pick '2' over '1'
    assert result == meta_v2
    assert result != meta_v1


@pytest.mark.django_db
def test_get_latest_version_calls_hf_service_when_no_meta():
    """
    Scenario 3: No active meta AND no local versions.
    Should attempt to download specific default model from HF.
    """
    # 1. Use a UNIQUE name to avoid IntegrityError and ensure no pre-existing metadata
    ai_model = AiModel.objects.create(
        name="temp_model_for_hf_fallback_test",
        description="test model",
    )

    # 2. Patch the service where it is DEFINED
    patch_target = "endoreg_db.services.model_meta_from_hf.ensure_model_meta_from_hf"

    fake_meta = ModelMeta(
        name="test_mock_return",
        model=ai_model,
        version="1",
    )

    with patch(patch_target) as mock_ensure:
        mock_ensure.return_value = fake_meta

        result = ai_model.get_latest_version()

    # 3. Assert it called the service with the HARDCODED values from your implementation
    # (Your implementation ignores self.name and requests the default colonoscopy model)
    mock_ensure.assert_called_once_with(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        model_name="image_multilabel_classification_colonoscopy_default",
        labelset_name="multilabel_classification_colonoscopy_default",
        meta_version="1",
    )

    assert result is fake_meta


@pytest.mark.django_db
def test_get_latest_version_repairs_default_active_meta_with_missing_weights():
    labelset = LabelSet.objects.create(
        name=DEFAULT_PREDICTION_LABELSET_NAME,
        version=1,
    )
    ai_model = AiModel.objects.create(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        description="default prediction model",
    )
    active_meta = ModelMeta.objects.create(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        model=ai_model,
        version="1",
        labelset=labelset,
        weights="model_weights/missing.safetensors",
    )
    ai_model.active_meta = active_meta
    ai_model.save(update_fields=["active_meta"])

    fake_meta = ModelMeta(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        model=ai_model,
        version="1",
        labelset=labelset,
    )

    with patch(
        "endoreg_db.services.model_meta_from_hf.ensure_model_meta_from_hf",
        return_value=fake_meta,
    ) as mock_ensure:
        result = ai_model.get_latest_version()

    mock_ensure.assert_called_once_with(
        model_id=DEFAULT_HF_MODEL_ID,
        model_name=DEFAULT_PREDICTION_MODEL_NAME,
        labelset_name=DEFAULT_PREDICTION_LABELSET_NAME,
        meta_version="1",
        labelset_version=1,
    )
    assert result is fake_meta
