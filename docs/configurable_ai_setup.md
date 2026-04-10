# Configurable AI Model Setup Guide

## Overview

The EndoReg DB setup system is now fully configurable, eliminating hardcoded model names and making it easy to adapt for different model configurations. This guide explains how to customize the setup for your specific AI models.

## Configuration Files

### 1. Setup Configuration (`endoreg_db/data/setup_config.yaml`)

This is the main configuration file that controls setup behavior:

```yaml
# Primary models used during setup
default_models:
  primary_classification_model: "image_multilabel_classification_colonoscopy_default"
  primary_labelset: "multilabel_classification_colonoscopy_default"

# HuggingFace fallback configuration
huggingface_fallback:
  enabled: true
  repo_id: "wg-lux/colo_segmentation_RegNetX800MF_base"
  filename: "colo_segmentation_RegNetX800MF_base.safetensors"
  labelset_name: "multilabel_classification_colonoscopy_default"

# Weight file search patterns (supports wildcards)
weights_search_patterns:
  - "colo_segmentation_RegNetX800MF_*.safetensors"
  - "image_multilabel_classification_colonoscopy_default_*.safetensors"
  - "*_colonoscopy_*.safetensors"
  
# Search directories for local weights
weights_search_dirs:
  - "tests/assets"
  - "assets" 
  - "data/storage/model_weights"
  - "${STORAGE_DIR}/model_weights"  # Environment variable substitution

# Default values for auto-generated metadata
auto_generation_defaults:
  activation: "sigmoid"
  mean: "0.485,0.456,0.406"
  std: "0.229,0.224,0.225"
  size_x: 224
  size_y: 224
  axes: "CHW"
  batchsize: 32
  num_workers: 4
```

### 2. Enhanced Model YAML (`endoreg_db/data/ai_model_meta/*.yaml`)

Model metadata files can now include setup-specific configuration:

```yaml
- model: endoreg_db.model_meta
  fields:
    name: "your_custom_model_name"
    version: "1"
    model: "your_custom_model_name"
    labelset: "your_custom_labelset"
    # ... other metadata fields
  
  # Setup-specific configuration for this model
  setup_config:
    is_primary_model: true  # Mark as primary model for setup
    weight_filenames:       # Specific weight file patterns
  - "your_model_weights_*.safetensors"
      - "custom_model_v1_*.pth"
    huggingface_fallback:   # HF configuration for this model
      repo_id: "your-org/your-model-repo"
  filename: "model_weights.safetensors"
```

## Configuration API

### SetupConfig Class

The `endoreg_db.utils.setup_config.SetupConfig` class provides programmatic access to configuration:

```python
from endoreg_db.utils.setup_config import setup_config

# Get configured model names
primary_model = setup_config.get_primary_model_name()
primary_labelset = setup_config.get_primary_labelset_name()

# Get HuggingFace configuration
hf_config = setup_config.get_huggingface_config()

# Find weight files using configured patterns
weight_files = setup_config.find_model_weights_files()

# Get auto-generation defaults
defaults = setup_config.get_auto_generation_defaults()
```

## Customization Examples

### Example 1: Different Model Type

To use a segmentation model instead of classification:

```yaml
# setup_config.yaml
default_models:
  primary_classification_model: "video_segmentation_transformer"
  primary_labelset: "segmentation_labels_v2"

weights_search_patterns:
  - "segmentation_transformer_*.pth"
  - "video_seg_*.safetensors"

auto_generation_defaults:
  activation: "softmax"
  mean: "0.5,0.5,0.5"  # Different normalization
  std: "0.5,0.5,0.5"
  size_x: 512  # Higher resolution
  size_y: 512
```

### Example 2: Multiple Model Support

For setups with multiple models:

```yaml
# setup_config.yaml - can reference any defined model as primary
default_models:
  primary_classification_model: "advanced_colonoscopy_classifier"
  primary_labelset: "advanced_colonoscopy_labels"

weights_search_patterns:
  - "*_colonoscopy_*.safetensors"  # Catches all colonoscopy models
  - "*_classification_*.pth"
  - "model_*.weights"
```

### Example 3: Custom HuggingFace Repository

```yaml
# setup_config.yaml
huggingface_fallback:
  enabled: true
  repo_id: "your-organization/custom-endoscopy-model"
  filename: "best_model.safetensors"
  labelset_name: "custom_endoscopy_labels"
```

### Example 4: Environment-Specific Configuration

```yaml
# setup_config.yaml
weights_search_dirs:
  - "${MODEL_CACHE_DIR}/weights"      # Custom environment variable
  - "${HOME}/.endoreg/models"         # User-specific location
  - "/shared/models/endoreg"          # Shared network location
  - "data/storage/model_weights"      # Default fallback
```

## Migration from Hardcoded Setup

### Step 1: Identify Current Models

```bash
# Check what models are currently configured
python manage.py shell -c "
from endoreg_db.models import AiModel
for model in AiModel.objects.all():
    print(f'Model: {model.name}')
"
```

### Step 2: Create Setup Configuration

Create `endoreg_db/data/setup_config.yaml` with your model names:

```yaml
default_models:
  primary_classification_model: "your_existing_model_name"
  primary_labelset: "your_existing_labelset"
```

### Step 3: Test Configuration

```bash
python manage.py shell -c "
from endoreg_db.utils.setup_config import setup_config
print('Primary model:', setup_config.get_primary_model_name())
print('Found weights:', setup_config.find_model_weights_files())
"
```

### Step 4: Run Setup

```bash
python manage.py setup_endoreg_db
```

## Advanced Features

### 1. Dynamic Weight File Discovery

The system automatically discovers weight files using glob patterns:

```python
# Finds files like:
# - colo_segmentation_RegNetX800MF_v1.safetensors
# - colo_segmentation_RegNetX800MF_best.safetensors
# - image_multilabel_classification_colonoscopy_default_final.safetensors
```

### 2. Environment Variable Substitution

Use environment variables in paths:

```yaml
weights_search_dirs:
  - "${ENDOREG_MODEL_DIR}/weights"
  - "${STORAGE_DIR}/custom_models"
```

### 3. Model-Specific Configuration Override

Override global settings per model in the model YAML:

```yaml
setup_config:
  weight_filenames:
  - "special_model_*.safetensors"  # Override global patterns
  huggingface_fallback:
    repo_id: "different-org/special-model"  # Override global HF config
```

### 4. Fallback Chain

The system follows this priority order:
1. **Model-specific weight patterns** (from model YAML)
2. **Global weight patterns** (from setup_config.yaml)  
3. **HuggingFace download** (if enabled and no local files found)

## Troubleshooting

### Configuration Not Loading

```bash
# Check if configuration file exists and is valid
python manage.py shell -c "
from endoreg_db.utils.setup_config import SetupConfig
config = SetupConfig()
print('Config loaded:', config._config)
"
```

### Weights Not Found

```bash
# Check weight discovery
python manage.py shell -c "
from endoreg_db.utils.setup_config import setup_config
print('Search patterns:', setup_config.get_weights_search_patterns())
print('Search dirs:', setup_config.get_weights_search_dirs())
print('Found files:', setup_config.find_model_weights_files())
"
```

### HuggingFace Issues

```bash
# Test HuggingFace configuration
python manage.py shell -c "
from endoreg_db.utils.setup_config import setup_config
hf_config = setup_config.get_huggingface_config()
print('HF enabled:', hf_config.get('enabled'))
print('HF repo:', hf_config.get('repo_id'))
"
```

## Benefits

### ✅ **Fully Configurable**
- No more hardcoded model names
- Easy to adapt for new models
- Environment-specific configurations

### ✅ **Flexible Weight Discovery**
- Supports wildcards and patterns
- Multiple search directories
- Environment variable substitution

### ✅ **Robust Fallbacks**
- Local files → HuggingFace → Graceful failure
- Configurable at multiple levels
- Smart priority handling

### ✅ **Easy Migration**
- Backward compatible with existing setups
- Gradual migration path
- Clear configuration structure

### ✅ **Development Friendly**
- Different configs for dev/test/prod
- Easy to add new models
- Clear separation of concerns

This configurable approach makes the EndoReg DB setup system much more flexible and maintainable!