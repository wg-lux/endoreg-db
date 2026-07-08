from django.test import TestCase  # Import TestCase
from collections.abc import Mapping
from typing import Protocol, cast
from endoreg_db.models import AiModel, ModelType
from endoreg_db.serializers.administration.ai import AiModelSerializer
from lx_dtypes.models.contracts import (
    AiModelSerializerInputPayload,
    validate_ai_model_serializer_output_payload,
)


class _SerializerDataCarrier(Protocol):
    data: Mapping[str, object]


class _ModelTypeWithName(Protocol):
    name: str


class _AiModelWithType(Protocol):
    name: str
    model_type: _ModelTypeWithName | None


# Create a class inheriting from TestCase
class AiModelSerializerTest(TestCase):
    def test_create_ai_model_with_model_type_name(self):
        """
        Test creating an AiModel instance using the serializer,
        providing the model_type by its name string.
        """
        # 1. Arrange: Create prerequisite ModelType
        model_type_name: str = "test_model_type"
        ModelType.objects.create(
            name=model_type_name, description="Segmentation models"
        )

        # 2. Arrange: Prepare data for the new AiModel
        ai_model_data = AiModelSerializerInputPayload(
            name="Test AI Model 2",
            description="A second test model created via serializer.",
            model_type=model_type_name,
        )

        # 3. Act: Instantiate and validate the serializer
        serializer = AiModelSerializer(data=ai_model_data.model_dump())
        is_valid = serializer.is_valid()

        # 4. Assert: Validation passes (use self.assertTrue)
        self.assertTrue(is_valid, "Serializer validation failed")

        # 5. Act: Save the serializer to create the object
        ai_model_instance_raw = serializer.save()

        self.assertIsInstance(
            ai_model_instance_raw,
            AiModel,
            "Serializer did not return an AiModel instance",
        )

        # 6. Assert: Check the created instance (use self.assertEqual)
        ai_model_instance = cast(_AiModelWithType, ai_model_instance_raw)
        self.assertEqual(ai_model_instance.name, "Test AI Model 2")
        ai_model_instance_type = ai_model_instance.model_type
        self.assertIsNotNone(ai_model_instance_type)
        if ai_model_instance_type is not None:
            self.assertEqual(ai_model_instance_type.name, model_type_name)

    def test_create_ai_model_with_model_type_object(self):
        """
        Test creating an AiModel instance using the serializer,
        providing the model_type as a string (name).
        """
        # 1. Arrange: Create prerequisite ModelType
        ModelType.objects.create(
            name="Classification", description="Classification models"
        )

        # 2. Arrange: Prepare data for the new AiModel
        ai_model_data = AiModelSerializerInputPayload(
            name="Test AI Model 3",
            description="A third test model created via serializer with object.",
            model_type="Classification",
        )

        # 3. Act: Instantiate and validate the serializer
        serializer = AiModelSerializer(data=ai_model_data.model_dump())
        is_valid = serializer.is_valid()

        # 4. Assert: Validation passes (use self.assertTrue)
        self.assertTrue(is_valid, "Serializer validation failed")

        # 5. Act: Save the serializer to create the object
        ai_model_instance_raw = serializer.save()

        self.assertIsInstance(
            ai_model_instance_raw,
            AiModel,
            "Serializer did not return an AiModel instance",
        )

        # 6. Assert: Check the created instance (use self.assertEqual)
        ai_model_instance = cast(_AiModelWithType, ai_model_instance_raw)
        self.assertEqual(ai_model_instance.name, "Test AI Model 3")
        ai_model_instance_type = ai_model_instance.model_type
        self.assertIsNotNone(ai_model_instance_type)
        if ai_model_instance_type is not None:
            self.assertEqual(ai_model_instance_type.name, "Classification")

    def test_serialize_ai_model(self):
        """
        Test serializing an AiModel instance.
        """
        # 1. Arrange: Create prerequisite objects
        model_type = ModelType.objects.create(
            name="Detection", description="Detection models"
        )
        ai_model = AiModel.objects.create(
            name="Test AI Model 4",
            description="A fourth test model for serialization.",
            model_type=model_type,
        )

        # 2. Act: Serialize the instance
        serializer = AiModelSerializer(instance=ai_model)
        serializer_payload = cast(_SerializerDataCarrier, serializer)
        serialized_data = validate_ai_model_serializer_output_payload(
            dict[str, object](serializer_payload.data)
        )

        # 3. Assert: Check the serialized data (use self.assertEqual)
        self.assertEqual(serialized_data.name, "Test AI Model 4")
        self.assertEqual(serialized_data.model_type, "Detection")
        self.assertEqual(
            serialized_data.description, "A fourth test model for serialization."
        )
