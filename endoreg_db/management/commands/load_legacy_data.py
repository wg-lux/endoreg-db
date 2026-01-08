# endoreg_db/management/commands/load_legacy_data.py

import json
from pathlib import Path
from typing import Dict, List

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

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

DEFAULT_LABELSET_NAME = "multilabel_classification_colonoscopy_default"
DEFAULT_LABELSET_VERSION = 1

# Batch size for PostgreSQL bulk inserts
BATCH_SIZE = 500


class Command(BaseCommand):
    """
    Import legacy multilabel image data from JSONL + images into the database.

    LOGIC (unchanged):
    - Read JSONL line-by-line
    - Create Frames linked to a VideoFile
    - Create ImageClassificationAnnotations for labels
    - Reuse / extend existing LabelSet
    - Create or reuse AIDataSet
    - Attach annotations to AIDataSet

    OPTIMIZATIONS (PostgreSQL):
    - Label caching (no per-row get_or_create)
    - Frame bulk_create first, then annotations
    - Bulk M2M insert for AIDataSet relations
    - Minimal ORM round-trips
    """

    # ------------------------------------------------------------------
    # CLI arguments
    # ------------------------------------------------------------------

    def add_arguments(self, parser):
        parser.add_argument(
            "--jsonl-path",
            type=str,
            default=str(
                Path(settings.BASE_DIR).parent
                / "data"
                / "legacy_data"
                / "legacy_img_dicts.jsonl"
            ),
            help="Path to legacy_img_dicts.jsonl",
        )
        parser.add_argument(
            "--images-root",
            type=str,
            default=str(
                Path(settings.BASE_DIR).parent / "data" / "legacy_data" / "images"
            ),
            help="Root directory containing legacy images.",
        )
        parser.add_argument(
            "--video-id",
            type=int,
            required=True,
            help="ID of an existing VideoFile to attach all legacy Frames to.",
        )
        parser.add_argument(
            "--dataset-name",
            type=str,
            default="legacy_multilabel_dataset_v1",
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

    # ------------------------------------------------------------------
    # Main logic
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        jsonl_path = Path(options["jsonl_path"])
        images_root = Path(options["images_root"])
        video_id = options["video_id"]
        dataset_name = options["dataset_name"]
        dataset_description = options["dataset_description"]
        labelset_name = options["labelset_name"]
        labelset_version = options["labelset_version"]
        dry_run = options["dry_run"]

        # --------------------------------------------------------------
        # Validations
        # --------------------------------------------------------------

        if not jsonl_path.exists():
            raise CommandError(f"JSONL file not found: {jsonl_path}")

        if not images_root.exists():
            raise CommandError(f"Images root directory not found: {images_root}")

        try:
            video = VideoFile.objects.get(id=video_id)
        except VideoFile.DoesNotExist:
            raise CommandError(f"VideoFile with id={video_id} does not exist.")

        self.stdout.write(self.style.NOTICE(f"Using VideoFile id={video.id}"))

        if not video.frame_dir:
            video.frame_dir = str(images_root)
            video.save(update_fields=["frame_dir"])

        # --------------------------------------------------------------
        # LabelSet (must exist)
        # --------------------------------------------------------------

        labelset = self._get_existing_labelset(labelset_name, labelset_version)

        # --------------------------------------------------------------
        # Label cache (CRITICAL OPTIMIZATION)
        # --------------------------------------------------------------

        label_cache: Dict[str, Label] = {
            label.name: label for label in labelset.labels.all()
        }

        # --------------------------------------------------------------
        # AIDataSet
        # --------------------------------------------------------------

        if dry_run:
            ai_dataset = None
        else:
            ai_dataset, _ = AIDataSet.objects.get_or_create(
                name=dataset_name,
                defaults={
                    "description": dataset_description,
                    "dataset_type": AIDataSet.DATASET_TYPE_IMAGE,
                    "ai_model_type": AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
                    "is_active": True,
                },
            )

        # --------------------------------------------------------------
        # Buffers
        # --------------------------------------------------------------

        frames_buffer: List[Frame] = []
        annotations_buffer: List[ImageClassificationAnnotation] = []

        frame_counter = 0
        annotation_counter = 0

        ctx = transaction.atomic if not dry_run else self._noop_context

        # --------------------------------------------------------------
        # Import loop
        # --------------------------------------------------------------

        with ctx():
            with jsonl_path.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    item = json.loads(line)

                    filename = item.get("filename")
                    labels = item.get("labels", [])
                    old_exam_id = item.get("old_examination_id")

                    if not filename:
                        continue

                    frame_counter += 1

                    frame = Frame(
                        video=video,
                        frame_number=frame_counter,
                        relative_path=filename,
                        timestamp=None,
                        old_examination_id=old_exam_id,
                        is_extracted=True,
                    )

                    if not dry_run:
                        frames_buffer.append(frame)

                    for label_name in labels:
                        label = label_cache.get(label_name)
                        if label is None:
                            label = Label.objects.create(name=label_name)
                            labelset.labels.add(label)
                            label_cache[label_name] = label

                        annotation_counter += 1
                        annotations_buffer.append(
                            ImageClassificationAnnotation(
                                frame=frame,
                                label=label,
                                value=True,
                                annotator="legacy_import",
                            )
                        )

                    # --------------------------------------------------
                    # Batch flush
                    # --------------------------------------------------

                    if not dry_run and len(frames_buffer) >= BATCH_SIZE:
                        self._flush_batches(
                            video=video,
                            frames=frames_buffer,
                            annotations=annotations_buffer,
                            ai_dataset=ai_dataset,
                        )
                        frames_buffer.clear()
                        annotations_buffer.clear()

            # Final flush
            if not dry_run and frames_buffer:
                self._flush_batches(
                    video=video,
                    frames=frames_buffer,
                    annotations=annotations_buffer,
                    ai_dataset=ai_dataset,
                )

        # --------------------------------------------------------------
        # Summary
        # --------------------------------------------------------------

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Parsed {frame_counter} Frames, "
                    f"{annotation_counter} Annotations"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imported {frame_counter} Frames, "
                    f"{annotation_counter} Annotations"
                )
            )

    # ------------------------------------------------------------------
    # Batch flush helper
    # ------------------------------------------------------------------

    def _flush_batches(
        self,
        *,
        video: VideoFile,
        frames: List[Frame],
        annotations: List[ImageClassificationAnnotation],
        ai_dataset: AIDataSet | None,
    ):
        # Insert frames
        Frame.objects.bulk_create(frames, returning=True)
        frame_map = {f.frame_number: f for f in frames}

        # Reload frames to obtain PKs
        """saved_frames = Frame.objects.filter(
            video=video,
            frame_number__in=[f.frame_number for f in frames],
        )"""

        #frame_map = {f.frame_number: f for f in saved_frames}

        # Fix annotation FK references
        for ann in annotations:
            ann.frame = frame_map[ann.frame.frame_number]

        # Insert annotations
        # ImageClassificationAnnotation.objects.bulk_create(annotations)
        ImageClassificationAnnotation.objects.bulk_create(
           annotations,
           returning=True,
)


        # Bulk M2M insert
        if ai_dataset is not None:
            through = ai_dataset.image_annotations.through
            through.objects.bulk_create(
                [
                    through(
                        aidataset_id=ai_dataset.id,
                        imageclassificationannotation_id=ann.id,
                    )
                    for ann in annotations
                ],
                ignore_conflicts=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_existing_labelset(
        self, name: str, version: int
    ) -> LabelSet:
        try:
            return LabelSet.objects.get(name=name, version=version)
        except LabelSet.DoesNotExist as exc:
            raise CommandError(
                f"LabelSet '{name}' (version={version}) does not exist."
            ) from exc

    class _noop_context:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False
