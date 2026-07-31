"""Concrete model for video files, handling both raw and processed states."""

from __future__ import annotations

import logging
import uuid as uuid_lib
from contextlib import AbstractContextManager
from datetime import date, datetime
from pathlib import Path
from typing import (
    Any,
    TYPE_CHECKING,
    Protocol,
    Sequence,
    TypedDict,
    Unpack,
    cast,
)

import numpy as np
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from lx_dtypes.models.contracts.video_file import (
    FrameSourceMode,
    VideoFileMetaJsonObject,
)
from lx_dtypes.models.contracts.video_segments import (
    VideoSegmentsPayloadDict,
    validate_video_segments_payload,
)
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload
from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore
from numpy.typing import NDArray

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.media.video.storage_mode import (
    VIDEO_STORAGE_MODE_CHOICES,
    VideoStorageMode,
    get_default_video_storage_mode_value,
)
from endoreg_db.utils.paths import (
    ANONYM_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
)
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.schemas import validate_video_file_meta_payload
from .video_file_queries import VideoQuerySet

# Configure logging
logger = logging.getLogger(__name__)  # Changed from "video_file"

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.metadata.video_meta import FFMpegMeta
    from endoreg_db.models.metadata.model_meta import ModelMeta
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
    from endoreg_db.models.state.video import VideoState


class VideoFrameScoreResult(Protocol):
    @property
    def labels(self) -> list[str]: ...

    @property
    def frame_scores(self) -> NDArray[np.float64]: ...

    @property
    def device(self) -> str: ...

    @property
    def frame_count(self) -> int: ...

    @property
    def frame_numbers(self) -> list[int] | None: ...

    @property
    def timestamps(self) -> list[float] | None: ...


class _VideoFileCreateKwargs(TypedDict, total=False):
    pass


class VideoFile(models.Model):
    StorageMode = VideoStorageMode

    objects = VideoQuerySet.as_manager()
    default_fps: float = DEFAULT_VIDEO_FPS
    use_default_fps = True

    raw_file: models.FileField = models.FileField(
        upload_to=SENSITIVE_VIDEO_DIR.name,  # Use .name for relative path
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        null=True,
        blank=True,
    )
    processed_file: models.FileField = models.FileField(
        max_length=500,
        upload_to=ANONYM_VIDEO_DIR.name,  # Use .name for relative path
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["mp4"])],
        null=True,
        blank=True,
    )

    uuid: models.UUIDField[uuid_lib.UUID, Any] = models.UUIDField(
        default=uuid_lib.uuid4, unique=True, editable=False
    )

    video_hash: models.CharField[str, Any] = models.CharField(
        max_length=255, unique=True, help_text="Hash of the raw video file."
    )
    processed_video_hash: models.CharField[str | None, Any] = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Hash of the processed video file, unique if not null.",
    )

    sensitive_meta: models.OneToOneField[Any] = models.OneToOneField(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    center: models.ForeignKey[Any] = models.ForeignKey(
        "Center", on_delete=models.PROTECT
    )
    processor: models.ForeignKey[Any] = models.ForeignKey(
        "EndoscopyProcessor", on_delete=models.PROTECT, blank=True, null=True
    )
    video_meta: models.OneToOneField[Any] = models.OneToOneField(
        "VideoMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    examination: models.ForeignKey[Any] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    patient: models.ForeignKey[Any] = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="video_files",
    )
    ai_model_meta: models.ForeignKey[Any] = models.ForeignKey(
        "ModelMeta", on_delete=models.SET_NULL, blank=True, null=True
    )
    state: models.OneToOneField[Any] = models.OneToOneField(
        "VideoState",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="video_file",
    )
    import_meta: models.OneToOneField[Any] = models.OneToOneField(
        "VideoImportMeta", on_delete=models.CASCADE, blank=True, null=True
    )

    original_file_name: models.CharField[str | None, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    storage_mode: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=VIDEO_STORAGE_MODE_CHOICES,
        default=get_default_video_storage_mode_value,
        help_text=(
            "Controls whether this video stays on the legacy application-encrypted "
            "streaming path or may be handed off to Nginx from an encrypted "
            "filesystem-backed media root."
        ),
    )
    raw_streamable_relative_path: models.CharField[str, Any] = models.CharField(
        max_length=512,
        blank=True,
        help_text=(
            "Protected-media relative path for the raw streamable asset when "
            "storage_mode = fs_encrypted_streamable."
        ),
    )
    processed_streamable_relative_path: models.CharField[str, Any] = models.CharField(
        max_length=512,
        blank=True,
        help_text=(
            "Protected-media relative path for the processed streamable asset "
            "when storage_mode = fs_encrypted_streamable."
        ),
    )
    uploaded_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    frame_dir: models.CharField[str, Any] = models.CharField(
        max_length=512,
        blank=True,
        help_text="Path to frames extracted from the raw video.",
    )
    fps: models.FloatField[float | None, Any] = models.FloatField(blank=True, null=True)
    duration: models.FloatField[float | None, Any] = models.FloatField(
        blank=True, null=True
    )
    frame_count: models.IntegerField[int | None, Any] = models.IntegerField(
        blank=True, null=True
    )
    width: models.IntegerField[int | None, Any] = models.IntegerField(
        blank=True, null=True
    )
    height: models.IntegerField[int | None, Any] = models.IntegerField(
        blank=True, null=True
    )
    suffix: models.CharField[str | None, Any] = models.CharField(
        max_length=10, blank=True, null=True
    )
    sequences: models.JSONField[VideoSegmentsPayloadDict, VideoSegmentsPayloadDict] = (
        models.JSONField(
            default=dict,
            blank=True,
            help_text="AI prediction sequences based on raw frames.",
        )
    )
    export_segments_by_video: models.BooleanField[bool, Any] = models.BooleanField(
        default=False,
        help_text="If true, include all segments for this video in exports.",
    )
    date: models.DateField[date | None, Any] = models.DateField(blank=True, null=True)
    meta: models.JSONField[
        VideoFileMetaJsonObject | None, VideoFileMetaJsonObject | None
    ] = models.JSONField(blank=True, null=True)
    date_created: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True
    )

    if TYPE_CHECKING:
        pk: int
        sensitive_meta_id: int | None
        center_id: int
        processor_id: int | None
        video_meta_id: int | None
        examination_id: int | None
        patient_id: int | None
        ai_model_meta_id: int | None
        state_id: int | None
        import_meta_id: int | None

        @property
        def label_video_segments(self) -> models.Manager[LabelVideoSegment]: ...

        @property
        def frames(self) -> models.Manager[Frame]: ...

    class Meta:
        indexes = [
            models.Index(
                fields=["uploaded_at"],
                name="video_file_uploaded_at_idx",
            ),
            models.Index(
                fields=["center", "uploaded_at"],
                name="video_file_center_time_idx",
            ),
        ]

    @property
    def ffmpeg_meta(self) -> "FFMpegMeta":
        from endoreg_db.services.video_files import get_video_ffmpeg_meta

        return get_video_ffmpeg_meta(self)

    NO_ACTIVE_FILE = "Has no raw file"
    NO_FILE_ASSOCIATED = "Active file has no associated file."

    def ensure_local_raw_file(self) -> AbstractContextManager[Path]:
        from endoreg_db.services.video_files import ensure_local_raw_video_file

        return ensure_local_raw_video_file(self)

    def ensure_local_processed_file(self) -> AbstractContextManager[Path]:
        from endoreg_db.services.video_files import ensure_local_processed_video_file

        return ensure_local_processed_video_file(self)

    @property
    def active_raw_file(self) -> "FieldFile":
        from endoreg_db.services.video_files import get_active_raw_video_file

        return get_active_raw_video_file(self)

    def _protected_stream_url(self, *, file_type: str) -> str | None:
        from endoreg_db.services.video_files import (
            get_protected_video_stream_url,
            parse_video_artifact_kind,
        )

        return get_protected_video_stream_url(
            self,
            artifact_kind=parse_video_artifact_kind(file_type),
        )

    @property
    def active_raw_file_url(self) -> str | None:
        from endoreg_db.services.video_files import get_active_raw_video_file_url

        return get_active_raw_video_file_url(self)

    def update_video_meta(
        self, save_instance: bool = True, raw_video_path: Path | None = None
    ) -> "VideoFile | None":
        from endoreg_db.services.video_files import update_video_meta

        return update_video_meta(
            self, save_instance=save_instance, raw_video_path=raw_video_path
        )

    def initialize_video_specs(
        self, use_raw: bool = True, local_video_path: Path | None = None
    ) -> bool:
        from endoreg_db.services.video_files import initialize_video_specs

        return initialize_video_specs(
            self, use_raw=use_raw, local_video_path=local_video_path
        )

    def get_fps(self) -> float:
        from endoreg_db.services.video_files import get_video_fps

        return get_video_fps(self)

    def get_endo_roi(self) -> RoiBoxCore | None:
        from endoreg_db.services.video_files import get_video_endo_roi

        return get_video_endo_roi(self)

    def get_crop_template(self) -> list[int] | None:
        from endoreg_db.services.video_files import get_video_crop_template

        return get_video_crop_template(self)

    def update_text_metadata(
        self,
        extracted_data_dict: VideoFileMetaJsonObject | None = None,
        ocr_frame_fraction: float = 0.1,
        cap: int = 50,
        overwrite: bool = False,
    ) -> "SensitiveMeta | None":
        from endoreg_db.services.video_files import update_video_text_metadata
        from lx_dtypes.models.contracts.video_text_metadata import (
            VideoTextMetaPayload as LxVideoTextMetaPayload,
        )

        contract_payload = (
            LxVideoTextMetaPayload.model_validate(extracted_data_dict)
            if extracted_data_dict is not None
            else None
        )
        return update_video_text_metadata(
            self,
            extracted_data_dict=contract_payload,
            ocr_frame_fraction=ocr_frame_fraction,
            cap=cap,
            overwrite=overwrite,
        )

    def extract_frames(
        self,
        quality: int = 2,
        overwrite: bool = False,
        ext: str = "jpg",
        verbose: bool = False,
        from_processed: bool = False,
    ) -> bool:
        from endoreg_db.services.video_files import extract_video_frames

        return extract_video_frames(
            self,
            quality=quality,
            overwrite=overwrite,
            ext=ext,
            verbose=verbose,
            from_processed=from_processed,
        )

    def initialize_frames(self, frame_paths: list[Path] | None = None) -> None:
        from endoreg_db.services.video_files import initialize_video_frames

        return initialize_video_frames(self, frame_paths=frame_paths)

    def delete_frames(self) -> str:
        from endoreg_db.services.video_files import delete_video_frames

        return delete_video_frames(self)

    def get_frame_path(self, frame_number: int) -> Path | None:
        from endoreg_db.services.video_files import get_video_frame_path

        return get_video_frame_path(self, frame_number)

    def get_frame_paths(self) -> list[Path]:
        from endoreg_db.services.video_files import get_video_frame_paths

        return get_video_frame_paths(self)

    def get_frame_number(self) -> int:
        from endoreg_db.services.video_files import get_video_frame_number

        return get_video_frame_number(self)

    def get_frames(self) -> models.QuerySet["Frame"]:
        from endoreg_db.services.video_files import get_video_frames

        return get_video_frames(self)

    def get_frame(self, frame_number: int) -> "Frame":
        from endoreg_db.services.video_files import get_video_frame

        return get_video_frame(self, frame_number)

    def get_frame_range(
        self, start_frame_number: int, end_frame_number: int
    ) -> models.QuerySet["Frame"]:
        from endoreg_db.services.video_files import get_video_frame_range

        return get_video_frame_range(self, start_frame_number, end_frame_number)

    def get_duration(self) -> float:
        from endoreg_db.services.video_files import get_video_duration

        return get_video_duration(self)

    def create_frame_object(
        self, frame_number: int, relative_path: str, extracted: bool = False
    ) -> "Frame":
        from endoreg_db.services.video_files import create_video_frame_object

        return create_video_frame_object(
            self,
            frame_number=frame_number,
            relative_path=relative_path,
            extracted=extracted,
        )

    def bulk_create_frames(self, frames_to_create: list["Frame"]) -> None:
        from endoreg_db.services.video_files import bulk_create_video_frames

        return bulk_create_video_frames(self, frames_to_create)

    def ensure_default_fps(self) -> float:
        from endoreg_db.services.video_files import ensure_default_video_fps

        return ensure_default_video_fps(self)

    def extract_specific_frame_range(
        self,
        start_frame: int,
        end_frame: int,
        overwrite: bool = False,
        quality: int = 2,
        ext: str = "jpg",
        verbose: bool = False,
    ) -> bool:
        from endoreg_db.services.video_files import extract_video_frame_range

        return extract_video_frame_range(
            self,
            start_frame=start_frame,
            end_frame=end_frame,
            overwrite=overwrite,
            quality=quality,
            ext=ext,
            verbose=verbose,
        )

    def delete_specific_frame_range(self, start_frame: int, end_frame: int) -> None:
        from endoreg_db.services.video_files import delete_video_frame_range

        delete_video_frame_range(
            self,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    def delete_with_file(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        from endoreg_db.services.video_files import delete_video_with_owned_files

        return delete_video_with_owned_files(
            self,
            using=using,
            keep_parents=keep_parents,
        )

    def get_base_frame_dir(self) -> Path:
        from endoreg_db.services.video_files import get_video_base_frame_dir

        return get_video_base_frame_dir(self)

    def set_frame_dir(self, force_update: bool = False) -> None:
        from endoreg_db.services.video_files import set_video_frame_dir

        return set_video_frame_dir(self, force_update=force_update)

    def get_frame_dir_path(self) -> Path | None:
        from endoreg_db.services.video_files import get_video_frame_dir_path

        return get_video_frame_dir_path(self)

    def get_temp_anonymized_frame_dir(self) -> Path:
        from endoreg_db.services.video_files import get_temp_anonymized_video_frame_dir

        return get_temp_anonymized_video_frame_dir(self)

    def get_target_anonymized_video_path(self) -> Path:
        from endoreg_db.services.video_files import get_target_anonymized_video_path

        return get_target_anonymized_video_path(self)

    def get_raw_file_path(self) -> Path | None:
        from endoreg_db.services.video_files import get_raw_video_file_path

        return get_raw_video_file_path(self)

    def get_raw_stream_path(self) -> Path | None:
        from endoreg_db.services.video_files import get_raw_video_stream_path

        return get_raw_video_stream_path(self)

    def get_processed_stream_path(
        self, *, materialize_if_missing: bool = False
    ) -> Path | None:
        from endoreg_db.services.video_files import get_processed_video_stream_path

        return get_processed_video_stream_path(
            self,
            materialize_if_missing=materialize_if_missing,
        )

    def get_processed_file_path(self) -> Path | None:
        from endoreg_db.services.video_files import get_processed_video_file_path

        return get_processed_video_file_path(self)

    def anonymize(self, delete_original_raw: bool = True) -> bool:
        from endoreg_db.services.video_files import anonymize_video_file

        return anonymize_video_file(self, delete_original_raw=delete_original_raw)

    def _create_anonymized_frame_files(
        self,
        anonymized_frame_dir: Path,
        endo_roi: dict[str, int],
        frames: models.QuerySet["Frame"],
        outside_frame_numbers: set[int],
        censor_color: tuple[int, int, int] = (0, 0, 0),
    ) -> list[Path]:
        from endoreg_db.services.video_files import create_anonymized_video_frame_files

        return create_anonymized_video_frame_files(
            self,
            anonymized_frame_dir=anonymized_frame_dir,
            endo_roi=endo_roi,
            frames=frames,
            outside_frame_numbers=outside_frame_numbers,
            censor_color=censor_color,
        )

    def _cleanup_raw_assets(self, video_hash: str) -> None:
        from endoreg_db.services.video_files import cleanup_video_raw_assets

        return cleanup_video_raw_assets(video_hash)

    def predict_video(
        self,
        model_meta: "ModelMeta",
        dataset_name: str = "inference_dataset",
        smooth_window_size_s: int = 1,
        binarize_threshold: float = 0.5,
        test_run: bool = False,
        n_test_frames: int = 10,
        return_frame_scores: bool = False,
        frame_source_mode: "FrameSourceMode" = "stream",
        frame_source_file_type: str = "raw",
    ) -> "dict[str, list[tuple[int, int]]] | VideoFrameScoreResult":
        from endoreg_db.services.video_files import predict_video

        return predict_video(
            self,
            model_meta=model_meta,
            dataset_name=dataset_name,
            smooth_window_size_s=smooth_window_size_s,
            binarize_threshold=binarize_threshold,
            test_run=test_run,
            n_test_frames=n_test_frames,
            return_frame_scores=return_frame_scores,
            frame_source_mode=frame_source_mode,
            frame_source_file_type=frame_source_file_type,
        )

    def extract_text_from_frames(
        self,
        frame_fraction: float = 0.001,
        cap: int = 15,
    ) -> dict[str, str | None] | None:
        from endoreg_db.services.video_files import extract_text_from_video_frames

        return extract_text_from_video_frames(
            self,
            frame_fraction=frame_fraction,
            cap=cap,
        )

    @property
    def is_processed(self) -> bool:
        return bool(self.processed_file and self.processed_file.name)

    @property
    def has_raw(self) -> bool:
        """
        Return True if a raw video file is associated with this instance.
        """
        return bool(self.raw_file and self.raw_file.name)

    @property
    def active_file(self) -> "FieldFile":
        from endoreg_db.services.video_files import get_active_video_file

        return get_active_video_file(self)

    @property
    def active_file_path(self) -> Path:
        from endoreg_db.services.video_files import get_active_video_file_path

        return get_active_video_file_path(self)

    @property
    def active_file_url(self) -> str | None:
        from endoreg_db.services.video_files import get_active_video_file_url

        return get_active_video_file_url(self)

    @classmethod
    def create_from_file(
        cls,
        file_path: str | Path,
        center_name: str,
        **kwargs: Unpack[_VideoFileCreateKwargs],
    ) -> "VideoFile | None":
        from endoreg_db.services.video_files import create_video_file_from_path

        return create_video_file_from_path(
            file_path,
            center_name=center_name,
            model_cls=cls,
            **kwargs,
        )

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: str | Path,
        center_name: str,
        processor_name: str | None,
        video_hash: str,
        save_video_file: bool = True,
        initialize: bool = True,
    ) -> "VideoFile":
        """
        Creates a VideoFile instance from a given video file path.
        Handles transcoding (if necessary), hashing, file storage, and database record creation.
        Raises exceptions on failure.
        """
        from endoreg_db.services.video_files import (
            create_initialized_video_file_from_path,
        )

        return create_initialized_video_file_from_path(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            video_hash=video_hash,
            save_video_file=save_video_file,
            initialize=initialize,
            model_cls=cls,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        return self.delete_with_file(using=using, keep_parents=keep_parents)

    def validate_metadata_annotation(
        self, extracted_data_dict: VideoTextMetaPayload | None = None
    ) -> bool:
        from endoreg_db.services.video_files import validate_video_metadata_annotation

        payload: VideoTextMetaPayload | None = (
            extracted_data_dict.model_dump()
            if extracted_data_dict is not None
            else None
        )
        return validate_video_metadata_annotation(self, payload)

    def initialize(self) -> "VideoFile":
        from endoreg_db.services.video_files import initialize_video_file

        return initialize_video_file(self)

    def __str__(self) -> str:
        """
        Return a human-readable string summarizing the video's state, active file name, and UUID.
        """
        try:
            active_file = self.active_file
            active_name = getattr(active_file, "name", None)
        except ValueError:
            active_name = None
        file_name = Path(active_name).name if active_name else "No file"
        state = (
            "Processed" if self.is_processed else ("Raw" if self.has_raw else "No File")
        )
        return f"VideoFile ({state}): {file_name} (UUID: {self.video_hash})"

    # --- Convenience state/meta helpers used in tests and admin workflows ---
    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "VideoFile":
        """
        Mark this video's processing state as having its sensitive meta fully processed.
        This proxies to the related VideoState and persists by default.
        """
        sm = self.sensitive_meta
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()
        state = self.get_or_create_state()
        state.mark_sensitive_meta_processed(save=save)
        return self

    def mark_sensitive_meta_verified(self) -> "VideoFile":
        """
        Mark the associated SensitiveMeta as verified by setting both DOB and names as verified.
        Ensures the SensitiveMeta and its state exist.
        """
        sm = self.sensitive_meta
        # Use SensitiveMeta methods to update underlying SensitiveMetaState
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()

        sm.mark_dob_verified()
        sm.mark_names_verified()
        return self

    def clean(self) -> None:
        super().clean()
        try:
            validated_sequences = validate_video_segments_payload(self.sequences or {})
        except ValueError as exc:
            raise ValidationError({"sequences": str(exc)}) from exc
        self.sequences = validated_sequences.model_dump(mode="json")
        try:
            validated_meta = validate_video_file_meta_payload(self.meta)
        except ValueError as exc:
            raise ValidationError({"meta": str(exc)}) from exc
        self.meta = cast(VideoFileMetaJsonObject | None, validated_meta)

    def save(self, *args: object, **kwargs: object) -> None:
        # Ensure state exists or is created before the main save operation
        # Now call the original save method
        """
        Saves the VideoFile instance to the database.

        Overrides the default save method to persist changes to the VideoFile model.
        """
        previous_processed_name = None
        if self.pk:
            previous_processed_name = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("processed_file", flat=True)
                .first()
            )
        current_processed_name = getattr(self.processed_file, "name", None) or ""
        self.clean()
        super().save(*args, **kwargs)
        if self.pk and previous_processed_name is not None:
            if str(previous_processed_name or "") != str(current_processed_name):
                self.get_or_create_state().clear_export_readiness(
                    clear_outside_segments_removed=True
                )

    def get_or_create_state(self) -> "VideoState":
        from endoreg_db.services.video_files import get_or_create_video_state

        return get_or_create_video_state(self)

    def get_outside_segments(
        self, only_validated: bool = False
    ) -> models.QuerySet["LabelVideoSegment"]:
        """
        Return all video segments labeled as "outside" for this video.

        Parameters:
            only_validated (bool): If True, only segments with a validated state are included.

        Returns:
            QuerySet: A queryset of LabelVideoSegment instances labeled as "outside". Returns an empty queryset if the label does not exist or an error occurs.
        """
        from endoreg_db.services.video_files import get_video_outside_segments

        return get_video_outside_segments(self, only_validated=only_validated)

    @classmethod
    def create_video_without_outside_frames(
        cls,
        instance: "VideoFile",
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        """
        Creates a new video by excluding frames that belong to 'outside' segments.

        Parameters:
            only_validated (bool): If True, only validated segments are considered for frame exclusion.
            outside_intervals: Precomputed half-open frame ranges to blacken.

        Returns:
            VideoFile: A new VideoFile instance with the frames excluding those labeled as 'outside'.
        """
        from endoreg_db.services.video_files import (
            rebuild_processed_video_without_outside_frames,
        )

        return rebuild_processed_video_without_outside_frames(
            instance,
            only_validated=only_validated,
            outside_intervals=outside_intervals,
        )

    def frame_number_to_s(self, frame_number: int) -> float:
        from endoreg_db.services.video_files import video_frame_number_to_seconds

        return video_frame_number_to_seconds(self, frame_number)

    def get_raw_stream_relative_path(self) -> str | None:
        from endoreg_db.services.video_files import get_raw_video_stream_relative_path

        return get_raw_video_stream_relative_path(self)

    def get_processed_stream_relative_path(self) -> str | None:
        from endoreg_db.services.video_files import (
            get_processed_video_stream_relative_path,
        )

        return get_processed_video_stream_relative_path(self)

    def get_stream_relative_path(self, file_type: str) -> str | None:
        from endoreg_db.services.video_files import (
            get_video_stream_relative_path,
            parse_video_artifact_kind,
        )

        return get_video_stream_relative_path(
            self,
            parse_video_artifact_kind(file_type),
        )

    def resolve_video_stream_source(
        self,
        file_type: str,
        *,
        materialize_if_missing: bool = False,
    ) -> tuple["FieldFile", Path | None]:
        from endoreg_db.services.video_files import (
            parse_video_artifact_kind,
            resolve_video_stream_source,
        )

        return resolve_video_stream_source(
            self,
            parse_video_artifact_kind(file_type),
            materialize_if_missing=materialize_if_missing,
        )

    def can_offload_stream_with_nginx(self, file_type: str) -> bool:
        from endoreg_db.services.video_files import (
            can_offload_video_stream,
            parse_video_artifact_kind,
        )

        return can_offload_video_stream(self, parse_video_artifact_kind(file_type))

    @staticmethod
    def _is_encrypted_streamable_path(path: Path | None) -> bool:
        from endoreg_db.services.video_files import is_encrypted_streamable_video_path

        return is_encrypted_streamable_video_path(path)
