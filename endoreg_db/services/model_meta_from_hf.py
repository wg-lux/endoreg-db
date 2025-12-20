# endoreg_db/services/model_meta_from_hf.py

from django.core.files.base import ContentFile
from huggingface_hub import hf_hub_download

from endoreg_db.models import AiModel, LabelSet, ModelMeta


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

    # If weights file not yet saved, save it
    if not model_meta.weights:
        with open(weights_path, "rb") as f:
            model_meta.weights.save(
                f"{model_name}_v{meta_version}.safetensors",
                ContentFile(f.read()),
            )

    # Set as active meta
    ai_model.active_meta = model_meta
    ai_model.save(update_fields=["active_meta"])

    return model_meta
