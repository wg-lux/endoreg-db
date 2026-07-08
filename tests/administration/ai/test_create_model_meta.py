from django.core.files.storage import Storage
from typing import Protocol, cast
from pathlib import Path

import pytest
from django.test import TestCase

from endoreg_db.models import ModelMeta

from ...helpers.data_loader import load_data
from ...helpers.default_objects import (
    get_latest_segmentation_model,
)


class _WeightsFileLike(Protocol):
    name: str
    storage: Storage


class _AiModelWithActiveMeta(Protocol):
    active_meta: ModelMeta | None


class _ModelMetaWithModel(Protocol):
    model: _AiModelWithActiveMeta


class AiModelTest(TestCase):
    ai_model_meta: ModelMeta

    def setUp(self):
        load_data()

        self.ai_model_meta = get_latest_segmentation_model()

    def test_model_meta_creation(self):
        """Test the creation of an AiModel instance."""
        print(self.ai_model_meta)


@pytest.mark.expensive
@pytest.mark.django_db(transaction=True)
def test_setup_default_model_meta_from_huggingface_downloads_safetensors():
    """Ensure we can pull safetensor weights from Hugging Face and register metadata."""
    load_data()

    ModelMeta.objects.all().delete()

    model_meta = ModelMeta.setup_default_from_huggingface(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name="multilabel_classification_colonoscopy_default",
    )

    weights_path = Path(model_meta.weights.path)

    assert weights_path.exists(), "Weights file should exist after download"
    assert weights_path.suffix == ".safetensors"
    assert weights_path.stat().st_size > 0
    model = cast(_ModelMetaWithModel, model_meta).model
    assert model.active_meta == model_meta

    try:
        weights_file = cast(_WeightsFileLike, model_meta.weights)
        weights_name = weights_file.name
        if not weights_name:
            raise ValueError("Model meta download did not persist a weight file name")
        weights_file.storage.delete(weights_name)
    except Exception:
        pass
