# endoreg_db/management/commands/model_input.py

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.data_loader_for_model_input import (
    ANNOTATION_SOURCE_SCOPE_ALL,
    VALID_ANNOTATION_SOURCE_SCOPES,
    build_dataset_for_training,
    normalize_annotation_source_scope,
)
from lx_dtypes.models.contracts.management_command import (
    ModelInputCommandOptionsPayload,
    validate_model_training_result,
)
from lx_dtypes.models.contracts.json_types import JsonObject


class _AIDataSetFields(Protocol):
    pk: object
    name: str
    dataset_type: str
    ai_model_type: str


class _LabelSetFields(Protocol):
    pk: object
    name: str
    version: int


class _LabelFields(Protocol):
    pk: object
    name: str


def _model_pk_as_int(value: object, *, model_name: str) -> int:
    if isinstance(value, int):
        return value
    raise CommandError(f"{model_name} primary key must be an integer.")


class Command(BaseCommand):
    help = (
        "Build the dynamic multi-label dataset from AIDataSet and train a "
        "GastroNet-ResNet50 multi-label model on it.\n"
        "\n"
        "This command:\n"
        "- Uses AIDataSet.id to select annotations.\n"
        "- Infers the LabelSet from used labels.\n"
        "- Builds image_paths, label_vectors, and label_masks from DB.\n"
        "- Prints a short debug dump.\n"
        "- Trains a model using RN50 GastroNet checkpoint (if provided).\n"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dataset-id",
            type=int,
            required=True,
            help="Primary key of the AIDataSet to use for training.",
        )
        parser.add_argument(
            "--annotation-source-scope",
            type=str,
            default=ANNOTATION_SOURCE_SCOPE_ALL,
            choices=sorted(VALID_ANNOTATION_SOURCE_SCOPES),
            help=(
                "Annotation sources within the AIDataSet to use: all, "
                "frame_only, or segment_only."
            ),
        )
        parser.add_argument(
            "--backbone-checkpoint",
            type=str,
            default=None,
            help=(
                "Path to RN50_GastroNet-1M_DINOv1.pth (or similar). "
                "If omitted, ResNet50 is randomly initialized."
            ),
        )
        parser.add_argument(
            "--backbone-name",
            type=str,
            default="gastro_rn50",
            help=(
                "Backbone name, e.g. 'gastro_rn50' (default), "
                "'resnet50_imagenet', 'resnet50_random', 'efficientnet_b0_imagenet', etc."
            ),
        )
        parser.add_argument(
            "--epochs",
            type=int,
            default=10,
            help="Number of training epochs.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            payload = ModelInputCommandOptionsPayload.model_validate(options)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        dataset_id = payload.dataset_id
        try:
            annotation_source_scope = normalize_annotation_source_scope(
                payload.annotation_source_scope
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        backbone_ckpt = payload.backbone_checkpoint
        backbone_name = payload.backbone_name
        num_epochs = payload.epochs

        try:
            dataset = AIDataSet.objects.get(id=dataset_id)
        except AIDataSet.DoesNotExist:
            raise CommandError(f"AIDataSet with id={dataset_id} does not exist.")
        dataset_fields = cast(_AIDataSetFields, dataset)
        dataset_pk = _model_pk_as_int(dataset_fields.pk, model_name="AIDataSet")

        # Basic info
        self.stdout.write(
            self.style.NOTICE(
                f"Using AIDataSet id={dataset_pk}, "
                f"name={dataset_fields.name!r}, "
                f"dataset_type={dataset_fields.dataset_type!r}, "
                f"ai_model_type={dataset_fields.ai_model_type!r}"
            )
        )

        data = build_dataset_for_training(
            dataset,
            annotation_source_scope=annotation_source_scope,
        )

        image_paths = data["image_paths"]
        label_vectors = data["label_vectors"]
        label_masks = data["label_masks"]
        labels = data["labels"]
        labelset = data["labelset"]

        self.stdout.write(self.style.NOTICE("Inferred LabelSet for this AIDataSet:"))
        labelset_fields = cast(_LabelSetFields, labelset)
        labelset_pk = _model_pk_as_int(labelset_fields.pk, model_name="LabelSet")
        self.stdout.write(
            f"  LabelSet id={labelset_pk}, "
            f"name={labelset_fields.name!r}, "
            f"version={labelset_fields.version}"
        )
        self.stdout.write("  Labels (index, id, name):")
        for idx, lbl in enumerate(labels):
            label_fields = cast(_LabelFields, lbl)
            label_pk = _model_pk_as_int(label_fields.pk, model_name="Label")
            self.stdout.write(f"    [{idx}] id={label_pk}, name={label_fields.name!r}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nBuilt training dataset from AIDataSet id={dataset_pk}:\n"
                f"- #samples: {len(image_paths)}\n"
                f"- #labels:  {len(labels)}"
            )
        )

        MAX_PRINT = 10
        self.stdout.write(self.style.NOTICE("\nPer-sample debug output (first 10):"))
        for i, (path, vec, mask) in enumerate(
            zip(image_paths, label_vectors, label_masks)
        ):
            if i >= MAX_PRINT:
                self.stdout.write(
                    self.style.WARNING(
                        f"... ({len(image_paths) - MAX_PRINT} more samples not shown)"
                    )
                )
                break

            self.stdout.write(
                f"  Sample {i}:"
                f"\n    path = {path!r}"
                f"\n    vector (1/0/None) = {vec}"
                f"\n    mask (1=use, 0=ignore) = {mask}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n Input for model training built successfully from AIDataSet id={dataset_pk}."
            )
        )

        # ------------------------------------------------------------------
        # Ask user if we should really start training
        # ------------------------------------------------------------------
        self.stdout.write("")
        confirm = (
            input(
                "Proceed with model training? "
                "Type 'yes' and press Enter to continue, anything else to abort: "
            )
            .strip()
            .lower()
        )

        if confirm != "yes":
            self.stdout.write(
                self.style.WARNING("Training aborted by user. No model was trained.")
            )
            return

        # ---- Training ----
        try:
            from endoreg_db.utils.ai.model_training.config import TrainingConfig

            trainer_module = import_module(
                "endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel"
            )
        except ImportError as exc:
            raise CommandError(
                "Training dependencies are not available. Install the AI training "
                "dependencies before running model_input."
            ) from exc
        train_candidate: object = getattr(trainer_module, "train_gastronet_multilabel")
        if not callable(train_candidate):
            raise CommandError("train_gastronet_multilabel is not callable.")
        train_model = cast(
            Callable[[TrainingConfig], JsonObject],
            train_candidate,
        )

        cfg = TrainingConfig(
            dataset_id=dataset_pk,
            annotation_source_scope=annotation_source_scope,
            backbone_checkpoint=backbone_ckpt,
            backbone_name=backbone_name,
            num_epochs=num_epochs,
        )
        result = validate_model_training_result(train_model(cfg))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTraining finished. Model saved to: {result.model_path}"
            )
        )
