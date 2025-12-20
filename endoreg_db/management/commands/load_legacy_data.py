# endoreg_db/management/commands/load_legacy_data.py

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from endoreg_db.models import (
    AIDataSet,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    VideoFile,
)

DEFAULT_LABELSET_NAME = (
    "multilabel_classification_colonoscopy_default"  # must be present in the DB
)
DEFAULT_LABELSET_VERSION = 1


class Command(BaseCommand):
    help = (
        "Import legacy multilabel image data from JSONL + images into the database.\n"
        "- Creates Frames linked to a given VideoFile\n"
        "- Creates ImageClassificationAnnotations (value=True) for each listed label\n"
        "- Reuses/extends an existing LabelSet\n"
        "- Fills an AIDataSet (image dataset) with all annotations via image_annotations"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--jsonl-path",
            type=str,
            default=str(
                Path(settings.BASE_DIR)
                / "data"
                / "legacy_data"
                / "legacy_img_dicts.jsonl"
            ),
            help="Path to legacy_img_dicts.jsonl",
        )
        parser.add_argument(
            "--images-root",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "legacy_data" / "images"),
            help="Root directory containing legacy images.",
        )
        # All imported frames need to belong to some VideoFile.
        parser.add_argument(
            "--video-id",
            type=int,
            required=True,
            help="ID of an existing VideoFile to attach all legacy Frames to.",
        )
        parser.add_argument(
            "--dataset-name",
            type=str,
            default="legacy_multilabel_dataset_v1",  # later change this if needed
            help="Name for the created/reused AIDataSet.",
        )
        parser.add_argument(
            "--dataset-description",
            type=str,
            default="Legacy multilabel colonoscopy dataset imported from JSONL.",
            help="Description for the created AIDataSet.",
        )
        parser.add_argument(
            "--labelset-name",
            type=str,
            default=DEFAULT_LABELSET_NAME,
            help="LabelSet name to use (must exist).",
        )
        parser.add_argument(
            "--labelset-version",
            type=int,
            default=DEFAULT_LABELSET_VERSION,
            help="LabelSet version to use (must exist).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate, but do not write anything to the database.",
        )

    def handle(self, *args, **options):
        jsonl_path = Path(options["jsonl_path"])
        images_root = Path(options["images_root"])
        video_id = options["video_id"]
        dataset_name = options["dataset_name"]
        dataset_description = options["dataset_description"]
        labelset_name = options["labelset_name"]
        labelset_version = options["labelset_version"]
        dry_run = options["dry_run"]

        # --- Basic checks ---
        if not jsonl_path.exists():
            raise CommandError(f"JSONL file not found: {jsonl_path}")

        if not images_root.exists():
            raise CommandError(f"Images root directory not found: {images_root}")

        try:
            video = VideoFile.objects.get(id=video_id)
        except VideoFile.DoesNotExist:
            raise CommandError(f"VideoFile with id={video_id} does not exist.")

        self.stdout.write(
            self.style.NOTICE(f"Using VideoFile id={video.id} for all Frames.")
        )

        # Ensure this VideoFile uses the legacy images folder as its frame_dir
        # IMPORTANT: we only set this if frame_dir is empty, so we don't break other videos.
        if not video.frame_dir:
            video.frame_dir = str(images_root)  # images_root is Path(...)
            video.save(update_fields=["frame_dir"])
            self.stdout.write(
                self.style.NOTICE(
                    f"Set frame_dir for VideoFile id={video.id} to '{video.frame_dir}' "
                    "for legacy image frames."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"VideoFile id={video.id} already has frame_dir='{video.frame_dir}'. "
                    "Legacy Frames will be resolved relative to this directory."
                )
            )

        # --- Use existing LabelSet (v1) ---
        labelset = self._get_existing_labelset(
            labelset_name=labelset_name,
            labelset_version=labelset_version,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Using LabelSet '{labelset.name}' (version={labelset.version}, id={labelset.id})."
            )
        )

        # --- Create or reuse AIDataSet (image dataset) ---
        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run: AIDataSet will NOT be created.")
            )
            ai_dataset = None
        else:
            ai_dataset, created = AIDataSet.objects.get_or_create(
                name=dataset_name,
                defaults={
                    "description": dataset_description,
                    "dataset_type": AIDataSet.DATASET_TYPE_IMAGE,
                    "ai_model_type": AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
                    "is_active": True,
                },
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created AIDataSet id={ai_dataset.id}, name='{ai_dataset.name}'."
                    )
                )
            else:
                # Use the helper method so this works even if we add video/text later
                current_count = ai_dataset.get_annotations_queryset().count()
                self.stdout.write(
                    self.style.WARNING(
                        f"Re-using existing AIDataSet id={ai_dataset.id}, name='{ai_dataset.name}'. "
                        f"(Current annotation_count={current_count})"
                    )
                )

        frame_counter = 0
        annotation_counter = 0

        # Use transaction unless dry-run
        ctx = transaction.atomic if not dry_run else self._noop_context

        with ctx():
            with jsonl_path.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise CommandError(
                            f"Invalid JSON on line {line_num} of {jsonl_path}: {exc}"
                        )

                    labels_list = item.get("labels", [])
                    filename = item.get("filename")
                    # old_examination_id and old_id are available if you want them later:
                    old_id = item.get("old_id")
                    old_exam_id = item.get("old_examination_id")

                    if not filename:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Skipping line {line_num}: no 'filename' key."
                            )
                        )
                        continue

                    image_path = images_root / filename
                    if not image_path.exists():
                        self.stdout.write(
                            self.style.WARNING(
                                f"Image file does not exist for line {line_num}: {image_path}"
                            )
                        )
                        # Still create Frame so DB + paths are consistent.

                    # --- Create Frame ---
                    frame_counter += 1
                    frame = Frame(
                        video=video,
                        frame_number=frame_counter,
                        relative_path=filename,  # filename is relative under images_root
                        timestamp=None,
                        old_examination_id=old_exam_id,  # keeping old examination id legacy exam id for grouping
                        is_extracted=True,
                    )
                    if not dry_run:
                        frame.save()

                    # --- Create annotations for positive labels ---
                    for label_name in labels_list:
                        label = self._get_or_create_label_and_attach_to_labelset(
                            label_name=label_name,
                            labelset=labelset,
                        )

                        annotation_counter += 1
                        annotation = ImageClassificationAnnotation(
                            frame=frame,
                            label=label,
                            value=True,
                            annotator="legacy_import",
                        )
                        if not dry_run:
                            annotation.save()
                            if ai_dataset is not None:
                                # IMPORTANT CHANGE:
                                # Use the AIDataSet helper, which for dataset_type='image'
                                # returns the image_annotations manager.
                                ai_dataset.get_annotations_queryset().add(annotation)

            # --- Summary ---
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"[DRY RUN] Processed {frame_counter} Frames, {annotation_counter} Annotations. "
                        "No database changes were committed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Imported {frame_counter} Frames, {annotation_counter} "
                        f"ImageClassificationAnnotations into AIDataSet id={ai_dataset.id}."
                    )
                )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _get_existing_labelset(
        self, labelset_name: str, labelset_version: int
    ) -> LabelSet:
        try:
            return LabelSet.objects.get(name=labelset_name, version=labelset_version)
        except LabelSet.DoesNotExist as exc:
            raise CommandError(
                f"LabelSet name='{labelset_name}', version={labelset_version} does not exist. "
                "Create it first (e.g. via fixtures or admin)."
            ) from exc

    def _get_or_create_label_and_attach_to_labelset(
        self, label_name: str, labelset: LabelSet
    ) -> Label:
        label, _ = Label.objects.get_or_create(name=label_name)
        # Attach to this labelset if missing
        if label not in labelset.labels.all():
            labelset.labels.add(label)
        return label

    class _noop_context:
        """Simple no-op context manager used for dry-run."""

        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False
