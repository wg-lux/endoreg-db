# endoreg_db/services/model_meta_from_hf.py

from importlib import import_module
from pathlib import Path
from logging import getLogger
from typing import Any, Protocol, cast

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from endoreg_db.models.utils import WEIGHTS_DIR
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
)
from lx_dtypes.models.contracts.huggingface_model_meta import (
    HuggingFaceModelMetaCommandPayload,
)

logger = getLogger(__name__)


class _HfHubDownload(Protocol):
    def __call__(
        self, *, repo_id: str, filename: str, local_dir: str | Path
    ) -> str: ...


class _StorageSave(Protocol):
    def __call__(
        self, name: str, content: Any, max_length: int | None = None
    ) -> str: ...


def hf_hub_download(
    *,
    repo_id: str,
    filename: str,
    local_dir: str | Path,
) -> str:
    hf_hub_download_typed = cast(
        _HfHubDownload,
        getattr(import_module("huggingface_hub"), "hf_hub_download"),
    )
    return hf_hub_download_typed(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
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
) -> None:
    relative_name = ""
    if model_meta.weights.name:
        relative_name = str(model_meta.weights.name)

    if not relative_name:
        relative_name = (
            f"{WEIGHTS_DIR.name}/{model_meta.name}_v{model_meta.version}.safetensors"
        )

    try:
        destination = Path(model_meta.weights.storage.path(relative_name))
        atomic_copy_file(source=weights_path, destination=destination)
        model_meta.weights.name = relative_name
        model_meta.save(update_fields=["weights"])
    except TypeError:
        with weights_path.open("rb") as source_file:
            storage_save = cast(_StorageSave, model_meta.weights.storage.save)
            saved_name = storage_save(
                relative_name,
                ContentFile[bytes](source_file.read()),
            )
            model_meta.weights.name = saved_name
            model_meta.save(update_fields=["weights"])


def _get_or_create_ai_model(*, model_name: str, model_id: str) -> AiModel:
    try:
        with transaction.atomic():
            ai_model, _ = AiModel.objects.get_or_create(
                name=model_name,
                defaults={"description": f"Model from {model_id}"},
            )
            return ai_model
    except IntegrityError:
        ai_model = AiModel.objects.filter(name=model_name).first()
        if ai_model is None:
            raise
        logger.info(
            "AiModel '%s' already exists after concurrent creation; reusing row %s.",
            model_name,
            ai_model.pk,
        )
        return ai_model


def _get_or_create_model_meta(
    *,
    ai_model: AiModel,
    labelset: LabelSet,
    model_id: str,
    model_name: str,
    meta_version: str,
) -> ModelMeta:
    defaults = {
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
    }
    try:
        with transaction.atomic():
            model_meta, _ = ModelMeta.objects.get_or_create(
                name=model_name,
                model=ai_model,
                version=meta_version,
                defaults=defaults,
            )
            return model_meta
    except IntegrityError:
        model_meta = ModelMeta.objects.filter(
            name=model_name,
            model=ai_model,
            version=meta_version,
        ).first()
        if model_meta is None:
            raise
        logger.info(
            "ModelMeta '%s' v%s for AiModel '%s' already exists after "
            "concurrent creation; reusing row %s.",
            model_name,
            meta_version,
            ai_model.name,
            model_meta.pk,
        )
        return model_meta


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
    payload = HuggingFaceModelMetaCommandPayload.model_validate(
        {
            "model_id": model_id,
            "model_name": model_name,
            "labelset_name": labelset_name,
            "meta_version": str(meta_version),
            "labelset_version": labelset_version,
        }
    )
    meta_version = payload.meta_version
    model_id = payload.model_id
    model_name = payload.model_name
    labelset_name = payload.labelset_name

    # Download the model weights
    weights_path = hf_hub_download(
        repo_id=model_id,
        filename="colo_segmentation_RegNetX800MF_base.safetensors",
        local_dir="/tmp",
    )

    # Get or create AI model
    ai_model = _get_or_create_ai_model(model_name=model_name, model_id=model_id)

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
    model_meta = _get_or_create_model_meta(
        ai_model=ai_model,
        labelset=labelset,
        model_id=model_id,
        model_name=model_name,
        meta_version=meta_version,
    )

    # If weights file is missing, repair the existing field path or create it.
    if not _model_meta_weights_exist(model_meta):
        _store_downloaded_weights(
            model_meta=model_meta,
            weights_path=Path(weights_path).resolve(),
        )

    # Set as active meta
    if ai_model.active_meta is None or ai_model.active_meta.pk != model_meta.pk:
        ai_model.active_meta = model_meta
        ai_model.save(update_fields=["active_meta"])

    return model_meta
