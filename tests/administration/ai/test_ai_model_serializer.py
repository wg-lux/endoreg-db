from django.test import TestCase  # Import TestCase
from endoreg_db.models import AiModel, ModelType
from endoreg_db.serializers.administration.ai import AiModelSerializer

# Create a class inheriting from TestCase
class AiModelSerializerTest(TestCase):

    def test_create_ai_model_with_model_type_name(self):
        """
        Test creating an AiModel instance using the serializer,
        providing the model_type by its name string.
        """
        # 1. Arrange: Create prerequisite ModelType
        model_type_name = "test_model_type"
        model_type = ModelType.objects.create(name=model_type_name, description="Segmentation models")

        # 2. Arrange: Prepare data for the new AiModel
        ai_model_data = {
            "name": "Test AI Model 2",
            "description": "A second test model created via serializer.",
            "model_type": model_type_name, # Provide name as string
            # Add other required fields if any, or ensure they allow null/blank
        }

        # 3. Act: Instantiate and validate the serializer
        serializer = AiModelSerializer(data=ai_model_data)
        is_valid = serializer.is_valid()

        # 4. Assert: Validation passes (use self.assertTrue)
        self.assertTrue(is_valid, f"Serializer validation failed: {serializer.errors}")

        # 5. Act: Save the serializer to create the object
        ai_model_instance = serializer.save()

        # 6. Assert: Check the created instance (use self.assertEqual)
        self.assertEqual(AiModel.objects.count(), 1)
        self.assertEqual(ai_model_instance.name, "Test AI Model 2")
        self.assertEqual(ai_model_instance.model_type, model_type)
        self.assertEqual(ai_model_instance.model_type.name, model_type_name)

    def test_create_ai_model_with_model_type_object(self):
        """
        Test creating an AiModel instance using the serializer,
        providing the model_type as a string (name).
        """
        # 1. Arrange: Create prerequisite ModelType
        model_type = ModelType.objects.create(name="Classification", description="Classification models")

        # 2. Arrange: Prepare data for the new AiModel
        ai_model_data = {
            "name": "Test AI Model 3",
            "description": "A third test model created via serializer with object.",
            "model_type": model_type.name,  # Pass the name (string) instead of the object
        }

        # 3. Act: Instantiate and validate the serializer
        serializer = AiModelSerializer(data=ai_model_data)
        is_valid = serializer.is_valid()

        # 4. Assert: Validation passes (use self.assertTrue)
        self.assertTrue(is_valid, f"Serializer validation failed: {serializer.errors}")

        # 5. Act: Save the serializer to create the object
        ai_model_instance = serializer.save()

        # 6. Assert: Check the created instance (use self.assertEqual)
        self.assertEqual(AiModel.objects.count(), 1)
        self.assertEqual(ai_model_instance.name, "Test AI Model 3")
        self.assertEqual(ai_model_instance.model_type, model_type)
        self.assertEqual(ai_model_instance.model_type.name, "Classification")
    
    def test_serialize_ai_model(self):
        """
        Test serializing an AiModel instance.
        """
        # 1. Arrange: Create prerequisite objects
        model_type = ModelType.objects.create(name="Detection", description="Detection models")
        ai_model = AiModel.objects.create(
            name="Test AI Model 4",
            description="A fourth test model for serialization.",
            model_type=model_type
        )

        # 2. Act: Serialize the instance
        serializer = AiModelSerializer(instance=ai_model)
        serialized_data = serializer.data

        # 3. Assert: Check the serialized data (use self.assertEqual)
        self.assertEqual(serialized_data['name'], "Test AI Model 4")
        self.assertEqual(serialized_data['model_type'], model_type.name) # SlugRelatedField serializes to the slug field value
        self.assertEqual(serialized_data['description'], "A fourth test model for serialization.")
