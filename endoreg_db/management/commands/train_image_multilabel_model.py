# endoreg_db/management/commands/train_image_multilabel_model.py

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.model_training.config import (
    DEFAULT_LABELSET_VERSION_TO_TRAIN,
    TrainingConfig,
)
from endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel import (
    train_gastronet_multilabel,
)


class Command(BaseCommand):
    help = "Train / fine-tune the image multi-label model on a given AIDataSet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset-id",
            type=int,
            required=True,
            help="Primary key of the AIDataSet to use for training.",
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

    def handle(self, *args, **options):
        dataset_id = options["dataset_id"]
        backbone_name = str(options["backbone_name"]).strip()
        backbone_checkpoint = options["backbone_checkpoint"]
        epochs = int(options["epochs"])
        batch_size = int(options["batch_size"])
        labelset_version = int(options["labelset_version"])
        freeze_backbone = bool(options["freeze_backbone"])
        treat_unlabeled_as_negative = bool(options["treat_unlabeled_as_negative"])

        try:
            dataset = AIDataSet.objects.get(id=dataset_id)
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
                f"Using AIDataSet id={dataset.id}, "
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
                f"treat_unlabeled_as_negative={treat_unlabeled_as_negative}"
            )
        )

        result = train_gastronet_multilabel(
            TrainingConfig(
                dataset_id=dataset.id,
                labelset_version_to_train=labelset_version,
                backbone_checkpoint=backbone_checkpoint,
                num_epochs=epochs,
                batch_size=batch_size,
                backbone_name=backbone_name,
                freeze_backbone=freeze_backbone,
                treat_unlabeled_as_negative=treat_unlabeled_as_negative,
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Training completed successfully. "
                f"Model saved to: {result['model_path']}"
            )
        )
        return result
