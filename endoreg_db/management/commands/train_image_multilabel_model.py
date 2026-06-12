# endoreg_db/management/commands/train_image_multilabel_model.py

from __future__ import annotations

import json
from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.multilabel_dataset_builder import (
    ANNOTATION_SOURCE_SCOPE_ALL,
    VALID_ANNOTATION_SOURCE_SCOPES,
    normalize_annotation_source_scope,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.management_command import (
    TrainImageMultilabelModelCommandOptionsPayload,
    validate_model_training_result,
)


class _TrainImageDataSet(Protocol):
    name: str
    dataset_type: str
    ai_model_type: str


def train_gastronet_multilabel(config: object) -> JsonObject:
    try:
        from endoreg_db.utils.ai.model_training.config import TrainingConfig

        trainer_module = import_module(
            "endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel"
        )
        train_candidate: object = getattr(trainer_module, "train_gastronet_multilabel")
        if not callable(train_candidate):
            raise CommandError("train_gastronet_multilabel is not callable.")
        train_model = cast(
            Callable[[TrainingConfig], JsonObject],
            train_candidate,
        )
    except ImportError as exc:
        raise CommandError(
            "Training dependencies are not available. Install the AI training "
            "dependencies before running train_image_multilabel_model."
        ) from exc
    if not isinstance(config, TrainingConfig):
        raise CommandError("TrainingConfig is required for image multilabel training.")
    return train_model(config)


class Command(BaseCommand):
    help = "Train / fine-tune the image multi-label model on a given AIDataSet."

    def add_arguments(self, parser: CommandParser) -> None:
        from endoreg_db.utils.ai.model_training.config import (
            DEFAULT_LABELSET_VERSION_TO_TRAIN,
        )

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
            "--backbone-name",
            type=str,
            default="gastro_rn50",
            help=(
                "Backbone name. Supported values: "
                "gastro_rn50, resnet50_imagenet, resnet50_random, "
                "efficientnet_b0_imagenet."
            ),
        )
        parser.add_argument(
            "--backbone-checkpoint",
            type=str,
            default=None,
            help="Optional checkpoint path for the selected backbone.",
        )
        parser.add_argument(
            "--epochs",
            type=int,
            default=10,
            help="Number of training epochs.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=32,
            help="Training batch size.",
        )
        parser.add_argument(
            "--labelset-version",
            type=int,
            default=DEFAULT_LABELSET_VERSION_TO_TRAIN,
            help="Only train labels belonging to this LabelSet.version.",
        )
        parser.add_argument(
            "--device",
            type=str,
            default="auto",
            help="Training device: auto, cpu, cuda, or a torch device string.",
        )
        parser.add_argument(
            "--freeze-backbone",
            dest="freeze_backbone",
            action="store_true",
            help="Freeze the backbone and train the classifier head only.",
        )
        parser.add_argument(
            "--unfreeze-backbone",
            dest="freeze_backbone",
            action="store_false",
            help="Fine-tune the full model including the backbone.",
        )
        parser.set_defaults(freeze_backbone=True)
        parser.add_argument(
            "--treat-unlabeled-as-negative",
            dest="treat_unlabeled_as_negative",
            action="store_true",
            help="Interpret unlabeled entries as explicit negatives during training.",
        )
        parser.add_argument(
            "--keep-unlabeled-unknown",
            dest="treat_unlabeled_as_negative",
            action="store_false",
            help="Keep unlabeled entries masked out of the loss and metrics.",
        )
        parser.set_defaults(treat_unlabeled_as_negative=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            options_payload = (
                TrainImageMultilabelModelCommandOptionsPayload.model_validate(options)
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        dataset_id = options_payload.dataset_id
        try:
            annotation_source_scope = normalize_annotation_source_scope(
                options_payload.annotation_source_scope
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        backbone_name = options_payload.backbone_name
        backbone_checkpoint = options_payload.backbone_checkpoint
        epochs = options_payload.epochs
        batch_size = options_payload.batch_size
        labelset_version = options_payload.labelset_version
        device = options_payload.device
        freeze_backbone = options_payload.freeze_backbone
        treat_unlabeled_as_negative = options_payload.treat_unlabeled_as_negative

        try:
            dataset = cast(_TrainImageDataSet, AIDataSet.objects.get(id=dataset_id))
        except AIDataSet.DoesNotExist:
            raise CommandError(f"AIDataSet with id={dataset_id} does not exist.")

        if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
            raise CommandError(
                "train_image_multilabel_model only supports image AIDataSet rows."
            )

        if dataset.ai_model_type != AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL:
            raise CommandError(
                "train_image_multilabel_model only supports "
                "image_multilabel_classification datasets."
            )

        self.stdout.write(
            self.style.NOTICE(
                f"Using AIDataSet id={dataset_id}, "
                f"name={dataset.name!r}, "
                f"dataset_type={dataset.dataset_type!r}, "
                f"ai_model_type={dataset.ai_model_type!r}"
            )
        )
        self.stdout.write(
            self.style.NOTICE(
                "Training configuration: "
                f"backbone_name={backbone_name!r}, "
                f"freeze_backbone={freeze_backbone}, "
                f"epochs={epochs}, "
                f"batch_size={batch_size}, "
                f"labelset_version={labelset_version}, "
                f"device={device!r}, "
                f"annotation_source_scope={annotation_source_scope!r}, "
                f"treat_unlabeled_as_negative={treat_unlabeled_as_negative}"
            )
        )
        try:
            from endoreg_db.utils.ai.model_training.config import TrainingConfig
        except ImportError as exc:
            raise CommandError(
                "Training dependencies are not available. Install the AI training "
                "dependencies before running train_image_multilabel_model."
            ) from exc

        config = TrainingConfig(
            dataset_id=dataset_id,
            annotation_source_scope=annotation_source_scope,
            labelset_version_to_train=labelset_version,
            backbone_checkpoint=backbone_checkpoint,
            num_epochs=epochs,
            batch_size=batch_size,
            device=device,
            backbone_name=backbone_name,
            freeze_backbone=freeze_backbone,
            treat_unlabeled_as_negative=treat_unlabeled_as_negative,
        )
        result = validate_model_training_result(train_gastronet_multilabel(config))

        self.stdout.write(
            self.style.SUCCESS(
                f"Training completed successfully. Model saved to: {result.model_path}"
            )
        )

        self.stdout.write(json.dumps(result.model_dump(mode="json")))
