# endoreg_db/management/commands/model_input.py

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.data_loader_for_model_input import (
    build_dataset_for_training,
)
from endoreg_db.utils.ai.model_training.config import (
    TrainingConfig,
)
from endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel import (
    train_gastronet_multilabel,
)


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

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset-id",
            type=int,
            required=True,
            help="Primary key of the AIDataSet to use for training.",
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

    def handle(self, *args, **options):
        dataset_id = options["dataset_id"]
        backbone_ckpt = options["backbone_checkpoint"]
        backbone_name = options["backbone_name"]
        num_epochs = options["epochs"]

        try:
            dataset = AIDataSet.objects.get(id=dataset_id)
        except AIDataSet.DoesNotExist:
            raise CommandError(f"AIDataSet with id={dataset_id} does not exist.")

        # Basic info
        self.stdout.write(
            self.style.NOTICE(
                f"Using AIDataSet id={dataset.id}, "
                f"name={dataset.name!r}, "
                f"dataset_type={dataset.dataset_type!r}, "
                f"ai_model_type={dataset.ai_model_type!r}"
            )
        )

        data = build_dataset_for_training(dataset)

        image_paths = data["image_paths"]
        label_vectors = data["label_vectors"]
        label_masks = data["label_masks"]
        labels = data["labels"]
        labelset = data["labelset"]

        self.stdout.write(self.style.NOTICE("Inferred LabelSet for this AIDataSet:"))
        self.stdout.write(
            f"  LabelSet id={labelset.id}, "
            f"name={labelset.name!r}, "
            f"version={labelset.version}"
        )
        self.stdout.write("  Labels (index, id, name):")
        for idx, lbl in enumerate(labels):
            self.stdout.write(f"    [{idx}] id={lbl.id}, name={lbl.name!r}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nBuilt training dataset from AIDataSet id={dataset.id}:\n"
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
                f"\n Input for model training built successfully from AIDataSet id={dataset.id}."
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
        cfg = TrainingConfig(
            dataset_id=dataset.id,
            backbone_checkpoint=backbone_ckpt,
            backbone_name=backbone_name,
            num_epochs=num_epochs,
        )
        result = train_gastronet_multilabel(cfg)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTraining finished. Model saved to: {result['model_path']}"
            )
        )
