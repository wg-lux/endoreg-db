# AI Model Configuration Guide

## Overview

This guide documents the comprehensive solution for EndoReg DB AI model configuration issues, including the integration between YAML-based configuration and automated setup processes.

## Problem Context

The EndoReg DB application was experiencing video processing failures with these specific errors:

1. **"No model metadata found for this model"** - AI models lacked proper metadata configuration
2. **"KeyError: 'file_path'"** - Video import service was not handling missing file paths defensively
3. **YAML configuration conflicts** - YAML-loaded models were being overwritten by setup auto-generation

## Solution Architecture

### 1. YAML-Based Configuration System

AI models and their metadata are now configured through structured YAML files:

**Location**: `/endoreg_db/data/ai_model_meta/`

**Example Configuration** (`default_multilabel_classification.yaml`):
```yaml
image_multilabel_classification_colonoscopy_default:
  model: image_multilabel_classification_colonoscopy_default
  labelset: multilabel_classification_colonoscopy_default
  version: 1
  name: image_multilabel_classification_colonoscopy_default
  description: Default multilabel classification model for colonoscopy video analysis with ImageNet preprocessing
  input_channels: 3
  input_height: 224
  input_width: 224
  activation: softmax
  normalization_mean: [0.485, 0.456, 0.406]
  normalization_std: [0.229, 0.224, 0.225]
   weights: model_weights/image_multilabel_classification_colonoscopy_default_v1_colo_segmentation_RegNetX800MF_6.safetensors
  created_at: "2024-01-01T00:00:00Z"
  updated_at: "2024-01-01T00:00:00Z"
```

### 2. Management Commands Integration

#### Loading YAML Configurations

**Command**: `python manage.py load_ai_model_data`

This command loads AI models and metadata from YAML files with proper foreign key relationships:

- **Models loaded**: AiModel, ModelMeta, VideoSegmentationLabel, VideoSegmentationLabelSet
- **Foreign key handling**: Automatically resolves relationships between models and labelsets
- **ModelMeta integration**: Re-enabled to support complete metadata loading

**Key implementation details**:
```python
IMPORT_METADATA = {
    ModelMeta.__name__: {
        "dir": AI_MODEL_META_DATA_DIR,
        "model": ModelMeta,
        "foreign_keys": ["labelset", "model"],
        "foreign_key_models": [LabelSet, AiModel],
    },
    # ... other models
}
```

#### Setup Command Enhancement

**Command**: `python manage.py setup_endoreg_db`

Enhanced with smart metadata handling:

- **YAML-aware**: Respects existing YAML-loaded configurations
- **Auto-generation**: Creates missing metadata only when needed
- **Weights handling**: Properly assigns FileField weights using `.name` property
- **Validation**: Comprehensive validation of AI model configurations

**New `--yaml-only` mode**:
```bash
python manage.py setup_endoreg_db --yaml-only
```
This mode skips auto-generation and relies entirely on YAML configurations.

### 3. Validation System

**Command**: `python manage.py validate_ai_models`

Comprehensive validation system that:
- Checks all AI models have proper metadata
- Validates metadata accessibility
- Reports detailed validation results
- Can be used for troubleshooting

### 4. Defensive Programming in Video Import

Enhanced `VideoImportService` with robust error handling:

```python
# Defensive file path access
file_path = getattr(video_file, 'file_path', None)
if not file_path:
    logger.warning(f"Video file {video_file.id} has no file_path, skipping...")
    continue
```

## Setup Workflow

### Option 1: YAML-First Setup (Recommended)

1. **Load YAML configurations first**:
   ```bash
   python manage.py load_ai_model_data
   ```

2. **Run setup in YAML-only mode**:
   ```bash
   python manage.py setup_endoreg_db --yaml-only
   ```

3. **Validate configuration**:
   ```bash
   python manage.py validate_ai_models
   ```

### Option 2: Auto-Generation Setup

1. **Run full setup** (will auto-generate missing metadata):
   ```bash
   python manage.py setup_endoreg_db
   ```

2. **Validate configuration**:
   ```bash
   python manage.py validate_ai_models
   ```

## File Structure

```
endoreg_db/
├── data/
│   └── ai_model_meta/
│       └── default_multilabel_classification.yaml
├── management/
│   └── commands/
│       ├── load_ai_model_data.py          # YAML loader
│       ├── setup_endoreg_db.py            # Enhanced setup
│       └── validate_ai_models.py          # Validation
├── models/
│   ├── administration/ai/
│   │   └── ai_model.py                    # AiModel model
│   └── metadata/
│       └── model_meta.py                  # ModelMeta model
└── services/
    └── video_import.py                    # Enhanced with defensive coding
```

## Key Features

### 1. Smart Weight Assignment

The setup command now properly handles Django FileField assignments:
```python
# Uses .name property instead of direct assignment
if weights_file.exists():
    metadata.weights.name = f"model_weights/{weights_file.name}"
    metadata.save()
```

### 2. Foreign Key Resolution

YAML loading properly resolves foreign key relationships:
- ModelMeta → AiModel (model field)
- ModelMeta → VideoSegmentationLabelSet (labelset field)

### 3. Conflict Prevention

The `--yaml-only` flag prevents setup from overwriting YAML-defined configurations:
```python
if yaml_only:
    self.stdout.write(f"  ⚠️  YAML-only mode: Skipping auto-generation for {model.name}")
    continue
```

## Validation and Testing

### Model Metadata Access Test

```python
from endoreg_db.models.administration.ai.ai_model import AiModel

for model in AiModel.objects.all():
    metadata = model.get_latest_version()
    print(f"Model: {model.name}")
    print(f"Metadata: {metadata}")
    print(f"Weights: {metadata.weights.name}")
```

### Service Integration Test

```python
from endoreg_db.services.video_import import VideoImportService

service = VideoImportService()  # Should initialize without errors
```

## Troubleshooting

### Common Issues

1. **"No model metadata found"**
   - Run: `python manage.py validate_ai_models`
   - If missing, run: `python manage.py setup_endoreg_db`

2. **"ValueError: The 'weights' attribute has no file associated"**
   - Ensure weights files exist in `storage/model_weights/`
   - Re-run setup to reassign weights properly

3. **YAML loading errors**
   - Check foreign key references in YAML match existing objects
   - Ensure proper model loading order (dependencies first)

### Validation Commands

```bash
# Check AI model configuration
python manage.py validate_ai_models

# Reload YAML configurations
python manage.py load_ai_model_data --verbosity=2

# Force recreate metadata
python manage.py setup_endoreg_db --force-recreate
```

## Migration Guide

### From Manual Setup to YAML Configuration

1. **Backup existing data**:
   ```bash
   python manage.py dumpdata endoreg_db.AiModel endoreg_db.ModelMeta > ai_models_backup.json
   ```

2. **Clear existing metadata** (optional):
   ```bash
   python manage.py shell -c "from endoreg_db.models.metadata.model_meta import ModelMeta; ModelMeta.objects.all().delete()"
   ```

3. **Load YAML configurations**:
   ```bash
   python manage.py load_ai_model_data
   ```

4. **Validate new setup**:
   ```bash
   python manage.py validate_ai_models
   ```

## Best Practices

1. **Always validate after changes**: Use `validate_ai_models` command
2. **Use YAML-first approach**: Define configurations in YAML for consistency
3. **Test service integration**: Ensure VideoImportService can access metadata
4. **Monitor weights files**: Ensure model weight files exist and are accessible
5. **Version control YAML**: Keep AI model configurations in version control

## Performance Considerations

- YAML loading is performed once during setup
- Model metadata is cached in database
- FileField weights are stored as paths, not loaded into memory
- Validation commands are designed for development/debugging use

## Security Notes

- Model weight files should be stored securely
- YAML configurations may contain sensitive model information
- Ensure proper access controls on storage directories

---

## Summary

This configuration system provides:
- ✅ Robust AI model metadata management
- ✅ YAML-based configuration with database integration
- ✅ Defensive error handling in video processing
- ✅ Comprehensive validation and troubleshooting tools
- ✅ Flexible setup workflows for different deployment scenarios

The integration successfully resolves the original video processing failures while providing a maintainable and extensible configuration system.