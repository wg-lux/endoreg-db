from endoreg_db.models import AiModel, ModelType
from endoreg_db.serializers.administration.ai import AiModelSerializer


def test_create_ai_model_with_model_type_name():
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

    # Print errors if validation fails
    if not is_valid:
        print(serializer.errors)

    # 4. Assert: Validation passes
    assert is_valid, "Serializer validation failed"

    # 5. Act: Save the serializer to create the object
    ai_model_instance = serializer.save()

    # 6. Assert: Check the created instance
    assert AiModel.objects.count() == 1
    assert ai_model_instance.name == "Test AI Model 2"
    assert ai_model_instance.model_type == model_type
    assert ai_model_instance.model_type.name == model_type_name

def test_create_ai_model_with_model_type_object():
    """
    Test creating an AiModel instance using the serializer,
    providing the model_type as an object instance.
    """
    # 1. Arrange: Create prerequisite ModelType
    model_type = ModelType.objects.create(name="Classification", description="Classification models")

    # 2. Arrange: Prepare data for the new AiModel
    ai_model_data = {
        "name": "Test AI Model 3",
        "description": "A third test model created via serializer with object.",
        "model_type": model_type, # Provide object instance
    }

    # 3. Act: Instantiate and validate the serializer
    serializer = AiModelSerializer(data=ai_model_data)
    is_valid = serializer.is_valid()

    # Print errors if validation fails
    if not is_valid:
        print(serializer.errors)

    # 4. Assert: Validation passes
    assert is_valid, "Serializer validation failed"

    # 5. Act: Save the serializer to create the object
    ai_model_instance = serializer.save()

    # 6. Assert: Check the created instance
    assert AiModel.objects.count() == 1
    assert ai_model_instance.name == "Test AI Model 3"
    assert ai_model_instance.model_type == model_type
    assert ai_model_instance.model_type.name == "Classification"

def test_serialize_ai_model():
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

    # 3. Assert: Check the serialized data
    assert serialized_data['name'] == "Test AI Model 4"
    assert serialized_data['model_type'] == model_type.name # SlugRelatedField serializes to the slug field value
    assert serialized_data['description'] == "A fourth test model for serialization."
