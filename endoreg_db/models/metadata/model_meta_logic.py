import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Any, Type

from django.db import transaction

# Assuming ModelMeta, AiModel, LabelSet are importable from the correct locations
# Adjust imports based on your project structure if necessary
from ..administration.ai.ai_model import AiModel
from ..label.label_set import LabelSet
from ..utils import WEIGHTS_DIR, STORAGE_DIR

from logging import getLogger

logger = getLogger("ai_model")

if TYPE_CHECKING:
    from .model_meta import ModelMeta # Import ModelMeta for type hinting


def get_latest_version_number_logic(
    cls: Type["ModelMeta"], meta_name: str, model_name: str
) -> int:
    """
    Finds the highest numerical version for a given meta_name and model_name.
    Assumes versions are simple integers or can be cast to integers.
    """
    try:
        latest = cls.objects.filter(
            name=meta_name, model__name=model_name
        ).latest("date_created") # Or order by version if it's reliably numeric/sortable
        # Attempt to convert version to int, default to 0 if fails or no version exists
        return int(latest.version) if latest and latest.version else 0
    except cls.DoesNotExist:
        return 0
    except ValueError: # Handle cases where version might not be purely numeric
        # Add more sophisticated version parsing if needed (e.g., semantic versioning)
        logger.warning(f"Warning: Could not parse version '{latest.version}' as integer for {meta_name}/{model_name}. Defaulting to 0.")
        return 0


@transaction.atomic
def create_from_file_logic(
    cls: Type["ModelMeta"], # cls is ModelMeta
    meta_name: str,
    model_name: str,
    labelset_name: str,
    weights_file: str,
    requested_version: Optional[str] = None,
    bump_if_exists: bool = False,
    **kwargs: Any,
) -> "ModelMeta":
    """
    Logic to create or update a ModelMeta instance from a weights file.

    Handles finding related objects, versioning, file copying, and saving.
    """
    # --- Find Related Objects ---
    try:
        ai_model = AiModel.objects.get(name=model_name)
    except AiModel.DoesNotExist as exc:
        raise ValueError(f"AiModel with name '{model_name}' not found.") from exc

    try:
        label_set = LabelSet.objects.get(name=labelset_name)
    except LabelSet.DoesNotExist as exc:
        raise ValueError(f"LabelSet with name '{labelset_name}' not found.") from exc

    # --- Determine Version ---
    target_version: str
    latest_version_num = get_latest_version_number_logic(cls, meta_name, model_name)

    if requested_version:
        target_version = str(requested_version)
        existing = cls.objects.filter(
            name=meta_name, model=ai_model, version=target_version
        ).first()
        if existing and not bump_if_exists:
            raise ValueError(
                f"ModelMeta '{meta_name}' version '{target_version}' for model '{model_name}' "
                f"already exists. Use bump_if_exists=True to increment."
            )
        elif existing and bump_if_exists:
            target_version = str(latest_version_num + 1)
            logger.info(f"Bumping version for {meta_name}/{model_name} to {target_version}")
    else:
        target_version = str(latest_version_num + 1)
        logger.info(f"Setting next version for {meta_name}/{model_name} to {target_version}")


    # --- Prepare Weights File ---
    source_weights_path = Path(weights_file).resolve()
    if not source_weights_path.exists():
        raise FileNotFoundError(f"Weights file not found: {source_weights_path}")

    # Construct destination path within MEDIA_ROOT/WEIGHTS_DIR
    weights_filename = source_weights_path.name
    # Relative path for the FileField upload_to
    relative_dest_path = Path(WEIGHTS_DIR.relative_to(STORAGE_DIR)) / f"{meta_name}_v{target_version}_{weights_filename}"
    # Full path for shutil.copy
    full_dest_path = STORAGE_DIR / relative_dest_path

    # Ensure the destination directory exists
    full_dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy the file
    try:
        shutil.copy(source_weights_path, full_dest_path)
        logger.info(f"Copied weights from {source_weights_path} to {full_dest_path}")
    except Exception as e:
        raise IOError(f"Failed to copy weights file: {e}") from e

    # --- Create/Update ModelMeta Instance ---
    defaults = {
        "labelset": label_set,
        "weights": relative_dest_path.as_posix(), # Store relative path for FileField
        **kwargs, # Pass through other fields like activation, mean, std, etc.
    }

    # Remove None values from defaults to avoid overriding model defaults unnecessarily
    defaults = {k: v for k, v in defaults.items() if v is not None}

    model_meta, created = cls.objects.update_or_create(
        name=meta_name,
        model=ai_model,
        version=target_version,
        defaults=defaults,
    )

    if created:
        logger.info(f"Created new ModelMeta: {model_meta}")
    else:
        logger.info(f"Updated existing ModelMeta: {model_meta}")

    # --- Optionally update AiModel's active_meta ---
    # You might want to add logic here to automatically set the newly created/updated
    # meta as the active one for the AiModel, e.g.:
    # ai_model.active_meta = model_meta
    # ai_model.save()

    return model_meta

# --- Add other logic functions referenced by ModelMeta here ---
# (get_latest_version_number_logic, get_activation_function_logic, etc.)
# Placeholder for get_activation_function_logic
def get_activation_function_logic(activation_name: str):
    import torch.nn as nn # Import locally as it's specific to this function
    if activation_name.lower() == "sigmoid":
        return nn.Sigmoid()
    elif activation_name.lower() == "softmax":
        # Note: Softmax usually requires specifying the dimension
        return nn.Softmax(dim=1) # Assuming dim=1 (channels) is common
    elif activation_name.lower() == "none":
        return nn.Identity()
    else:
        # Consider adding more activations or raising an error
        raise ValueError(f"Unsupported activation function: {activation_name}")

# Placeholder for get_inference_dataset_config_logic
def get_inference_dataset_config_logic(model_meta: "ModelMeta") -> dict:
    # This would typically extract relevant fields from model_meta
    # for configuring a dataset during inference
    return {
        "mean": [float(x) for x in model_meta.mean.split(',')],
        "std": [float(x) for x in model_meta.std.split(',')],
        "size_y": model_meta.size_y, # Add size_y key
        "size_x": model_meta.size_x, # Add size_x key
        "axes": [int(x) for x in model_meta.axes.split(',')],
        # Add other relevant config like normalization type, etc.
    }

# Placeholder for get_config_dict_logic
def get_config_dict_logic(model_meta: "ModelMeta") -> dict:
    # Returns a dictionary representation of the model's configuration
    return {
        "name": model_meta.name,
        "version": model_meta.version,
        "model_name": model_meta.model.name,
        "labelset_name": model_meta.labelset.name,
        "activation": model_meta.activation,
        "weights_path": model_meta.weights.path if model_meta.weights else None,
        "mean": model_meta.mean,
        "std": model_meta.std,
        "size_x": model_meta.size_x,
        "size_y": model_meta.size_y,
        "axes": model_meta.axes,
        "batchsize": model_meta.batchsize,
        "num_workers": model_meta.num_workers,
        "description": model_meta.description,
        # Add any other relevant fields
    }

# Placeholder for get_model_meta_by_name_version_logic
def get_model_meta_by_name_version_logic(
    cls: Type["ModelMeta"],
    meta_name: str,
    model_name: str,
    version: Optional[str] = None,
) -> "ModelMeta":
    """
    Retrieves a ModelMeta instance by name, model name, and optionally version.
    If version is None, retrieves the latest version based on date_created.
    """
    try:
        ai_model = AiModel.objects.get(name=model_name)
    except AiModel.DoesNotExist as exc:
        raise cls.DoesNotExist(f"AiModel with name '{model_name}' not found.") from exc

    if version:
        try:
            return cls.objects.get(name=meta_name, model=ai_model, version=version)
        except Exception as exc:
            raise cls.DoesNotExist(
                f"ModelMeta '{meta_name}' version '{version}' for model '{model_name}' not found."
            ) from exc
    else:
        # Get latest version
        latest = cls.objects.filter(name=meta_name, model=ai_model).order_by("-date_created").first()
        if latest:
            return latest
        else:
            raise cls.DoesNotExist(
                f"No ModelMeta found for '{meta_name}' and model '{model_name}'."
            )
