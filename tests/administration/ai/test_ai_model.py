from django.test import TestCase
from typing import Protocol, cast
from endoreg_db.models import AiModel, ModelType


class _ModelTypeWithName(Protocol):
    name: str


class _AiModelWithModelType(Protocol):
    name: str
    description: str
    model_type: _ModelTypeWithName | None


class AiModelTest(TestCase):
    model_type: ModelType
    ai_model: AiModel

    def setUp(self):
        # Create a ModelType instance
        self.model_type = ModelType.objects.create(
            name="Test Model Type", description="A test model type"
        )

        # Create an AiModel instance
        self.ai_model = AiModel.objects.create(
            name="Test AI Model",
            description="A test AI model",
            model_type=self.model_type,
        )

    def test_ai_model_creation(self):
        """Test the creation of an AiModel instance."""
        ai_model = cast(_AiModelWithModelType, self.ai_model)
        self.assertEqual(ai_model.name, "Test AI Model")
        self.assertEqual(ai_model.description, "A test AI model")
        self.assertIsNotNone(ai_model.model_type)
        if ai_model.model_type is not None:
            self.assertEqual(ai_model.model_type.name, "Test Model Type")
