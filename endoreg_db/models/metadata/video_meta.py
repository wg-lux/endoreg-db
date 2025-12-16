import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# import endoreg_center_id from django settings
from django.conf import settings
from django.db import models

# check if endoreg_center_id is set
if not hasattr(settings, "ENDOREG_CENTER_ID"):
    ENDOREG_CENTER_ID = 9999
else:
    ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

# Import the new utility function
from ...utils.video import ffmpeg_wrapper

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..administration import Center
    from ..medical.hardware import Endoscope, EndoscopyProcessor


# VideoMeta
class VideoMeta(models.Model):
    """
    Stores technical and contextual metadata related to a video file.

    Links to hardware (processor, endoscope), center, import details, and FFmpeg technical specs.
    """

    processor = models.ForeignKey("EndoscopyProcessor", on_delete=models.CASCADE, blank=True, null=True)
    endoscope = models.ForeignKey("Endoscope", on_delete=models.CASCADE, blank=True, null=True)
    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    import_meta = models.OneToOneField("VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True)
    ffmpeg_meta = models.OneToOneField("FFMpegMeta", on_delete=models.CASCADE, blank=True, null=True)

    if TYPE_CHECKING:
        processor: models.ForeignKey["EndoscopyProcessor|None"]
        endoscope: models.ForeignKey["Endoscope|None"]
        center: models.ForeignKey["Center|None"]
        import_meta: models.OneToOneField["VideoImportMeta|None"]
        ffmpeg_meta: models.OneToOneField["FFMpegMeta|None"]

    @property
    def center_safe(self) -> "Center":
        center = self.center
        if not center:
            raise Center.DoesNotExist("Center does not exist for this VideoMeta instance.")
        return center

    @property
    def processor_safe(self) -> "EndoscopyProcessor":
        processor = self.processor
        if not processor:
            raise EndoscopyProcessor.DoesNotExist("EndoscopyProcessor does not exist for this VideoMeta instance.")
        return processor

    @property
    def ffmpeg_meta_safe(self) -> "FFMpegMeta":
        ffmpeg_meta = self.ffmpeg_meta
        if not ffmpeg_meta:
            raise FFMpegMeta.DoesNotExist("FFMpegMeta does not exist for this VideoMeta instance.")
        return ffmpeg_meta

    @classmethod
    def create_from_file(
        cls,
        video_path: Path,
        center: "Center",
        processor: Optional["EndoscopyProcessor"] = None,
        endoscope: Optional["Endoscope"] = None,
        save_instance: bool = True,
    ) -> "VideoMeta":
        """
        Create a new VideoMeta from a video file path, initializing FFMpegMeta.
        Raises FileNotFoundError, TypeError, or RuntimeError on failure.
        """
        if not isinstance(video_path, Path):
            raise TypeError("video_path must be a Path object")
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found at {video_path}")

        meta = cls(center=center, processor=processor, endoscope=endoscope)
        try:
            # initialize_ffmpeg_meta now raises exceptions on failure
            meta.initialize_ffmpeg_meta(video_path)
        except Exception as e:
            # Re-raise exceptions from ffmpeg meta initialization
            logger.error("Failed during FFMpegMeta initialization within create_from_file for %s: %s", video_path.name, e, exc_info=True)
            raise RuntimeError(f"Failed to initialize FFMpeg metadata for {video_path.name}") from e

        if save_instance:
            meta.save()  # This ensures VideoImportMeta is created too
            logger.info("Created and saved VideoMeta instance PK %s from %s", meta.pk, video_path.name)
        else:
            logger.info("Instantiated VideoMeta from %s (not saved yet)", video_path.name)

        return meta

    def __str__(self):
        """Returns a string summary of the video metadata."""
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
        """Ensures VideoImportMeta exists before saving."""
        if self.import_meta is None:
            self.import_meta = VideoImportMeta.objects.create()
        super(VideoMeta, self).save(*args, **kwargs)

    def initialize_ffmpeg_meta(self, video_path: Path):
        """
        Initializes FFMpeg metadata for the video file if not already done.
        Raises RuntimeError if FFMpegMeta creation fails.
        """
        if self.ffmpeg_meta:
            logger.debug("FFMpegMeta already exists for VideoMeta PK %s. Skipping initialization.", self.pk)
            return

        logger.info("Initializing FFMpegMeta for VideoMeta PK %s from %s", self.pk if self.pk else "(unsaved)", video_path.name)
        try:
            # FFMpegMeta.create_from_file now raises exceptions on failure
            ffmpeg_instance = FFMpegMeta.create_from_file(video_path)
            # If create_from_file succeeds, link it. No need to check for None.
            self.ffmpeg_meta = ffmpeg_instance
            # If the VideoMeta instance is already saved, save the link immediately.
            # Otherwise, the link will be saved when VideoMeta itself is saved.
            if self.pk:
                self.save(update_fields=["ffmpeg_meta"])
            logger.info("Successfully created and linked FFMpegMeta PK %s", self.ffmpeg_meta_safe.pk)

        except Exception as e:
            # Log the error and re-raise it
            logger.error("Failed to create or link FFMpegMeta from %s: %s", video_path, e, exc_info=True)
            raise RuntimeError(f"Failed to create FFMpeg metadata from {video_path}") from e

    def update_meta(self, video_path: Path):
        """
        Updates the FFMpeg metadata from the file, replacing existing data.
        Raises RuntimeError if FFMpegMeta creation fails.
        """
        logger.info("Updating FFMpegMeta for VideoMeta PK %s from %s", self.pk, video_path.name)
        existing_ffmpeg_pk = None
        if self.ffmpeg_meta:
            existing_ffmpeg_pk = self.ffmpeg_meta.pk
            logger.debug("Deleting existing FFMpegMeta PK %s before update.", existing_ffmpeg_pk)
            # Nullify the relation first before deleting the related object
            self.ffmpeg_meta = None
            self.save(update_fields=["ffmpeg_meta"])  # Save the null relation
            FFMpegMeta.objects.filter(pk=existing_ffmpeg_pk).delete()  # Delete the old object

        # initialize_ffmpeg_meta handles creation, linking, saving the link, and raises exceptions
        self.initialize_ffmpeg_meta(video_path)

    def get_endo_roi(self):
        """Retrieves the endoscope region of interest (ROI) from the associated processor."""
        from ..medical.hardware import EndoscopyProcessor

        processor: EndoscopyProcessor = self.processor_safe
        endo_roi = processor.get_roi_endoscope_image()
        return endo_roi

    @property
    def fps(self) -> Optional[float]:
        """Returns the frame rate (FPS) from the linked FFMpegMeta."""
        if not self.ffmpeg_meta:
            logger.warning("FFMpegMeta not linked for VideoMeta PK %s. Cannot get FPS.", self.pk)
            return None
        return self.ffmpeg_meta.fps

    @property
    def duration(self) -> Optional[float]:
        """Returns the duration in seconds from the linked FFMpegMeta."""
        return self.ffmpeg_meta.duration if self.ffmpeg_meta else None

    @property
    def width(self) -> Optional[int]:
        """Returns the video width in pixels from the linked FFMpegMeta."""
        return self.ffmpeg_meta.width if self.ffmpeg_meta else None

    @property
    def height(self) -> Optional[int]:
        """Returns the video height in pixels from the linked FFMpegMeta."""
        return self.ffmpeg_meta.height if self.ffmpeg_meta else None

    @property
    def frame_count(self) -> Optional[int]:
        """Calculates frame count based on duration and FPS from FFMpegMeta."""
        if self.ffmpeg_meta and self.ffmpeg_meta.duration is not None and self.ffmpeg_meta.fps is not None and self.ffmpeg_meta.fps > 0:
            return int(self.ffmpeg_meta.duration * self.ffmpeg_meta.fps)
        return None


class FFMpegMeta(models.Model):
    """
    Stores technical video stream information extracted using FFmpeg (ffprobe).
    """

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
        """Calculates and returns the frames per second (FPS) if possible."""
        if self.frame_rate_num is not None and self.frame_rate_den is not None and self.frame_rate_den != 0:
            return self.frame_rate_num / self.frame_rate_den
        return None

    @classmethod
    def create_from_file(cls, file_path: Path):
        """
        Creates an FFMpegMeta instance by running ffprobe on the given file path.
        Raises RuntimeError on failure.
        """
        logger.info("Running ffprobe on %s", file_path)
        try:
            probe_data = ffmpeg_wrapper.get_stream_info(file_path)  # Use the new utility
        except Exception as probe_err:
            logger.error("ffprobe execution failed for %s: %s", file_path, probe_err, exc_info=True)
            raise RuntimeError(f"ffprobe execution failed for {file_path}") from probe_err

        if not probe_data or "streams" not in probe_data:
            logger.error("Failed to get valid stream info from ffprobe for %s", file_path)
            # Raise exception instead of returning None
            raise RuntimeError(f"Invalid stream info from ffprobe for {file_path}")

        video_stream = next((s for s in probe_data["streams"] if s.get("codec_type") == "video"), None)

        if not video_stream:
            logger.warning("No video stream found in ffprobe output for %s", file_path)
            # Raise exception instead of returning None
            raise RuntimeError(f"No video stream found in {file_path}")

        # Extract data safely using .get()
        width = video_stream.get("width")
        height = video_stream.get("height")
        duration_str = video_stream.get("duration")
        # --- FIX: Handle potential format key ---
        if duration_str is None and "format" in probe_data and "duration" in probe_data["format"]:
            duration_str = probe_data["format"]["duration"]
            logger.debug("Using duration from format block: %s", duration_str)
        # --- End Fix ---
        duration = float(duration_str) if duration_str else None

        # Frame rate can be tricky, often represented as "num/den"
        frame_rate_str = video_stream.get("r_frame_rate")
        # --- FIX: Fallback to avg_frame_rate if r_frame_rate is invalid ---
        if not frame_rate_str or frame_rate_str == "0/0":
            frame_rate_str = video_stream.get("avg_frame_rate")
            logger.debug("Using avg_frame_rate as fallback: %s", frame_rate_str)
        # --- End Fix ---
        frame_rate_num, frame_rate_den = None, None
        if frame_rate_str and "/" in frame_rate_str:
            try:
                num_str, den_str = frame_rate_str.split("/")
                frame_rate_num = int(num_str)
                frame_rate_den = int(den_str)
                if frame_rate_den == 0:  # Avoid division by zero
                    logger.warning("Invalid frame rate denominator (0) for %s", file_path)
                    frame_rate_num, frame_rate_den = None, None
            except ValueError:
                logger.warning("Could not parse frame rate '%s' for %s", frame_rate_str, file_path)
                frame_rate_num, frame_rate_den = None, None

        codec_name = video_stream.get("codec_name")
        pixel_format = video_stream.get("pix_fmt")
        bit_rate_str = video_stream.get("bit_rate")
        # --- FIX: Handle potential format key for bit_rate ---
        if bit_rate_str is None and "format" in probe_data and "bit_rate" in probe_data["format"]:
            bit_rate_str = probe_data["format"]["bit_rate"]
            logger.debug("Using bit_rate from format block: %s", bit_rate_str)
        # --- End Fix ---
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
                raw_probe_data=probe_data,
            )
            logger.info("Successfully created FFMpegMeta for %s (ID: %d)", file_path.name, instance.pk)
            return instance
        except Exception as e:
            logger.error("Error creating FFMpegMeta DB record from %s: %s", file_path.name, e, exc_info=True)
            # Raise exception instead of returning None
            raise RuntimeError(f"Database error creating FFMpegMeta for {file_path.name}") from e

    def __str__(self):
        """Returns a string summary of the FFmpeg metadata."""
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
    """
    Stores metadata related to the import and processing status of a video.
    """

    file_name = models.CharField(max_length=255, blank=True, null=True)
    video_anonymized = models.BooleanField(default=False)
    video_patient_data_detected = models.BooleanField(default=False)
    outside_detected = models.BooleanField(default=False)
    patient_data_removed = models.BooleanField(default=False)
    outside_removed = models.BooleanField(default=False)

    def __str__(self):
        """Returns a string summary of the import metadata."""
        result_html = ""

        result_html += f"Video anonymized: {self.video_anonymized}\n"
        result_html += f"Video patient data detected: {self.video_patient_data_detected}\n"
        result_html += f"Outside detected: {self.outside_detected}\n"
        result_html += f"Patient data removed: {self.patient_data_removed}\n"
        result_html += f"Outside removed: {self.outside_removed}\n"
        return result_html
