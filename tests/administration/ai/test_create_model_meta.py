from django.test import TestCase
from endoreg_db.models import AiModel, ModelType

from ...helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
    load_default_ai_model
)

from ...helpers.default_objects import (
    get_latest_segmentation_model,
)

class AiModelTest(TestCase):
    def setUp(self):

        load_ai_model_label_data()
        load_ai_model_data()
        load_default_ai_model()

        self.ai_model_meta = get_latest_segmentation_model()
        
    def test_model_meta_creation(self):
        """Test the creation of an AiModel instance."""
        print(self.ai_model_meta)

