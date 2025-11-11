from pathlib import Path

import pytest
from django.test import TestCase

from ...helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
    load_default_ai_model
)

from ...helpers.default_objects import (
    get_latest_segmentation_model,
)

from endoreg_db.models import ModelMeta

class AiModelTest(TestCase):
    def setUp(self):

        load_ai_model_label_data()
        load_ai_model_data()
        load_default_ai_model()

        self.ai_model_meta = get_latest_segmentation_model()


    def test_model_meta_creation(self):
        """Test the creation of an AiModel instance."""
        print(self.ai_model_meta)


@pytest.mark.expensive
@pytest.mark.django_db(transaction=True)
def test_setup_default_model_meta_from_huggingface_downloads_safetensors():
    """Ensure we can pull safetensor weights from Hugging Face and register metadata."""
    load_ai_model_label_data()
    load_ai_model_data()

    ModelMeta.objects.all().delete()

    model_meta = ModelMeta.setup_default_from_huggingface(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name="multilabel_classification_colonoscopy_default",
    )

    weights_path = Path(model_meta.weights.path)

    assert weights_path.exists(), "Weights file should exist after download"
    assert weights_path.suffix == ".safetensors"
    assert weights_path.stat().st_size > 0
    assert model_meta.model.active_meta == model_meta

    try:
        model_meta.weights.storage.delete(model_meta.weights.name)
    except Exception:
        pass
