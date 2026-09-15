# tests/models/test_ai_model_get_latest_version.py

import pytest
from unittest.mock import patch
from typing import Protocol, cast
from endoreg_db.models.administration.ai.ai_model import (
    AiModel,
    DEFAULT_HF_MODEL_ID,
    DEFAULT_PREDICTION_LABELSET_NAME,
    DEFAULT_PREDICTION_MODEL_NAME,
)
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.metadata.model_meta import ModelMeta
from tests.helpers.model_weights import ensure_managed_stub_weights


class _ModelMetaQueryset(Protocol):
    def all(self) -> "_ModelMetaQueryset": ...
    def delete(self) -> None: ...


class _AiModelLike(Protocol):
    active_meta: ModelMeta | None
    metadata_versions: _ModelMetaQueryset

    def get_latest_version(self) -> ModelMeta | None: ...
    def save(self, update_fields: list[str] | None = None) -> None: ...


def _get_default_prediction_labelset() -> LabelSet:
    labelset = (
        LabelSet.objects.filter(name=DEFAULT_PREDICTION_LABELSET_NAME, version=1)
        .order_by("-id")
        .first()
    )
    if labelset is not None:
        return labelset
    return LabelSet.objects.create(
        name=DEFAULT_PREDICTION_LABELSET_NAME,
        version=1,
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
    ai_model = cast(
        _AiModelLike,
        AiModel.objects.create(name="model_active_check", description="test"),
    )

    active_meta = ModelMeta.objects.create(
        name="meta_active",
        model=ai_model,
        version="2",
        labelset=labelset,
    )
    ensure_managed_stub_weights(active_meta, suffix="meta_active_stub.safetensors")
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
    ai_model = cast(
        _AiModelLike,
        AiModel.objects.create(name="model_version_check", description="test"),
    )

    meta_v1 = ModelMeta.objects.create(
        name="meta_v1",
        model=ai_model,
        version="1",
        labelset=labelset,
    )
    ensure_managed_stub_weights(meta_v1, suffix="meta_v1_stub.safetensors")
    meta_v2 = ModelMeta.objects.create(
        name="meta_v2",
        model=ai_model,
        version="2",
        labelset=labelset,
    )
    ensure_managed_stub_weights(meta_v2, suffix="meta_v2_stub.safetensors")

    # Ensure no active meta is set
    assert ai_model.active_meta is None

    result = ai_model.get_latest_version()

    # Should pick '2' over '1'
    assert result == meta_v2
    assert result != meta_v1


@pytest.mark.django_db
def test_get_latest_version_raises_for_non_default_model_without_meta():
    """
    Scenario 3: No active meta AND no local versions.
    Non-default models must fail closed instead of silently falling back to the default model.
    """
    ai_model = cast(
        _AiModelLike,
        AiModel.objects.create(
            name="temp_model_for_hf_fallback_test",
            description="test model",
        ),
    )

    patch_target = "endoreg_db.services.model_meta_from_hf.ensure_model_meta_from_hf"

    with patch(patch_target) as mock_ensure:
        with pytest.raises(ValueError, match="No model metadata found"):
            ai_model.get_latest_version()

    mock_ensure.assert_not_called()


@pytest.mark.django_db
def test_get_latest_version_calls_hf_service_when_default_has_no_meta():
    labelset = _get_default_prediction_labelset()
    ai_model, _ = AiModel.objects.get_or_create(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        defaults={"description": "default prediction model"},
    )
    ai_model = cast(_AiModelLike, ai_model)
    ai_model.active_meta = None
    ai_model.save(update_fields=["active_meta"])
    ai_model.metadata_versions.all().delete()

    fake_meta = ModelMeta(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        model=ai_model,
        version="1",
        labelset=labelset,
        weights="model_weights/default_hf_fallback_stub.safetensors",
    )
    ensure_managed_stub_weights(fake_meta)

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
    )
    assert result is fake_meta


@pytest.mark.django_db
def test_get_latest_version_repairs_default_active_meta_with_missing_weights():
    labelset = _get_default_prediction_labelset()
    ai_model, _ = AiModel.objects.get_or_create(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        defaults={"description": "default prediction model"},
    )
    ai_model = cast(_AiModelLike, ai_model)
    active_meta, _ = ModelMeta.objects.update_or_create(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        model=ai_model,
        version="1",
        defaults={
            "labelset": labelset,
            "weights": "model_weights/missing_default_repair.safetensors",
        },
    )
    ai_model.active_meta = active_meta
    ai_model.save(update_fields=["active_meta"])

    fake_meta = ModelMeta(
        name=DEFAULT_PREDICTION_MODEL_NAME,
        model=ai_model,
        version="1",
        labelset=labelset,
        weights="model_weights/default_active_repair_stub.safetensors",
    )
    ensure_managed_stub_weights(fake_meta)

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
