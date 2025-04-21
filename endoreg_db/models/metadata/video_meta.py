from django.db import models
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

# import endoreg_center_id from django settings
from django.conf import settings

# check if endoreg_center_id is set
if not hasattr(settings, "ENDOREG_CENTER_ID"):
    ENDOREG_CENTER_ID = 9999
else:
    ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

# Import the new utility function
from ...utils.ffmpeg_wrapper import get_stream_info

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import EndoscopyProcessor, Endoscope, Center
    from ..media.video.video_file import VideoFile


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
    )

    @classmethod
    def create_from_file(
        cls,
        video_path: Path,
        center: "Center",
        processor: Optional["EndoscopyProcessor"] = None,
        endoscope: Optional["Endoscope"] = None,
        save_instance: bool = True,
    ) -> "VideoMeta":
        """Create a new VideoMeta from a video file path."""
        if not isinstance(video_path, Path):
            raise TypeError("video_path must be a Path object")
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found at {video_path}")

        meta = cls(center=center, processor=processor, endoscope=endoscope)
        meta.initialize_ffmpeg_meta(video_path)

        if save_instance:
            meta.save()
            logger.info("Created and saved VideoMeta instance PK %s from %s", meta.pk, video_path.name)
        else:
            logger.info("Instantiated VideoMeta from %s (not saved yet)", video_path.name)

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

    def save(self, *args, **kwargs):
        if self.import_meta is None:
            self.import_meta = VideoImportMeta.objects.create()
        super(VideoMeta, self).save(*args, **kwargs)

    def initialize_ffmpeg_meta(self, video_path: Path):
        """Initializes FFMpeg metadata for the video file if not already done."""
        if self.ffmpeg_meta:
            logger.debug("FFMpegMeta already exists for VideoMeta PK %s. Skipping initialization.", self.pk)
            return

        logger.info("Initializing FFMpegMeta for VideoMeta PK %s from %s", self.pk, video_path.name)
        try:
            self.ffmpeg_meta = FFMpegMeta.create_from_file(video_path)
            if self.ffmpeg_meta:
                logger.info("Successfully created FFMpegMeta PK %s", self.ffmpeg_meta.pk)
            else:
                logger.warning("FFMpegMeta creation returned None for %s", video_path.name)
        except Exception as e:
            logger.error("Failed to create FFMpegMeta from %s: %s", video_path, e, exc_info=True)

    def update_meta(self, video_path: Path):
        """Updates the video metadata from the file."""
        logger.info("Updating FFMpegMeta for VideoMeta PK %s from %s", self.pk, video_path.name)
        if self.ffmpeg_meta:
            logger.debug("Deleting existing FFMpegMeta PK %s before update.", self.ffmpeg_meta.pk)
            self.ffmpeg_meta.delete()
            self.ffmpeg_meta = None

        self.initialize_ffmpeg_meta(video_path)

    def get_endo_roi(self):
        from ..medical.hardware import EndoscopyProcessor

        processor: EndoscopyProcessor = self.processor
        endo_roi = processor.get_roi_endoscope_image()
        return endo_roi

    @property
    def fps(self) -> Optional[float]:
        """Returns the frame rate from the linked FFMpegMeta."""
        if not self.ffmpeg_meta:
            logger.warning("FFMpegMeta not linked for VideoMeta PK %s. Cannot get FPS.", self.pk)
            return None
        return self.ffmpeg_meta.fps

    @property
    def duration(self) -> Optional[float]:
        return self.ffmpeg_meta.duration if self.ffmpeg_meta else None

    @property
    def width(self) -> Optional[int]:
        return self.ffmpeg_meta.width if self.ffmpeg_meta else None

    @property
    def height(self) -> Optional[int]:
        return self.ffmpeg_meta.height if self.ffmpeg_meta else None

    @property
    def frame_count(self) -> Optional[int]:
        """Calculates frame count if possible."""
        if self.ffmpeg_meta and self.ffmpeg_meta.duration is not None and self.ffmpeg_meta.fps is not None and self.ffmpeg_meta.fps > 0:
            return int(self.ffmpeg_meta.duration * self.ffmpeg_meta.fps)
        return None


class FFMpegMeta(models.Model):
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True)  # Duration in seconds
    frame_rate_num = models.IntegerField(null=True, blank=True)  # Numerator for frame rate
    frame_rate_den = models.IntegerField(null=True, blank=True)  # Denominator for frame rate
    codec_name = models.CharField(max_length=50, null=True, blank=True)
    pixel_format = models.CharField(max_length=50, null=True, blank=True)
    bit_rate = models.BigIntegerField(null=True, blank=True)  # Bit rate in bits per second
    raw_probe_data = models.JSONField(null=True, blank=True)  # Store the full JSON output for debugging or future use

    @property
    def fps(self) -> Optional[float]:
        if self.frame_rate_num is not None and self.frame_rate_den is not None and self.frame_rate_den != 0:
            return self.frame_rate_num / self.frame_rate_den
        return None

    @classmethod
    def create_from_file(cls, file_path: Path):
        """
        Creates an FFMpegMeta instance by running ffprobe on the given file.
        """
        logger.info("Running ffprobe on %s", file_path)
        probe_data = get_stream_info(file_path)  # Use the new utility

        if not probe_data or "streams" not in probe_data:
            logger.error("Failed to get valid stream info from ffprobe for %s", file_path)
            return None

        video_stream = next((s for s in probe_data["streams"] if s.get("codec_type") == "video"), None)

        if not video_stream:
            logger.warning("No video stream found in ffprobe output for %s", file_path)
            return None

        # Extract data safely using .get()
        width = video_stream.get("width")
        height = video_stream.get("height")
        duration_str = video_stream.get("duration")
        duration = float(duration_str) if duration_str else None

        # Frame rate can be tricky, often represented as "num/den"
        frame_rate_str = video_stream.get("r_frame_rate")
        frame_rate_num, frame_rate_den = None, None
        if frame_rate_str and "/" in frame_rate_str:
            try:
                num_str, den_str = frame_rate_str.split('/')
                frame_rate_num = int(num_str)
                frame_rate_den = int(den_str)
            except ValueError:
                logger.warning("Could not parse frame rate '%s' for %s", frame_rate_str, file_path)

        codec_name = video_stream.get("codec_name")
        pixel_format = video_stream.get("pix_fmt")
        bit_rate_str = video_stream.get("bit_rate")
        bit_rate = int(bit_rate_str) if bit_rate_str else None

        try:
            instance = cls.objects.create(
                width=width,
                height=height,
                duration=duration,
                frame_rate_num=frame_rate_num,
                frame_rate_den=frame_rate_den,
                codec_name=codec_name,
                pixel_format=pixel_format,
                bit_rate=bit_rate,
                raw_probe_data=probe_data,  # Store the raw data
            )
            logger.info("Successfully created FFMpegMeta for %s (ID: %d)", file_path, instance.pk)
            return instance
        except Exception as e:
            logger.error("Error creating FFMpegMeta from %s: %s", file_path, e, exc_info=True)
            return None

    def __str__(self):
        result_html = ""

        result_html += f"Width: {self.width}\n"
        result_html += f"Height: {self.height}\n"
        result_html += f"Duration: {self.duration}\n"
        result_html += f"Frame Rate: {self.fps}\n"
        result_html += f"Codec Name: {self.codec_name}\n"
        result_html += f"Pixel Format: {self.pixel_format}\n"
        result_html += f"Bit Rate: {self.bit_rate}\n"

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


class SensitiveMeta(models.Model):
    is_validated = models.BooleanField(default=False, help_text="Indicates if the sensitive metadata has been validated.")
