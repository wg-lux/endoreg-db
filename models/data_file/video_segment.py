from django.db import models
import numpy as np
from ..annotation import ImageClassificationAnnotation

def find_segments_in_prediction_array(prediction_array: np.array, min_frame_len: int):
    """
    Expects a prediction array of shape (num_frames) and a minimum frame length.
    Returns a list of tuples (start_frame_number, end_frame_number) that represent the segments.
    """
    # Add False to the beginning and end to detect changes at the array boundaries
    padded_prediction = np.pad(prediction_array, (1, 1), 'constant', constant_values=False)
    
    # Find the start points and end points of the segments
    diffs = np.diff(padded_prediction.astype(int))
    segment_starts = np.where(diffs == 1)[0]
    segment_ends = np.where(diffs == -1)[0]
    
    # Filter segments based on min_frame_len
    segments = [(start, end) for start, end in zip(segment_starts, segment_ends) if end - start >= min_frame_len]
    
    return segments

class AbstractLabelVideoSegment(models.Model):
    video = None # Placeholder for the video field, to be defined in derived classes
    prediction_meta = None # Placeholder for the prediction_meta field, to be defined in derived classes
    start_frame_number = models.IntegerField()
    end_frame_number = models.IntegerField()
    source = models.ForeignKey("InformationSource", on_delete=models.CASCADE)
    label = models.ForeignKey("Label", on_delete=models.CASCADE)

    class Meta:
        abstract = True

    def __str__(self):
        return self.video.file.path + " Label - " + self.label.name + " - " + str(self.start_frame_number) + " - " + str(self.end_frame_number)
    
    def get_frames(self):
        return self.video.get_frame_range(self.start_frame_number, self.end_frame_number)
    
    def get_annotations(self) -> ImageClassificationAnnotation.objects:
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(frame__in=frames, label=self.label)

        return annotations
    
    def get_frames_without_annotation(self):
        """
        Get a frame without an annotation.
        """
        assert 1 == 2, "This method should be overridden in derived classes"

    def get_segment_len_in_s(self):
        return (self.end_frame_number - self.start_frame_number) / self.video.fps

class LegacyLabelVideoSegment(AbstractLabelVideoSegment):
    video = models.ForeignKey("LegacyVideo", on_delete=models.CASCADE)
    prediction_meta = models.ForeignKey("LegacyVideoPredictionMeta", on_delete=models.CASCADE, related_name="video_segments")

    def get_video_model(self):
        from endoreg_db.models.data_file.video import LegacyVideo
        return LegacyVideo

    def get_annotations(self) -> ImageClassificationAnnotation.objects:
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(legacy_frame__in=frames, label=self.label)

        return annotations

    def get_frames_without_annotation(self, n_frames):
        """
        Get a frame without an annotation.
        """
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(legacy_frame__in=frames, label=self.label)

        annotated_frames = [annotation.legacy_frame for annotation in annotations]
        frames_without_annotation = [frame for frame in frames if frame not in annotated_frames]

        # draw n random frames
        if len(frames_without_annotation) > n_frames:
            frames_without_annotation = np.random.choice(frames_without_annotation, n_frames, replace=False)

        return frames_without_annotation
    
class LabelVideoSegment(AbstractLabelVideoSegment):
    video = models.ForeignKey("Video", on_delete=models.CASCADE)
    prediction_meta = models.ForeignKey("VideoPredictionMeta", on_delete=models.CASCADE, related_name="video_segments")

    def get_video_model(self):
        from endoreg_db.models.data_file.video import Video
        return Video

    def get_frames_without_annotation(self, n_frames):
        """
        Get a frame without an annotation.
        """
        frames = self.get_frames()
        annotations = ImageClassificationAnnotation.objects.filter(frame__in=frames, label=self.label)

        annotated_frames = [annotation.frame for annotation in annotations]
        frames_without_annotation = [frame for frame in frames if frame not in annotated_frames]

        # draw n random frames
        if len(frames_without_annotation) > n_frames:
            frames_without_annotation = np.random.choice(frames_without_annotation, n_frames, replace=False)

        return frames_without_annotation
