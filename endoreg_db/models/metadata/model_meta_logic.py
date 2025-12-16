import shutil
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Type

from django.db import transaction
from huggingface_hub import hf_hub_download

# Assuming ModelMeta, AiModel, LabelSet are importable from the correct locations
# Adjust imports based on your project structure if necessary
from ..administration.ai.ai_model import AiModel
from ..label.label_set import LabelSet
from ..utils import STORAGE_DIR, WEIGHTS_DIR

logger = getLogger("ai_model")

if TYPE_CHECKING:
    from .model_meta import ModelMeta  # Import ModelMeta for type hinting


def _get_model_meta_class():
    """Lazy import to avoid circular imports"""
    from .model_meta import ModelMeta

    return ModelMeta


def get_latest_version_number_logic(cls: Type["ModelMeta"], meta_name: str, model_name: str) -> int:
    """
    Finds the highest numerical version for a given meta_name and model_name.
    Iterates through all versions, attempts to parse them as integers,
    and returns the maximum integer found. If no numeric versions are found,
    returns 0.
    """
    versions_qs = cls.objects.filter(name=meta_name, model__name=model_name).values_list("version", flat=True)

    max_v = 0
    found_numeric_version = False

    for v_str in versions_qs:
        if v_str is None:  # Skip None versions
            continue
        try:
            v_int = int(v_str)
            if not found_numeric_version or v_int > max_v:
                max_v = v_int
            found_numeric_version = True
        except ValueError:
            logger.warning(
                f"Warning: Could not parse version string '{v_str}' as an integer for "
                f"meta_name='{meta_name}', model_name='{model_name}' while determining the max version."
            )

    return max_v if found_numeric_version else 0


@transaction.atomic
def create_from_file_logic(
    cls: Type["ModelMeta"],  # cls is ModelMeta
    meta_name: str,
    model_name: str,
    labelset_name: str,
    weights_file: str,
    labelset_version: Optional[int | str] = None,
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

    labelset_qs = LabelSet.objects.filter(name=labelset_name)
    if labelset_version not in (None, "", -1):
        try:
            version_value = int(labelset_version)
        except (TypeError, ValueError):
            version_value = labelset_version
        labelset_qs = labelset_qs.filter(version=version_value)

    try:
        label_set = labelset_qs.get()
    except LabelSet.DoesNotExist as exc:
        raise ValueError(f"LabelSet '{labelset_name}' with version '{labelset_version}' not found.") from exc
    except LabelSet.MultipleObjectsReturned:
        # Prefer the highest version when duplicates remain and no explicit version requested
        label_set = labelset_qs.order_by("-version").first()
        if not label_set:
            raise ValueError(f"LabelSet '{labelset_name}' could not be resolved.")

    # --- Determine Version ---
    target_version: str
    latest_version_num = get_latest_version_number_logic(cls, meta_name, model_name)

    if requested_version:
        target_version = str(requested_version)
        existing = cls.objects.filter(name=meta_name, model=ai_model, version=target_version).first()
        if existing and not bump_if_exists:
            raise ValueError(
                f"ModelMeta '{meta_name}' version '{target_version}' for model '{model_name}' already exists. Use bump_if_exists=True to increment."
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
        "weights": relative_dest_path.as_posix(),  # Store relative path for FileField
        **kwargs,  # Pass through other fields like activation, mean, std, etc.
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
    ai_model.active_meta = model_meta
    ai_model.save()

    return model_meta


# --- Add other logic functions referenced by ModelMeta here ---
# (get_latest_version_number_logic, get_activation_function_logic, etc.)
# Placeholder for get_activation_function_logic
def get_activation_function_logic(activation_name: str):
    import torch.nn as nn  # Import locally as it's specific to this function

    if activation_name.lower() == "sigmoid":
        return nn.Sigmoid()
    elif activation_name.lower() == "softmax":
        # Note: Softmax usually requires specifying the dimension
        return nn.Softmax(dim=1)  # Assuming dim=1 (channels) is common
    elif activation_name.lower() == "none":
        return nn.Identity()
    else:
        # Consider adding more activations or raising an error
        raise ValueError(f"Unsupported activation function: {activation_name}")


# Placeholder for get_inference_dataset_config_logic
def _normalise_scalar_sequence(value: Any) -> str | None:
    """Serialise sequence-like values into the canonical comma-separated form."""

    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _parse_float_sequence(value: Any, *, fallback: Iterable[float]) -> list[float]:
    """Coerce stored normalisation strings/iterables to float lists."""

    if value in (None, ""):
        return list(fallback)

    tokens: Iterable[Any]
    if isinstance(value, str):
        tokens = [tok.strip() for tok in value.split(",") if tok.strip()]
    elif isinstance(value, (list, tuple)):
        tokens = value
    else:
        tokens = [value]

    parsed: list[float] = []
    for token in tokens:
        try:
            parsed.append(float(token))
        except (TypeError, ValueError):
            logger.warning("Failed to parse normalisation value %r; using fallback", token)
            return list(fallback)

    return parsed or list(fallback)


def _parse_axes(axes_value: str | Iterable[Any]) -> list[int]:
    """Normalise stored axis notation to integer indices.

    Historic metadata mixes numeric strings ("2,0,1") with symbolic tokens
    ("CHW", "HWC"). The inference code ultimately expects a list of integers,
    so we coerce common symbolic forms to their numeric equivalents and fall
    back to sequential indices when the format is unknown.
    """

    if not axes_value:
        return [0, 1, 2]

    if isinstance(axes_value, str):
        token_source = axes_value.strip()
        if "," in token_source:
            tokens = [token.strip() for token in token_source.split(",") if token.strip()]
        else:
            tokens = [char for char in token_source if char.strip()]
    else:
        tokens = [str(token).strip() for token in axes_value if str(token).strip()]

    symbol_map = {"C": 0, "H": 1, "W": 2}
    parsed_axes: list[int] = []

    for idx, token in enumerate(tokens):
        normalised = token.upper()
        if normalised.lstrip("+-").isdigit():
            parsed_axes.append(int(normalised))
        elif normalised in symbol_map:
            parsed_axes.append(symbol_map[normalised])
        else:
            logger.warning("Unknown axes token '%s'; defaulting to index order", token)
            parsed_axes.append(idx)

    return parsed_axes or [0, 1, 2]


def get_inference_dataset_config_logic(model_meta: "ModelMeta") -> dict:
    # This would typically extract relevant fields from model_meta
    # for configuring a dataset during inference
    DEFAULT_MEAN = (0.45211223, 0.27139644, 0.19264949)
    DEFAULT_STD = (0.31418097, 0.21088019, 0.16059452)

    return {
        "mean": _parse_float_sequence(model_meta.mean, fallback=DEFAULT_MEAN),
        "std": _parse_float_sequence(model_meta.std, fallback=DEFAULT_STD),
        "size_y": int(model_meta.size_y) if model_meta.size_y else 716,
        "size_x": int(model_meta.size_x) if model_meta.size_x else 716,
        "axes": _parse_axes(model_meta.axes),
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
            raise cls.DoesNotExist(f"ModelMeta '{meta_name}' version '{version}' for model '{model_name}' not found.") from exc
    else:
        # Get latest version
        latest = cls.objects.filter(name=meta_name, model=ai_model).order_by("-date_created").first()
        if latest:
            return latest
        else:
            raise cls.DoesNotExist(f"No ModelMeta found for '{meta_name}' and model '{model_name}'.")


import re

from huggingface_hub import model_info


def infer_default_model_meta_from_hf(model_id: str) -> dict[str, Any]:
    """
    Infers default model metadata (activation, normalization, input size)
    from a Hugging Face model_id using its tags and architecture.

    Returns:
        A dict with fields: name, activation, mean, std, size_x, size_y
    """

    if not (info := model_info(model_id)):
        logger.info(f"Could not retrieve model info for {model_id}, using ColoReg segmentation defaults.")
        return {
            "name": "wg-lux/colo_segmentation_RegNetX800MF_base",
            "activation": "sigmoid",
            "mean": (0.45211223, 0.27139644, 0.19264949),
            "std": (0.31418097, 0.21088019, 0.16059452),
            "size_x": 716,
            "size_y": 716,
            "description": f"Defaults for unknown model {model_id}",
        }

    # Extract architecture from tags or model_id ---
    tags = info.tags or []
    model_name = model_id.split("/")[-1].lower()

    # Heuristics for architecture and task
    architecture = next((t for t in tags if t.startswith("architecture:")), None)
    task = next((t for t in tags if t.startswith("task:")), None)

    # Default values
    activation = "sigmoid"
    size_x = size_y = 716
    mean = (0.45211223, 0.27139644, 0.19264949)
    std = (0.31418097, 0.21088019, 0.16059452)

    # --- 2. Task-based inference ---
    if task:
        if "segmentation" in task or "detection" in task:
            activation = "sigmoid"
        elif any(k in task for k in ["classification"]):
            activation = "softmax"

    # --- 3. Architecture-based inference ---
    if architecture:
        arch = architecture.replace("architecture:", "")
    else:
        arch = re.sub(r"[^a-z0-9]+", "_", model_name)

    return {
        "name": arch,
        "activation": activation,
        "mean": mean,
        "std": std,
        "size_x": size_x,
        "size_y": size_y,
        "description": f"Inferred defaults for {model_id}",
    }


def setup_default_from_huggingface_logic(
    cls,
    model_id: str,
    labelset_name: str | None = None,
    labelset_version: Optional[int | str] = None,
):
    """
    Downloads model weights from Hugging Face and auto-fills ModelMeta fields.
    """
    meta = infer_default_model_meta_from_hf(model_id)

    # Download safetensor weights; raise a clear error if unavailable
    try:
        weights_path = hf_hub_download(
            repo_id=model_id,
            filename="colo_segmentation_RegNetX800MF_base.safetensors",
            local_dir=WEIGHTS_DIR,
        )
    except Exception as exc:  # pragma: no cover - network errors
        raise RuntimeError("Failed to download safetensor weights from Hugging Face; ensure the repository provides a .safetensors artifact.") from exc

    ai_model, _ = AiModel.objects.get_or_create(name=meta["name"])
    if not labelset_name:
        labelset = LabelSet.objects.first()
        if not labelset:
            raise ValueError("No labelset found and no labelset_name provided")
    else:
        labelset_qs = LabelSet.objects.filter(name=labelset_name)
        if labelset_version not in (None, "", -1):
            try:
                version_value = int(labelset_version)
            except (TypeError, ValueError):
                version_value = labelset_version
            labelset_qs = labelset_qs.filter(version=version_value)
        labelset = labelset_qs.order_by("-version").first()
        if not labelset:
            raise ValueError(f"LabelSet '{labelset_name}' with version '{labelset_version}' not found.")

    ModelMeta = _get_model_meta_class()
    model_meta = ModelMeta.objects.filter(name=meta["name"], model=ai_model).first()
    if model_meta:
        logger.info(f"ModelMeta {meta['name']} for model {ai_model.name} already exists. Skipping creation.")
        return model_meta

    return create_from_file_logic(
        cls,
        meta_name=meta["name"],
        model_name=ai_model.name,
        labelset_name=labelset.name,
        labelset_version=labelset.version,
        weights_file=weights_path,
        activation=meta["activation"],
        mean=meta["mean"],
        std=meta["std"],
        size_x=meta["size_x"],
        size_y=meta["size_y"],
        description=meta["description"],
    )
