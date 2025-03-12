from django.db import models
import subprocess
import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

# import endoreg_center_id from django settings
from django.conf import settings

# check if endoreg_center_id is set
if not hasattr(settings, "ENDOREG_CENTER_ID"):
    ENDOREG_CENTER_ID = 9999
else:
    ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

if TYPE_CHECKING:
    from endoreg_db.models import EndoscopyProcessor, Endoscope, Center


# VideoMeta
class VideoMeta(models.Model):
    processor = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.CASCADE, blank=True, null=True
    )
    endoscope = models.ForeignKey(
        "Endoscope", on_delete=models.CASCADE, blank=True, null=True
    )
    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    import_meta = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )
    ffmpeg_meta = models.OneToOneField(
        "FFMpegMeta", on_delete=models.CASCADE, blank=True, null=True
    )  ##

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        center: Optional["Center"],
        processor: Optional["EndoscopyProcessor"] = None,
        endoscope: Optional["Endoscope"] = None,
    ):
        """Create a new VideoMeta from a file."""
        meta = cls.objects.create(center=center)
        meta.update_meta(file_path)

        if processor:
            meta.processor = processor
            meta.get_endo_roi()
        if endoscope:
            meta.endoscope = endoscope

        meta.save()

        return meta

    def __str__(self):
        processor_name = self.processor.name if self.processor is not None else "None"
        endoscope_name = self.endoscope.name if self.endoscope is not None else "None"
        center_name = self.center.name if self.center is not None else "None"
        ffmpeg_meta_str = self.ffmpeg_meta.__str__()
        import_meta_str = self.import_meta.__str__()

        result_html = ""

        result_html += f"Processor: {processor_name}\n"
        result_html += f"Endoscope: {endoscope_name}\n"
        result_html += f"Center: {center_name}\n"
        result_html += f"FFMpeg Meta: {ffmpeg_meta_str}\n"
        result_html += f"Import Meta: {import_meta_str}\n"

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
        from endoreg_db.models import EndoscopyProcessor

        processor: EndoscopyProcessor = self.processor
        endo_roi = processor.get_roi_endoscope_image()
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
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(file_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe = json.loads(proc.stdout)

        video_stream = next(
            (stream for stream in probe["streams"] if stream["codec_type"] == "video"),
            None,
        )
        audio_streams = [
            stream for stream in probe["streams"] if stream["codec_type"] == "audio"
        ]

        if not video_stream:
            return None

        metadata = {
            "duration": float(video_stream.get("duration", 0)),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "frame_rate": float(
                next(iter(video_stream.get("avg_frame_rate", "").split("/")), 0)
            ),
            "video_codec": video_stream.get("codec_name", ""),
        }

        if audio_streams:
            first_audio_stream = audio_streams[0]
            metadata.update(
                {
                    "audio_codec": first_audio_stream.get("codec_name", ""),
                    "audio_channels": int(first_audio_stream.get("channels", 0)),
                    "audio_sample_rate": int(first_audio_stream.get("sample_rate", 0)),
                }
            )

        return cls.objects.create(**metadata)

    def __str__(self):
        result_html = ""

        result_html += f"Duration: {self.duration}\n"
        result_html += f"Width: {self.width}\n"
        result_html += f"Height: {self.height}\n"
        result_html += f"Frame Rate: {self.frame_rate}\n"
        result_html += f"Video Codec: {self.video_codec}\n"
        result_html += f"Audio Codec: {self.audio_codec}\n"
        result_html += f"Audio Channels: {self.audio_channels}\n"
        result_html += f"Audio Sample Rate: {self.audio_sample_rate}\n"

        return result_html


class VideoImportMeta(models.Model):
    video_anonymized = models.BooleanField(default=False)
    video_patient_data_detected = models.BooleanField(default=False)
    outside_detected = models.BooleanField(default=False)
    patient_data_removed = models.BooleanField(default=False)
    outside_removed = models.BooleanField(default=False)

    def __str__(self):
        result_html = ""

        result_html += f"Video anonymized: {self.video_anonymized}\n"
        result_html += (
            f"Video patient data detected: {self.video_patient_data_detected}\n"
        )
        result_html += f"Outside detected: {self.outside_detected}\n"
        result_html += f"Patient data removed: {self.patient_data_removed}\n"
        result_html += f"Outside removed: {self.outside_removed}\n"
        return result_html
