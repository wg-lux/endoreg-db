"""Concrete model for video files, handling both raw and processed states."""

import logging
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING, Union, Dict, Tuple

from django.db import models
from django.db.models import QuerySet
from django.core.validators import FileExtensionValidator
from django.core.files.storage import FileSystemStorage
from django.core.files.base import ContentFile
from django.db import transaction
from icecream import ic

# --- Import model-specific function modules ---
from .video_file_save import _save_video_file
from .video_file_state import _get_or_create_state, _set_frames_extracted
from .video_file_segments import (
    _sequences_to_label_video_segments,
    _get_outside_segments,
    _get_outside_frames,
    _get_outside_frame_paths,
    _label_segments_to_frame_annotations,
)
from .video_file_anonymize import _anonymize, _censor_outside_frames, _cleanup_raw_assets, _make_temporary_anonymized_frames
from .video_file_ai import _predict_video_entry, _extract_text_information
from .video_file_meta import (
    _update_text_metadata,
    _update_video_meta,
    _get_fps,
    _get_endo_roi,
    _get_crop_template,
    _initialize_video_specs,
)
from .video_file_frames import (
    _extract_frames,
    _initialize_frames,
    _delete_frames,
    _get_frame_path,
    _get_frame_paths,
    _get_frame_number,
    _get_frames,
    _get_frame,
    _get_frame_range,
    _create_frame_object,
    _bulk_create_frames,
)
from .video_file_io import (
    _delete_with_file,
    _get_base_frame_dir,
    _set_frame_dir,
    _get_frame_dir_path,
    _get_temp_anonymized_frame_dir,
    _get_target_anonymized_video_path,
    _get_raw_file_path,
    _get_processed_file_path,
)
# --- End Import model-specific function modules ---

# --- Utility Imports ---
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
from ....utils.video import (
    transcode_videofile,
    transcode_videofile_if_required,
)
# --- End Utility Imports ---

# --- Model & Other Imports ---
from ...utils import TEST_RUN, TEST_RUN_FRAME_NUMBER, data_paths, VIDEO_DIR, FILE_STORAGE, ANONYM_VIDEO_DIR, STORAGE_DIR
from ...metadata import VideoMeta, SensitiveMeta
from ...state import VideoState
# --- End Model & Other Imports ---


# Configure logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...label import LabelVideoSegment, Label
    from ..frame import Frame
    from ...metadata import ModelMeta, VideoPredictionMeta, RawVideoPredictionMeta, VideoImportMeta
    from ...administration import Center
    from ...medical.hardware import EndoscopyProcessor
    from ...medical.patient import PatientExamination, Patient


class VideoFile(models.Model):
    """
    Concrete Django model for video files.

    References both the original raw video file and an optional processed/anonymized
    version. Access via the `active_file` property prioritizes the processed file.
    Methods are implemented in separate modules within this directory (e.g., video_file_frames.py).
    """

    uuid = models.UUIDField(unique=True)

    # --- File Fields ---
    raw_file = models.FileField(
        upload_to=VIDEO_DIR,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        storage=FILE_STORAGE,
        null=True,
        blank=True,
    )
    processed_file = models.FileField(
        upload_to=ANONYM_VIDEO_DIR,
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        storage=FILE_STORAGE,
        null=True,
        blank=True,
    )

    # --- Hash Fields ---
    video_hash = models.CharField(max_length=255, unique=True, help_text="Hash of the raw video file.")
    processed_video_hash = models.CharField(
        max_length=255, unique=True, null=True, blank=True, help_text="Hash of the processed video file, unique if not null."
    )

    # --- Foreign Keys & One-to-One ---
    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        related_name="video_files",
        null=True,
        blank=True,
    )
    center = models.ForeignKey("Center", on_delete=models.PROTECT)
    processor = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.PROTECT, blank=True, null=True
    )
    video_meta = models.OneToOneField(
        "VideoMeta", on_delete=models.SET_NULL, blank=True, null=True
    )
    examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    patient = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    ai_model_meta = models.ForeignKey(
        "ModelMeta", on_delete=models.SET_NULL, blank=True, null=True
    )
    state = models.OneToOneField(
        "VideoState",
        on_delete=models.CASCADE,
        related_name="video_file",
        null=True,
        blank=True,
    )
    import_meta = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    # --- Metadata & Other Fields ---
    original_file_name = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    frame_dir = models.CharField(max_length=512, blank=True, help_text="Path to frames extracted from the raw video.")
    fps = models.FloatField(blank=True, null=True)
    duration = models.FloatField(blank=True, null=True)
    frame_count = models.IntegerField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    suffix = models.CharField(max_length=10, blank=True, null=True)
    sequences = models.JSONField(default=dict, blank=True, help_text="AI prediction sequences based on raw frames.")
    date = models.DateField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)

    # --- Type Hinting for Related Managers ---
    if TYPE_CHECKING:
        label_video_segments: "QuerySet[LabelVideoSegment]"
        frames: "QuerySet[Frame]

    # --- Properties ---
    @property
    def is_processed(self) -> bool:
        return bool(self.processed_file and self.processed_file.name)

    @property
    def has_raw(self) -> bool:
        return bool(self.raw_file and self.raw_file.name)

    @property
    def active_file(self) -> Optional[models.FileField]:
        if self.is_processed:
            return self.processed_file
        elif self.has_raw:
            return self.raw_file
        else:
            logger.warning("VideoFile %s has neither processed nor raw file set.", self.uuid)
            return None

    @property
    def active_file_path(self) -> Optional[Path]:
        active = self.active_file
        try:
            if active == self.processed_file:
                return _get_processed_file_path(self)
            elif active == self.raw_file:
                return _get_raw_file_path(self)
            else:
                return None
        except Exception as e:
            logger.warning("Could not get path for active file of VideoFile %s: %s", self.uuid, e, exc_info=True)
            return None

    # --- Meta Class ---
    class Meta:
        indexes = [
            models.Index(fields=["video_hash"]),
            models.Index(fields=["processed_video_hash"]),
            models.Index(fields=["uuid"]),
            models.Index(fields=["center", "processor"]),
            models.Index(fields=["examination"]),
        ]
        verbose_name = "Video File"
        verbose_name_plural = "Video Files"

    # --- Class Methods ---
    @classmethod
    def transcode_videofile(cls, filepath: Path, transcoded_path: Path) -> Path:
        try:
            result_path = transcode_videofile(filepath=filepath, transcoded_path=transcoded_path)
            return result_path
        except Exception as e:
            raise e

    @classmethod
    def check_hash_exists(cls, video_hash: str) -> bool:
        return cls.objects.filter(video_hash=video_hash).exists()

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        center_name: str,
        processor_name: Optional[str] = None,
        video_dir: Path = VIDEO_DIR,
        save: bool = True,
        delete_source: bool = False,
    ) -> "VideoFile":
        from .create_from_file import _create_from_file
        return _create_from_file(
            cls_model=cls,
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            video_dir=video_dir,
            save=save,
            delete_source=delete_source,
        )

    # --- Instance Methods (Delegated to Helper Functions) ---
    def save(self, *args, **kwargs):
        return _save_video_file(self, *args, **kwargs)

    def get_or_create_state(self) -> "VideoState":
        return _get_or_create_state(self)

    def set_frames_extracted(self, value: bool = True):
        return _set_frames_extracted(self, value)

    def sequences_to_label_video_segments(self, video_prediction_meta: "VideoPredictionMeta"):
        return _sequences_to_label_video_segments(self, video_prediction_meta)

    def get_outside_segments(self, outside_label_name: str = "outside") -> "QuerySet[LabelVideoSegment]":
        return _get_outside_segments(self, outside_label_name)

    def get_outside_frames(self, outside_label_name: str = "outside") -> List["Frame"]:
        return _get_outside_frames(self, outside_label_name)

    def get_outside_frame_paths(self, outside_label_name: str = "outside") -> List[Path]:
        return _get_outside_frame_paths(self, outside_label_name)

    def label_segments_to_frame_annotations(self):
        return _label_segments_to_frame_annotations(self)

    def get_frame_number(self) -> int:
        return _get_frame_number(self)

    def get_frames(self) -> "QuerySet[Frame]":
        return _get_frames(self)

    def get_frame(self, frame_number: int) -> "Frame":
        return _get_frame(self, frame_number)

    def get_frame_range(self, start_frame_number: int, end_frame_number: int) -> "QuerySet[Frame]":
        return _get_frame_range(self, start_frame_number, end_frame_number)

    def extract_frames(self, quality: int = 2, overwrite: bool = False, ext="jpg", verbose=False) -> List[Path]:
        return _extract_frames(self, quality, overwrite, ext, verbose)

    def initialize_frames(self, paths: List[Path]):
        return _initialize_frames(self, paths)

    def delete_frames(self) -> str:
        return _delete_frames(self)

    def get_frame_path(self, frame_number: int) -> Optional[Path]:
        return _get_frame_path(self, frame_number)

    def get_frame_paths(self) -> List[Path]:
        return _get_frame_paths(self)

    def create_frame_object(self, frame_number: int, image_file=None, extracted: bool = False) -> "Frame":
        return _create_frame_object(self, frame_number, image_file, extracted)

    def bulk_create_frames(self, frames_to_create: List["Frame"]):
        return _bulk_create_frames(self, frames_to_create)

    def predict_video(
        self,
        model_meta_name: str,
        model_meta_version: Optional[int] = None,
        dataset_name: str = "inference_dataset",
        smooth_window_size_s: int = 1,
        binarize_threshold: float = 0.5,
        test_run: bool = TEST_RUN,
        n_test_frames: int = TEST_RUN_FRAME_NUMBER,
        save_results: bool = True,
    ):
        from .predict_video import _predict_video
        return _predict_video(
            video=self,
            model_meta_name=model_meta_name,
            model_meta_version=model_meta_version,
            dataset_name=dataset_name,
            smooth_window_size_s=smooth_window_size_s,
            binarize_threshold=binarize_threshold,
            test_run=test_run,
            n_test_frames=n_test_frames,
            save_results=save_results,
        )

    def extract_text_information(self, frame_fraction: float = 0.001, cap: int = 15) -> Optional[Dict[str, str]]:
        return _extract_text_information(self, frame_fraction, cap)

    def update_text_metadata(self, ocr_frame_fraction=0.001, cap: int = 15, save_instance: bool = True):
        return _update_text_metadata(self, ocr_frame_fraction, cap, save_instance)

    def update_video_meta(self, save_instance: bool = True):
        return _update_video_meta(self, save_instance)

    def get_fps(self) -> Optional[float]:
        return _get_fps(self)

    def get_endo_roi(self) -> Optional[Dict[str, int]]:
        return _get_endo_roi(self)

    def get_crop_template(self) -> Optional[List[int]]:
        return _get_crop_template(self)

    def initialize_video_specs(self, use_raw=True) -> bool:
        return _initialize_video_specs(self, use_raw)

    def censor_outside_frames(self):
        return _censor_outside_frames(self)

    def _make_temporary_anonymized_frames(self) -> Tuple[Path, List[Path]]:
        return _make_temporary_anonymized_frames(self)

    def anonymize(self, delete_original_raw: bool = True) -> Path:
        return _anonymize(self, delete_original_raw)

    def _cleanup_raw_assets(self, raw_file_path: Path, raw_frame_dir: Optional[Path]):
        return _cleanup_raw_assets(self, raw_file_path, raw_frame_dir)

    def delete_with_file(self, *args, **kwargs):
        return _delete_with_file(self, *args, **kwargs)

    def _get_base_frame_dir(self) -> Path:
        return _get_base_frame_dir(self)

    def set_frame_dir(self, force_update: bool = False):
        return _set_frame_dir(self, force_update)

    def get_frame_dir_path(self) -> Optional[Path]:
        return _get_frame_dir_path(self)

    def _get_temp_anonymized_frame_dir(self) -> Path:
        return _get_temp_anonymized_frame_dir(self)

    def _get_target_anonymized_video_path(self) -> Path:
        return _get_target_anonymized_video_path(self)

    def get_raw_file_path(self) -> Optional[Path]:
        return _get_raw_file_path(self)

    def get_processed_file_path(self) -> Optional[Path]:
        return _get_processed_file_path(self)

    def __str__(self) -> str:
        active_path = self.active_file_path
        file_name = active_path.name if active_path else "No file"
        state = "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        return f"VideoFile ({state}): {file_name} (UUID: {self.uuid})"

    def get_frame_model(self):
        try:
            from ..frame import Frame
            return Frame
        except ImportError:
            logger.error("Could not import Frame model.")
            raise

