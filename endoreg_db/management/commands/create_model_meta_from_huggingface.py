"""
Django management command to create ModelMeta from Hugging Face model.
"""

from pathlib import Path
from importlib import import_module
from typing import BinaryIO, Protocol, cast

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.huggingface_model_meta import (
    HuggingFaceModelMetaCommandValue,
    validate_huggingface_model_meta_command_payload,
)

from endoreg_db.models import AiModel, LabelSet, ModelMeta

MODEL_WEIGHTS_FILENAME = "colo_segmentation_RegNetX800MF_base.safetensors"


class _HfHubDownload(Protocol):
    def __call__(
        self,
        *,
        repo_id: str,
        filename: str,
        local_dir: str,
    ) -> str: ...


class _NamedAiModel(Protocol):
    name: str
    active_meta: "ModelMeta"

    def save(self) -> None: ...


class _WeightedModelMeta(Protocol):
    weights: "_ModelWeightsFile"


class _ModelWeightsFile(Protocol):
    def save(self, name: str, content: ContentFile[bytes]) -> None: ...


hf_hub_download = cast(
    _HfHubDownload,
    getattr(import_module("huggingface_hub"), "hf_hub_download"),
)


class Command(BaseCommand):
    help = "Create ModelMeta by downloading model from Hugging Face"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--model_id",
            type=str,
            default="wg-lux/colo_segmentation_RegNetX800MF_base",
            help="Hugging Face model ID",
        )
        parser.add_argument(
            "--model_name",
            type=str,
            default="image_multilabel_classification_colonoscopy_default",
            help="Name for the AI model",
        )
        parser.add_argument(
            "--labelset_name",
            type=str,
            default="multilabel_classification_colonoscopy_default",
            help="Name of the labelset",
        )
        parser.add_argument(
            "--meta_version",
            type=str,
            default="1",
            help="Version for the model meta",
        )
        parser.add_argument(
            "--labelset_version",
            type=int,
            default=None,
            help="LabelSet version; if omitted, the latest by name is used",
        )

    def handle(
        self,
        *args: str,
        **options: HuggingFaceModelMetaCommandValue,
    ) -> None:
        try:
            payload = validate_huggingface_model_meta_command_payload(options)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Downloading model {payload.model_id} from Hugging Face...")

        try:
            # Download the model weights
            weights_path = Path(
                hf_hub_download(
                    repo_id=payload.model_id,
                    filename=MODEL_WEIGHTS_FILENAME,
                    local_dir="/tmp",
                )
            )
            self.stdout.write(f"Downloaded weights to: {weights_path}")

            # Get or create AI model
            ai_model, created = AiModel.objects.get_or_create(
                name=payload.model_name,
                defaults={"description": f"Model from {payload.model_id}"},
            )
            typed_ai_model = cast(_NamedAiModel, ai_model)
            if created:
                self.stdout.write(f"Created AI model: {typed_ai_model.name}")

            # Get labelset (optionally by version); fail with non-zero exit
            labelset_qs = LabelSet.objects.filter(name=payload.labelset_name)
            if payload.labelset_version is not None:
                labelset_qs = labelset_qs.filter(version=payload.labelset_version)
            labelset = labelset_qs.order_by("-version").first()
            if labelset is None:
                raise CommandError(
                    f"LabelSet '{payload.labelset_name}'"
                    + (
                        f" v{payload.labelset_version}"
                        if payload.labelset_version is not None
                        else ""
                    )
                    + " not found"
                )

            # Create ModelMeta
            model_meta, created = ModelMeta.objects.get_or_create(
                name=payload.model_name,
                model=ai_model,
                version=payload.meta_version,
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
                    "description": f"Downloaded from {payload.model_id}",
                },
            )
            typed_model_meta = cast(_WeightedModelMeta, model_meta)

            # Save the weights file to the model
            with weights_path.open("rb") as weights_file:
                _save_model_weights(
                    model_meta=typed_model_meta,
                    model_name=payload.model_name,
                    version=payload.meta_version,
                    weights_file=weights_file,
                )

            # Set as active meta
            typed_ai_model.active_meta = model_meta
            typed_ai_model.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully {'created' if created else 'updated'} ModelMeta: {model_meta}"
                )
            )

        except CommandError:
            raise
        except Exception as exc:
            raise CommandError("ModelMeta creation failed") from exc


def _save_model_weights(
    *,
    model_meta: _WeightedModelMeta,
    model_name: str,
    version: str,
    weights_file: BinaryIO,
) -> None:
    model_meta.weights.save(
        f"{model_name}_v{version}.safetensors",
        ContentFile[bytes](weights_file.read()),
    )
