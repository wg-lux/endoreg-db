from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import ImageClassificationAnnotation
    # later:
    # from endoreg_db.models import VideoSegmentationAnnotation
    # from endoreg_db.models import TextAnnotation  # example name


class AIDataSet(models.Model):
    """
    AIDataSet stores the definition of a training dataset for an AI model.

    It does NOT store annotation vectors directly.

    Instead, it stores:
    - which AI model family it is for         (ai_model_type)
    - which type of data it is based on       (dataset_type)
    - which annotations belong to it          (image/video/text *_annotations)

    For now:
        dataset_type == "image"
            -> image_annotations contains ImageClassificationAnnotation rows
               (each with frame_id and label_id)

    Later:
        dataset_type == "video"
            -> video_annotations will contain video-level annotation rows

        dataset_type == "text"
            -> text_annotations will contain text-level annotation rows
    """

    # ------------------------------------------------------------------
    # CHOICES
    # ------------------------------------------------------------------
    DATASET_TYPE_IMAGE = "image"
    DATASET_TYPE_VIDEO = "video"
    DATASET_TYPE_TEXT = "text"

    DATASET_TYPE_CHOICES = [
        (DATASET_TYPE_IMAGE, "Image"),
        # later, when implemented:
        # (DATASET_TYPE_VIDEO, "Video"),
        # (DATASET_TYPE_TEXT, "Text"),
    ]

    AI_MODEL_TYPE_IMAGE_MULTILABEL = "image_multilabel_classification"
    # later: add more ai_model_type values as needed, e.g.
    # AI_MODEL_TYPE_VIDEO_SEGMENTATION = "video_segmentation"
    # AI_MODEL_TYPE_TEXT_CLASSIFICATION = "text_classification"

    # ------------------------------------------------------------------
    # BASIC FIELDS
    # ------------------------------------------------------------------

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )

    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional notes / explanation about this dataset.",
    )

    ai_model_type = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification". '
            "Used to pick the correct architecture and output dimension logic."
        ),
    )

    dataset_type = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Controls which annotation table will be used. "
            'Currently only "image" is implemented; later "video", "text", etc.'
        ),
    )

    # ------------------------------------------------------------------
    # TYPE-SPECIFIC ANNOTATION RELATIONS (Option A)
    # ------------------------------------------------------------------
    # For each dataset_type, we provide a separate M2M field.
    # Only one of them is actually *used* per dataset instance.
    # The others simply remain empty.

    # IMAGE DATASETS:
    # For dataset_type == "image":
    #   - image_annotations defines which ImageClassificationAnnotation rows
    #     belong to this dataset.
    image_annotations = models.ManyToManyField(
        "ImageClassificationAnnotation",
        related_name="image_ai_datasets",
        blank=True,
        help_text=(
            "For dataset_type='image', this is the set of ImageClassificationAnnotation "
            "rows that define this AIDataSet. Each annotation has frame_id and label_id."
        ),
    )

    # VIDEO DATASETS (FUTURE):
    # For dataset_type == "video", you will later add something like:
    #
    # video_annotations = models.ManyToManyField(
    #     "VideoSegmentationAnnotation",    # or whatever your video annotation model is
    #     related_name="video_ai_datasets",
    #     blank=True,
    #     help_text=(
    #         "For dataset_type='video', this will be the set of video-level "
    #         "annotation rows that define this AIDataSet."
    #     ),
    # )
    #
    # TEXT DATASETS (FUTURE):
    # For dataset_type == "text", you could add:
    #
    # text_annotations = models.ManyToManyField(
    #     "TextAnnotation",                 # placeholder name
    #     related_name="text_ai_datasets",
    #     blank=True,
    #     help_text=(
    #         "For dataset_type='text', this will be the set of text-level "
    #         "annotation rows that define this AIDataSet."
    #     ),
    # )

    # ------------------------------------------------------------------
    # META FIELDS
    # ------------------------------------------------------------------
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this AIDataSet was created.",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this AIDataSet was last modified.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    if TYPE_CHECKING:
        # for static type checkers only
        image_annotations: models.ManyToManyField["ImageClassificationAnnotation"]
        # video_annotations: models.ManyToManyField["VideoSegmentationAnnotation"]
        # text_annotations: models.ManyToManyField["TextAnnotation"]

    # ------------------------------------------------------------------
    # UNIFIED ACCESS HELPERS (USE THESE IN YOUR TRAINING CODE)
    # ------------------------------------------------------------------

    def get_annotations_queryset(self):
        """
        Return the *active* annotation relation for this dataset, based on dataset_type.

        - For dataset_type='image' -> returns self.image_annotations
        - For dataset_type='video' -> later: return self.video_annotations
        - For dataset_type='text'  -> later: return self.text_annotations

        This is what your data loader / training code should call.
        """
        if self.dataset_type == self.DATASET_TYPE_IMAGE:
            return self.image_annotations

        # TODO (future): implement once video/text annotation models exist
        # if self.dataset_type == self.DATASET_TYPE_VIDEO:
        #     return self.video_annotations
        #
        # if self.dataset_type == self.DATASET_TYPE_TEXT:
        #     return self.text_annotations

        # Fallback: empty queryset (nothing to train on)
        return self.image_annotations.none()

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"
