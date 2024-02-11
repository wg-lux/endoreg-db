from django.db import models
from rest_framework import serializers
from ..label import Label
from ..data_file.video_segment import LegacyLabelVideoSegment
from .image_classification import ImageClassificationAnnotation

ANNOTATION_PER_S_THRESHOLD = 2

def clear_finished_legacy_tasks():
    # fetch all BinaryClassificationAnnotationTasks that are finished and delete them
    tasks = LegacyBinaryClassificationAnnotationTask.objects.filter(finished=True)

    # delete tasks with a bulk operation
    tasks.delete()

def get_legacy_binary_classification_annotation_tasks_by_label(label:Label, n:int=100, legacy=False):
    clear_finished_legacy_tasks()

    if legacy:
        # fetch all LegacyLabelVideoSegments with the given label
        _segments = LegacyLabelVideoSegment.objects.filter(label=label)
        frames_for_tasks = []

        for segment in _segments:
            # check if the segment has already been annotated
            annotations = list(ImageClassificationAnnotation.objects.filter(legacy_frame__in=segment.get_frames(), label=label))
            segment_len_in_s = segment.get_segment_len_in_s()

            target_annotation_number = segment_len_in_s * ANNOTATION_PER_S_THRESHOLD

            if len(annotations) < target_annotation_number:
                get_frame_number = int(target_annotation_number - len(annotations))
                frames = segment.get_frames_without_annotation(get_frame_number)
                frames_for_tasks.extend(frames)

            if len(frames_for_tasks) >= n:
                break

        # create tasks
        tasks = []
        for frame in frames_for_tasks:

            # get_or_create task
            task, created = LegacyBinaryClassificationAnnotationTask.objects.get_or_create(
                label=label,
                image_path=frame.image.path,
                frame_id=frame.pk,
            )
        

class AbstractBinaryClassificationAnnotationTask(models.Model):
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
    frame = models.ForeignKey("Frame", on_delete=models.CASCADE, related_name="binary_classification_annotation_tasks")
    image_type = models.CharField(max_length=255, default="frame")

    def get_frame(self):
        return self.video_segment.get_frame_by_id(self.frame_id)

class LegacyBinaryClassificationAnnotationTask(AbstractBinaryClassificationAnnotationTask):
    frame = models.ForeignKey("LegacyFrame", on_delete=models.CASCADE, related_name="binary_classification_annotation_tasks")
    image_type = models.CharField(max_length=255, default="legacy")

    def get_frame(self):
        return self.frame



