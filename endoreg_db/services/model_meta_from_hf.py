# endoreg_db/services/model_meta_from_hf.py

from pathlib import Path

from huggingface_hub import hf_hub_download

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.utils.filesystem.file_operations import (
    atomic_copy_file,
    ensure_directory,
)


def _model_meta_weights_exist(model_meta: ModelMeta) -> bool:
    if not model_meta.weights:
        return False
    try:
        return Path(model_meta.weights.path).exists()
    except (OSError, ValueError):
        return False


def _store_downloaded_weights(
    *,
    model_meta: ModelMeta,
    weights_path: Path,
    model_name: str,
    meta_version: str,
) -> None:
    relative_name = str(model_meta.weights.name or "").strip()
    if not relative_name:
        upload_to = str(model_meta.weights.field.upload_to).strip("/")
        filename = f"{model_name}_v{meta_version}.safetensors"
        relative_name = f"{upload_to}/{filename}" if upload_to else filename

    destination = Path(model_meta.weights.storage.path(relative_name))
    ensure_directory(destination.parent)
    atomic_copy_file(source=weights_path, destination=destination)
    model_meta.weights = relative_name
    model_meta.save(update_fields=["weights"])


def ensure_model_meta_from_hf(
    *,
    model_id: str,
    model_name: str,
    labelset_name: str,
    meta_version: str = "1",
    labelset_version: int | None = None,
) -> ModelMeta:
    """
    Download weights from Hugging Face (if needed) and ensure a ModelMeta
    exists for the given configuration. Returns the ModelMeta.
    """
    # Download the model weights
    weights_path = hf_hub_download(
        repo_id=model_id,
        filename="colo_segmentation_RegNetX800MF_base.safetensors",
        local_dir="/tmp",
    )

    # Get or create AI model
    ai_model, _ = AiModel.objects.get_or_create(
        name=model_name, defaults={"description": f"Model from {model_id}"}
    )

    # Get labelset
    labelset_qs = LabelSet.objects.filter(name=labelset_name)
    if labelset_version is not None:
        labelset_qs = labelset_qs.filter(version=labelset_version)
    labelset = labelset_qs.order_by("-version").first()
    if labelset is None:
        raise ValueError(
            f"LabelSet '{labelset_name}'"
            + (f" v{labelset_version}" if labelset_version is not None else "")
            + " not found"
        )

    # Create or get ModelMeta
    model_meta, _ = ModelMeta.objects.get_or_create(
        name=model_name,
        model=ai_model,
        version=meta_version,
        defaults={
            "labelset": labelset,
            "activation": "sigmoid",
            "mean": "0.45211223,0.27139644,0.19264949",
            "std": "0.31418097,0.21088019,0.16059452",
            "size_x": 716,
            "size_y": 716,
            "axes": "2,0,1",
            "batchsize": 16,
            "num_workers": 0,
            "description": f"Downloaded from {model_id}",
        },
    )

    # If weights file is missing, repair the existing field path or create it.
    if not _model_meta_weights_exist(model_meta):
        _store_downloaded_weights(
            model_meta=model_meta,
            weights_path=Path(weights_path).resolve(),
            model_name=model_name,
            meta_version=meta_version,
        )

    # Set as active meta
    ai_model.active_meta = model_meta
    ai_model.save(update_fields=["active_meta"])

    return model_meta
