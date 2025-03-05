from django.db import models
# from rest_framework import serializers
# from ..label import Label
# from .image_classification import ImageClassificationAnnotation

ANNOTATION_PER_S_THRESHOLD = 2

# TODO Migrate
# def clear_finished_legacy_tasks():
#     """
#     Deletes all finished LegacyBinaryClassificationAnnotationTask entries.
#     """
#     tasks = LegacyBinaryClassificationAnnotationTask.objects.filter(is_finished=True)
#     tasks.delete()

# def get_legacy_binary_classification_annotation_tasks_by_label(label:Label, n:int=100, legacy=False):
#     clear_finished_legacy_tasks()
#     """
#     Retrieves legacy binary classification annotation tasks for a specific label.

#     Args:
#         label (Label): The label to filter tasks by.
#         n (int): Maximum number of tasks to retrieve. Defaults to 100.
#         legacy (bool): If True, includes legacy tasks. Defaults to False.
#     """
#     if legacy:
#         # fetch all LegacyLabelVideoSegments with the given label
#         _segments = LegacyLabelVideoSegment.objects.filter(label=label)
#         frames_for_tasks = []

#         for segment in _segments:
#             # check if the segment has already been annotated
#             annotations = list(ImageClassificationAnnotation.objects.filter(legacy_frame__in=segment.get_frames(), label=label))
#             segment_len_in_s = segment.get_segment_len_in_s()

#             target_annotation_number = segment_len_in_s * ANNOTATION_PER_S_THRESHOLD

#             if len(annotations) < target_annotation_number:
#                 get_frame_number = int(target_annotation_number - len(annotations))
#                 frames = segment.get_frames_without_annotation(get_frame_number)
#                 frames_for_tasks.extend(frames)

#             if len(frames_for_tasks) >= n:
#                 break

#         # create tasks
#         tasks = []
#         for frame in frames_for_tasks:

#             # get_or_create task
#             task, created = LegacyBinaryClassificationAnnotationTask.objects.get_or_create(
#                 label=label,
#                 image_path=frame.image.path,
#                 frame_id=frame.pk,
#             )


class AbstractBinaryClassificationAnnotationTask(models.Model):
    """
    Abstract base class for binary classification annotation tasks.

    Attributes:
        label (ForeignKey): The associated label.
        is_finished (bool): Indicates if the task is completed.
        date_created (datetime): The creation date of the task.
        date_finished (datetime): The completion date of the task.
        image_path (str): Path to the associated image.
        image_type (str): The type of the image (e.g., "frame" or "legacy").
        frame_id (int): Identifier of the associated frame.
        labelstudio_project_id (int): The Label Studio project ID.
        labelstudio_task_id (int): The Label Studio task ID.
    """

    label = models.ForeignKey("Label", on_delete=models.CASCADE)
    is_finished = models.BooleanField(default=False)
    date_created = models.DateTimeField(auto_now_add=True)
    date_finished = models.DateTimeField(blank=True, null=True)
    image_path = models.CharField(max_length=255, blank=True, null=True)
    image_type = models.CharField(max_length=255, blank=True, null=True)
    frame_id = models.IntegerField(blank=True, null=True)
    labelstudio_project_id = models.IntegerField(blank=True, null=True)
    labelstudio_task_id = models.IntegerField(blank=True, null=True)

    class Meta:
        abstract = True


class BinaryClassificationAnnotationTask(AbstractBinaryClassificationAnnotationTask):
    """
    Represents a binary classification task for a frame.

    Attributes:
       frame (ForeignKey): The associated frame for this task.
    """

    frame = models.ForeignKey(
        "Frame",
        on_delete=models.CASCADE,
        related_name="binary_classification_annotation_tasks",
    )
    image_type = models.CharField(
        max_length=255, default="frame"
    )  # Default image type for non-legacy tasks

    def get_frame(self):
        """
        Retrieves the frame associated with this task.
        """
        from endoreg_db.models.data_file.frame import Frame

        frame = self.frame
        if not frame:
            return None
        else:
            assert isinstance(frame, Frame)

        return frame
