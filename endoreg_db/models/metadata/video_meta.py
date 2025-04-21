from django.db import models
import subprocess
import json
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
        from endoreg_db.models import EndoscopyProcessor

        processor: EndoscopyProcessor = self.processor
        endo_roi = processor.get_roi_endoscope_image()
        return endo_roi

    @property
    def fps(self) -> Optional[float]:
        """Returns the frame rate from the linked FFMpegMeta."""
        if not self.ffmpeg_meta:
            logger.warning("FFMpegMeta not linked for VideoMeta PK %s. Cannot get FPS.", self.pk)
            return None
        return self.ffmpeg_meta.frame_rate

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
        if self.ffmpeg_meta and self.ffmpeg_meta.duration is not None and self.ffmpeg_meta.frame_rate is not None and self.ffmpeg_meta.frame_rate > 0:
            return int(self.ffmpeg_meta.duration * self.ffmpeg_meta.frame_rate)
        return None


class FFMpegMeta(models.Model):
    duration = models.FloatField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    frame_rate = models.FloatField(blank=True, null=True)
    video_codec = models.CharField(max_length=50, blank=True, null=True)
    audio_codec = models.CharField(max_length=50, blank=True, null=True)
    audio_channels = models.IntegerField(blank=True, null=True)
    audio_sample_rate = models.IntegerField(blank=True, null=True)

    @classmethod
    def create_from_file(cls, file_path: Path):
        assert isinstance(file_path, Path), "file_path must be a Path object"
        assert file_path.exists(), "file_path does not exist"
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(file_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
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

            duration_str = video_stream.get("duration")
            duration = float(duration_str) if duration_str is not None else None

            width_str = video_stream.get("width")
            width = int(width_str) if width_str is not None else None

            height_str = video_stream.get("height")
            height = int(height_str) if height_str is not None else None

            frame_rate_str = video_stream.get("avg_frame_rate", "0/1")
            try:
                num, den = map(int, frame_rate_str.split('/'))
                frame_rate = float(num / den) if den != 0 else None
            except (ValueError, ZeroDivisionError):
                frame_rate = None

            video_codec = video_stream.get("codec_name")

            metadata = {
                "duration": duration,
                "width": width,
                "height": height,
                "frame_rate": frame_rate,
                "video_codec": video_codec,
            }

            if audio_streams:
                first_audio_stream = audio_streams[0]
                audio_codec = first_audio_stream.get("codec_name")
                audio_channels_str = first_audio_stream.get("channels")
                audio_channels = int(audio_channels_str) if audio_channels_str is not None else None
                audio_sample_rate_str = first_audio_stream.get("sample_rate")
                audio_sample_rate = int(audio_sample_rate_str) if audio_sample_rate_str is not None else None

                metadata.update(
                    {
                        "audio_codec": audio_codec,
                        "audio_channels": audio_channels,
                        "audio_sample_rate": audio_sample_rate,
                    }
                )

            instance = cls.objects.create(**metadata)
            logger.info("Created FFMpegMeta PK %s from %s", instance.pk, file_path.name)
            return instance

        except subprocess.CalledProcessError as e:
            logger.error("ffprobe command failed for %s: %s\n%s", file_path, e, e.stderr)
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse ffprobe JSON output for %s: %s", file_path, e)
            return None
        except Exception as e:
            logger.error("Error creating FFMpegMeta from %s: %s", file_path, e, exc_info=True)
            return None

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
