from endoreg_db.models.annotation.image_classification import ImageClassificationAnnotation
from endoreg_db.models.label.label import Label

from django.db import models


class AbstractFrame(models.Model):
    video = None # Placeholder for the video field, to be defined in derived classes
    frame_number = models.IntegerField()
    # Add any other fields you need to store frame-related information
    image = models.ImageField(upload_to="frames")  # Or some other field type, depending on how you're storing the frame
    suffix = models.CharField(max_length=255)
    # ImageClassificationAnnotation has a foreign key to this model (related name: image_classification_annotations)

    class Meta:
        # Ensure that for each video, the frame_number is unique
        abstract = True
        unique_together = ('video', 'frame_number')
        # Optimize for retrieval in frame_number order
        indexes = [models.Index(fields=['video', 'frame_number'])]


    def __str__(self):
        return self.video.file.path + " - " + str(self.frame_number)

    def get_frame_model(self):
        assert 1 == 2, "This method should be overridden in derived classes"

    def get_classification_annotations(self):
        """
        Get all image classification annotations for this frame.
        """
        return ImageClassificationAnnotation.objects.filter(frame=self)

    def get_classification_annotations_by_label(self, label:Label):
        """
        Get all image classification annotations for this frame with the given label.
        """
        return ImageClassificationAnnotation.objects.filter(frame=self, label=label)

    def get_classification_annotations_by_value(self, value:bool):
        """
        Get all image classification annotations for this frame with the given value.
        """
        return ImageClassificationAnnotation.objects.filter(frame=self, value=value)

    def get_classification_annotations_by_label_and_value(self, label:Label, value:bool):
        """
        Get all image classification annotations for this frame with the given label and value.
        """
        return ImageClassificationAnnotation.objects.filter(frame=self, label=label, value=value)