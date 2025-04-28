from django.test import TestCase
from endoreg_db.models import AiModel, ModelType

class AiModelTest(TestCase):
    def setUp(self):
        # Create a ModelType instance
        self.model_type = ModelType.objects.create(
            name="Test Model Type",
            description="A test model type"
        )
        
        # Create an AiModel instance
        self.ai_model = AiModel.objects.create(
            name="Test AI Model",
            description="A test AI model",
            model_type=self.model_type,
        )
        
    def test_ai_model_creation(self):
        """Test the creation of an AiModel instance."""
        self.assertEqual(self.ai_model.name, "Test AI Model")
        self.assertEqual(self.ai_model.description, "A test AI model")
        self.assertEqual(self.ai_model.model_type, self.model_type)
