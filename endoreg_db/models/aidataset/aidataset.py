# endoreg_db/models/aidataset/aidataset.py

from django.db import models

from endoreg_db.models import ImageClassificationAnnotation


class AIDataSet(models.Model):
    """
    AIDataSet stores the definition of a training dataset for an AI model.

    It does NOT store annotation vectors directly. Instead, it stores:
    - which kind of data it is based on (dataset_type)
    - which model family it belongs to (ai_model_type)
    - which annotations belong to this dataset (via reqset)

    For now:
        dataset_type == "image"
            -> reqset points to ImageClassificationAnnotation rows
               (each with frame_id and label_id)

    Later:
        dataset_type == "video"
            -> extend this model to also connect to a video-annotation table

        dataset_type == "text"
            -> extend this model to also connect to a text-annotation table
    """

    # -------------------------------------------------------------------------
    # CHOICES
    # -------------------------------------------------------------------------
    DATASET_TYPE_IMAGE = "image"
    DATASET_TYPE_VIDEO = "video"
    DATASET_TYPE_TEXT = "text"

    DATASET_TYPE_CHOICES = [
        (DATASET_TYPE_IMAGE, "Image"),
        # Add more here when you implement them:
        # (DATASET_TYPE_VIDEO, "Video"),
        # (DATASET_TYPE_TEXT, "Text"),
    ]

    # You can later add choices here if you want to restrict ai_model_type to
    # known values; for now it’s a free string.
    AI_MODEL_TYPE_IMAGE_MULTILABEL = "image_multilabel_classification"

    # -------------------------------------------------------------------------
    # FIELDS (as you specified)
    # -------------------------------------------------------------------------

    # id: implicit primary key

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Human-readable identifier, e.g. "Legacy multilabel dataset v1".',
    )

    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional description / notes about the dataset.",
    )

    ai_model_type = models.CharField(
        max_length=255,
        default=AI_MODEL_TYPE_IMAGE_MULTILABEL,
        help_text=(
            "AI model family this dataset is for, e.g. "
            '"image_multilabel_classification". '
            "Used to pick the correct architecture/output logic."
        ),
    )

    dataset_type = models.CharField(
        max_length=32,
        choices=DATASET_TYPE_CHOICES,
        default=DATASET_TYPE_IMAGE,
        help_text=(
            "Controls which annotation table is used. "
            'Currently only "image" is implemented; later "video", "text", etc.'
        ),
    )

    # -------------------------------------------------------------------------
    # Requirementset (reqset)
    #
    # According to your specification:
    # - For dataset_type == "image":
    #       reqset is the connection to ImageClassificationAnnotation
    #       and defines which annotations are part of this dataset.
    #
    # We use a ManyToManyField to represent
    #   “this dataset is defined by these annotations”.
    #
    # Later, when you support video/text:
    # - You can either:
    #     * add separate M2M fields (e.g. video_reqset, text_reqset), or
    #     * introduce another abstraction that routes to the right tables.
    # -------------------------------------------------------------------------
    reqset = models.ManyToManyField(
        ImageClassificationAnnotation,
        related_name="ai_datasets",
        blank=True,
        help_text=(
            "For dataset_type='image', this set of ImageClassificationAnnotation rows "
            "defines which frames and labels belong to this AIDataSet. "
            "Each annotation has frame_id and label_id."
        ),
    )

    # -------------------------------------------------------------------------
    # META / AUDIT
    # -------------------------------------------------------------------------
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this AIDataSet was created.",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when this AIDataSet was last modified.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Soft toggle to enable/disable this dataset for training.",
    )

    # -------------------------------------------------------------------------
    # HELPER METHODS (no heavy logic, but useful for later)
    # -------------------------------------------------------------------------

    def __str__(self) -> str:
        if self.name:
            return f"AIDataSet(id={self.id}, name={self.name})"
        return f"AIDataSet(id={self.id})"

    def get_image_annotations(self):
        """
        Return the queryset of ImageClassificationAnnotation objects that
        define this dataset, but ONLY if dataset_type == 'image'.

        This is the entry point for building:
        - the list of frames (and thus image paths)
        - the list of labels (and thus the label vectors)

        Later:
            When you add support for 'video' or 'text', you can:
            - either extend this method to branch by dataset_type, or
            - add new methods get_video_annotations(), get_text_annotations().
        """
        if self.dataset_type != self.DATASET_TYPE_IMAGE:
            # For now, only image datasets are supported.
            # You can change this behavior once you implement other types.
            return ImageClassificationAnnotation.objects.none()

        return self.reqset.select_related("frame", "label")

    # NOTE:
    # The logic to build the actual data loader
    # (paths list + annotation vectors [1, 0, None])
    # should live in a separate service/function that uses:
    #
    #   annotations = dataset.get_image_annotations()
    #
    # and from there:
    #   - derive frames via annotation.frame
    #   - derive labels via annotation.label
    #   - derive LabelSet and label order based on your label configuration.
    #
    # This keeps the model lean and leaves training-specific logic outside.
