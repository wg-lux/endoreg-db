# endoreg_db/management/commands/train_image_multilabel_model.py

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import AIDataSet
from endoreg_db.utils.ai.data_loader_for_model_training import (
    build_dataset_for_training,
)


class Command(BaseCommand):
    help = (
        "Train / fine-tune the image multi-label model on a given AIDataSet.\n"
        "\n"
        "This command is fully dynamic:\n"
        "- It uses the AIDataSet row (id) to decide which annotations to use.\n"
        "- It *infers* the LabelSet from the labels in those annotations.\n"
        "- It builds image_paths, label_vectors, and label_masks for training.\n"
        "- It prints detailed debug information about the chosen LabelSet and samples.\n"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset-id",
            type=int,
            required=True,
            help="Primary key of the AIDataSet to use for training.",
        )

    def handle(self, *args, **options):
        dataset_id = options["dataset_id"]

        try:
            dataset = AIDataSet.objects.get(id=dataset_id)
        except AIDataSet.DoesNotExist:
            raise CommandError(f"AIDataSet with id={dataset_id} does not exist.")

        # ------------------------------------------------------------------
        # Basic dataset info (AIDataSet row)
        # ------------------------------------------------------------------
        self.stdout.write(
            self.style.NOTICE(
                f"Using AIDataSet id={dataset.id}, "
                f"name={dataset.name!r}, "
                f"dataset_type={dataset.dataset_type!r}, "
                f"ai_model_type={dataset.ai_model_type!r}"
            )
        )

        # Build in-memory dataset (completely dynamic, labelset inferred automatically)
        data = build_dataset_for_training(dataset)

        image_paths = data["image_paths"]
        label_vectors = data["label_vectors"]
        label_masks = data["label_masks"]
        labels = data["labels"]
        labelset = data["labelset"]

        # Optional: additional meta info, if present
        frame_ids = data.get("frame_ids", [])
        old_examination_ids = data.get("old_examination_ids", [])

        # ------------------------------------------------------------------
        # Debug: show which LabelSet was picked and its labels
        # ------------------------------------------------------------------
        self.stdout.write(self.style.NOTICE("Inferred LabelSet for this AIDataSet:"))
        self.stdout.write(
            f"  LabelSet id={labelset.id}, "
            f"name={labelset.name!r}, "
            f"version={labelset.version}"
        )
        self.stdout.write("  Labels (index, id, name):")
        for idx, lbl in enumerate(labels):
            self.stdout.write(f"    [{idx}] id={lbl.id}, name={lbl.name!r}")

        # ------------------------------------------------------------------
        # Summary of constructed dataset
        # ------------------------------------------------------------------
        self.stdout.write(
            self.style.SUCCESS(
                f"\nBuilt training dataset from AIDataSet id={dataset.id}:\n"
                f"- #samples: {len(image_paths)}\n"
                f"- #labels:  {len(labels)}"
            )
        )

        # ------------------------------------------------------------------
        # Debug: print each frame's path, label vector and mask
        # NOTE: If your dataset is very large, this will spam the console.
        #       For now you want full transparency, so we print all.
        # ------------------------------------------------------------------
        MAX_PRINT = 50
        self.stdout.write(self.style.NOTICE("\nPer-sample debug output:"))
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

            frame_id = frame_ids[i] if i < len(frame_ids) else None
            old_exam = old_examination_ids[i] if i < len(old_examination_ids) else None

            self.stdout.write(
                f"  Sample {i}:"
                f"\n    path = {path!r}"
                f"\n    frame_id = {frame_id}"
                f"\n    old_examination_id = {old_exam}"
                f"\n    vector (1/0/None) = {vec}"
                f"\n    mask (1=use, 0=ignore) = {mask}"
            )

        # ------------------------------------------------------------------
        # TODO: Insert your actual training loop here
        # ------------------------------------------------------------------
        # Example (pseudo-code):
        #
        #   import torch
        #   from torch.utils.data import Dataset, DataLoader
        #
        #   class EndoDataset(Dataset):
        #       def __init__(self, image_paths, label_vectors, label_masks):
        #           ...
        #
        #   ds = EndoDataset(image_paths, label_vectors, label_masks)
        #   loader = DataLoader(ds, batch_size=32, shuffle=True)
        #
        #   for batch in loader:
        #       # forward, compute loss using mask, backward, step, ...
        #
        # ------------------------------------------------------------------

        self.stdout.write(
            self.style.SUCCESS(
                "\nDataset construction finished. "
                "You can now plug this into your model training code."
            )
        )
