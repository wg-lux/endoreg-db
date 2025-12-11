# tests/models/test_ai_model_get_latest_version.py

import pytest
from unittest.mock import patch

from endoreg_db.models import AiModel, ModelMeta, LabelSet, Center


@pytest.mark.django_db
def test_get_latest_version_returns_active_meta():
    labelset = LabelSet.objects.create(
        name="multilabel_classification_colonoscopy_default",
        version=1,
    )
    ai_model = AiModel.objects.create(name="my_model", description="test model")

    active_meta = ModelMeta.objects.create(
        name="my_model",
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
    labelset = LabelSet.objects.create(
        name="multilabel_classification_colonoscopy_default",
        version=1,
    )
    ai_model = AiModel.objects.create(name="my_model", description="test model")

    meta_v1 = ModelMeta.objects.create(
        name="my_model",
        model=ai_model,
        version="1",
        labelset=labelset,
    )
    meta_v2 = ModelMeta.objects.create(
        name="my_model",
        model=ai_model,
        version="2",
        labelset=labelset,
    )

    assert ai_model.active_meta is None

    result = ai_model.get_latest_version()

    # should pick highest version (assuming version is sortable as string/number)
    assert result == meta_v2
    assert result != meta_v1


@pytest.mark.django_db
def test_get_latest_version_calls_hf_service_when_no_meta():
    """
    When there is no active_meta and metadata_versions is empty,
    get_latest_version should call ensure_model_meta_from_hf and return its result.
    """
    ai_model = AiModel.objects.create(
        name="image_multilabel_classification_colonoscopy_default",
        description="test model",
    )

    # Patch the function where it is DEFINED, since it's imported
    # inside get_latest_version.
    patch_target = (
        "endoreg_db.services.model_meta_from_hf.ensure_model_meta_from_hf"
    )

    fake_meta = ModelMeta(
        name="image_multilabel_classification_colonoscopy_default",
        model=ai_model,
        version="1",
    )

    with patch(patch_target) as mock_ensure:
        mock_ensure.return_value = fake_meta

        result = ai_model.get_latest_version()

    mock_ensure.assert_called_once_with(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        model_name="image_multilabel_classification_colonoscopy_default",
        labelset_name="multilabel_classification_colonoscopy_default",
        meta_version="1",
    )

    assert result is fake_meta
