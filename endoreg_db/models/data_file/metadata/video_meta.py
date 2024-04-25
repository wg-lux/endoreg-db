from django.db import models
import ffmpeg
from pathlib import Path

# import endoreg_center_id from django settings
from django.conf import settings

# check if endoreg_center_id is set
if not hasattr(settings, 'ENDOREG_CENTER_ID'):
    ENDOREG_CENTER_ID = 9999
else:
    ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

# VideoMeta
class VideoMeta(models.Model):
    processor = models.ForeignKey('EndoscopyProcessor', on_delete=models.CASCADE, blank=True, null=True)
    endoscope = models.ForeignKey('Endoscope', on_delete=models.CASCADE, blank=True, null=True)
    center = models.ForeignKey('Center', on_delete=models.CASCADE)
    import_meta = models.OneToOneField('VideoImportMeta', on_delete=models.CASCADE, blank=True, null=True)
    ffmpeg_meta = models.OneToOneField('FFMpegMeta', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):

        processor_name = self.processor.name if self.processor is not None else "None"
        endoscope_name = self.endoscope.name if self.endoscope is not None else "None"
        center_name = self.center.name if self.center is not None else "None"

        result_html = ""

        result_html += f"Processor: {processor_name}<br>"
        result_html += f"Endoscope: {endoscope_name}<br>"
        result_html += f"Center: {center_name}<br>"

        return result_html
    
    # import meta should be created when video meta is created
    def save(self, *args, **kwargs):
        if self.import_meta is None:
            self.import_meta = VideoImportMeta.objects.create()
        super(VideoMeta, self).save(*args, **kwargs)

    def initialize_ffmpeg_meta(self, file_path):
        """Initializes FFMpeg metadata for the video file if not already done."""
        self.ffmpeg_meta = FFMpegMeta.create_from_file(Path(file_path))
        self.save()

    def update_meta(self, file_path):
        """Updates the video metadata from the file."""
        self.initialize_ffmpeg_meta(file_path)
        self.save()

    def get_endo_roi(self):
        endo_roi = self.processor.get_roi_endoscope_image()
        return endo_roi

    def get_fps(self):
        if not self.ffmpeg_meta:
            return None
    
        return self.ffmpeg_meta.frame_rate
 

class FFMpegMeta(models.Model):
    # Existing fields
    duration = models.FloatField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    frame_rate = models.FloatField(blank=True, null=True)
    
    # New fields for comprehensive information
    video_codec = models.CharField(max_length=50, blank=True, null=True)
    audio_codec = models.CharField(max_length=50, blank=True, null=True)
    audio_channels = models.IntegerField(blank=True, null=True)
    audio_sample_rate = models.IntegerField(blank=True, null=True)

    # Existing __str__ method can be updated to include new fields

    @classmethod
    def create_from_file(cls, file_path: Path):
        """Creates an FFMpegMeta instance from a video file using ffmpeg probe."""
        try:
            probe = ffmpeg.probe(file_path.resolve().as_posix())
        except: # ffmpeg.Error as e:
            # print(e.stderr)
            print(f"Error while probing {file_path}")
            return None

        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        audio_streams = [stream for stream in probe['streams'] if stream['codec_type'] == 'audio']

        # Check for the existence of a video stream
        if video_stream is None:
            print(f"No video stream found in {file_path}")
            return None

        # Extract and store video metadata
        metadata = {
            'duration': float(video_stream.get('duration', 0)),
            'width': int(video_stream.get('width', 0)),
            'height': int(video_stream.get('height', 0)),
            'frame_rate': float(next(iter(video_stream.get('avg_frame_rate', '').split('/')), 0)),
            'video_codec': video_stream.get('codec_name', ''),
        }

        # If there are audio streams, extract and store audio metadata from the first stream
        if audio_streams:
            first_audio_stream = audio_streams[0]
            metadata.update({
                'audio_codec': first_audio_stream.get('codec_name', ''),
                'audio_channels': int(first_audio_stream.get('channels', 0)),
                'audio_sample_rate': int(first_audio_stream.get('sample_rate', 0)),
            })

        # Create and return the FFMpegMeta instance
        return cls.objects.create(**metadata)

class VideoImportMeta(models.Model):

    video_anonymized = models.BooleanField(default=False)
    video_patient_data_detected = models.BooleanField(default=False)
    outside_detected = models.BooleanField(default=False)
    patient_data_removed = models.BooleanField(default=False)
    outside_removed = models.BooleanField(default=False)

    def __str__(self):
        result_html = ""

        result_html += f"Video anonymized: {self.video_anonymized}<br>"
        result_html += f"Video patient data detected: {self.video_patient_data_detected}<br>"
        result_html += f"Outside detected: {self.outside_detected}<br>"
        result_html += f"Patient data removed: {self.patient_data_removed}<br>"
        result_html += f"Outside removed: {self.outside_removed}<br>"
        return result_html