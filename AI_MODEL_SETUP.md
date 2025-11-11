## AI Model Metadata Setup Commands

This section documents the complete setup process for AI model metadata in EndoReg DB. These commands must be run in sequence to properly initialize the AI model system for video processing.

### 1. Load AI Models
```bash
uv run python manage.py load_ai_model_data
```
**Purpose**: Loads the base AI model definitions into the database.
- Creates `AiModel` instances with model names, types, and descriptions
- Required before creating model metadata
- **Output**: "Loaded X AI models"

### 2. Load AI Model Labels
```bash
uv run python manage.py load_ai_model_label_data
```
**Purpose**: Loads label sets and labels associated with AI models.
- Creates `LabelSet` and `Label` instances for model outputs
- Links labels to AI models for classification tasks
- **Output**: "Loaded X model labels"

### 3. Create Django Cache Table
```bash
uv run python manage.py createcachetable
```
**Purpose**: Creates the database table for Django's caching framework.
- Required for API polling and caching functionality
- Prevents `OperationalError` when accessing cached data
- **Output**: "Cache table for database cache created."

### 4. Create Model Metadata
```bash
uv run python manage.py create_multilabel_model_meta \
    --model_name image_multilabel_classification_colonoscopy_default \
    --model_meta_version 1 \
    --image_classification_labelset_name multilabel_classification_colonoscopy_default
```
**Purpose**: Creates `ModelMeta` instances with model weights and configuration.
- Copies model weights file to storage directory
- Sets up model parameters (activation, normalization, input size)
- Links metadata to AI models and label sets
- **Output**: "Created new ModelMeta: [model_name] (v1)"


### Verification Commands

After running the setup commands, verify everything is working:

```bash
# Check AI models and metadata
uv run python manage.py shell -c "
from endoreg_db.models import AiModel, ModelMeta
from endoreg_db.helpers.default_objects import get_latest_segmentation_model

print('AI Models:', AiModel.objects.count())
print('Model Metadata:', ModelMeta.objects.count())

model_meta = get_latest_segmentation_model()
print('Latest model meta:', model_meta)
print('Weights exist:', model_meta.weights and model_meta.weights.path)
"
```

### Troubleshooting

- **"Model file not found"**: Ensure you're running commands from the correct project directory (`/home/admin/endoreg-db`)
- **"No model metadata found"**: Run steps 1-4 in sequence
- **"Processor is not of type EndoscopyProcessor"**: Apply the import fix in step 5
- **Cache table errors**: Run step 3 to create the Django cache table

### Alternative: Hugging Face Setup

If local model files are not available, use the Hugging Face download command:

```bash
uv run python manage.py create_model_meta_from_huggingface \
    --model_name image_multilabel_classification_colonoscopy_default \
    --labelset_name multilabel_classification_colonoscopy_default \
    --meta_version 1
```

**Note**: Requires the model to exist on Hugging Face Hub.</content>
<parameter name="filePath">/home/admin/endoreg-db/AI_MODEL_SETUP.md