import hashlib
import json
import logging
import os
import uuid
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

logger = logging.getLogger(__name__)
PHI_REGION_LABEL_NAME = "phi_region"
PHI_REGION_INFORMATION_SOURCE_NAME = "lx_anonymizer_phi_detector"
PHI_REGION_ANNOTATOR = "system:lx_anonymizer"
ENDOSCOPE_IMAGE_ROI_REQUIRED_KEYS = ("x", "y", "width", "height")
ENDOSCOPE_IMAGE_ROI_POSITIVE_KEYS = (
    "width",
    "height",
    "image_width",
    "image_height",
)
ENDOSCOPE_IMAGE_ROI_NON_NEGATIVE_KEYS = ("x", "y")


def _temp_media_path(final_path: Path, marker: str = "part") -> Path:
    """Keep the media suffix last so FFmpeg can infer the container."""
    return final_path.with_name(f"{final_path.stem}.{marker}{final_path.suffix}")


from lx_anonymizer.frame_cleaner import FrameCleaner
from lx_dtypes.models import SensitiveMeta
from lx_dtypes.models.contracts.media_streaming import (
    FfmpegStreamProbeEntry,
    validate_ffmpeg_stream_info,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.video_frame_box_annotations import (
    VideoPhiFrameObservationPayload,
    VideoPhiRegionPayload,
    validate_video_phi_frame_observations,
)

from endoreg_db.import_files.context.import_context import (
    AnonymizerSourceSnapshot,
    ImportContext,
)
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.label.label import Label
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.hardware.endoscopy_processor import (
    EndoscopyProcessor,
)
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.filesystem.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.video.ffmpeg_wrapper import (
    _resolve_ffmpeg_executable,
    _resolve_ffprobe_executable,
    get_stream_info,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


class _VideoAnonymizationVideo(Protocol):
    video_hash: str
    meta: JsonObject | None

    def ensure_local_raw_file(self) -> AbstractContextManager[Path]: ...


class _FrameCleaner(Protocol):
    def clean_video(
        self,
        *,
        video_path: Path,
        endoscope_image_roi: dict[str, int],
        endoscope_data_roi_nested: dict[str, dict[str, int | None]],
        output_path: Path,
    ) -> tuple[Path, JsonObject | None]: ...


class _InformationSourceManager(Protocol):
    def get_or_create_by_name(
        self,
        name: str,
        **defaults: str,
    ) -> tuple[InformationSource, bool]: ...


class _EndoscopyProcessor(Protocol):
    def get_roi_endoscope_image(self) -> dict[str, int | None] | None: ...

    def get_sensitive_rois(self) -> dict[str, dict[str, int | None] | None]: ...


class _EndoscopyProcessorClass(Protocol):
    class DoesNotExist(Exception): ...

    def get_by_name(self, name: str) -> _EndoscopyProcessor: ...


def _processed_video_dir() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().transcoding
        / "anonymized_videos"
    )


def _quarantine_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().quarantine


def _ensure_ffmpeg_tools_on_path() -> None:
    """
    lx_anonymizer shells out to "ffmpeg" and "ffprobe" by executable name.

    endoreg_db supports Nix-style deployments where FFmpeg is discoverable via
    explicit resolver logic but not necessarily present on PATH. Prepending the
    resolved tool directory keeps both integrations using the same binaries.
    """
    ffmpeg_path = _resolve_ffmpeg_executable()
    ffprobe_path = _resolve_ffprobe_executable()
    if not ffmpeg_path or not ffprobe_path:
        raise RuntimeError(
            "FFmpeg and ffprobe are required for video anonymization but could not be resolved."
        )

    ffmpeg_dir = Path(ffmpeg_path).parent
    ffprobe_dir = Path(ffprobe_path).parent
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    prepend_dirs: list[str] = []
    for path in (ffmpeg_dir, ffprobe_dir):
        path_value = path.as_posix()
        if path_value not in path_parts and path_value not in prepend_dirs:
            prepend_dirs.append(path_value)
    if prepend_dirs:
        os.environ["PATH"] = os.pathsep.join(
            [*prepend_dirs, os.environ.get("PATH", "")]
        )
        logger.info(
            "Prepended FFmpeg tool directories to PATH for lx_anonymizer: %s",
            prepend_dirs,
        )


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_endoscope_image_roi(
    roi: dict[str, int | None] | None,
    *,
    processor_name: str,
) -> dict[str, int]:
    if roi is None:
        raise RuntimeError(
            f"Endoscopy processor {processor_name!r} has no endoscope image ROI configured."
        )

    missing_keys = [key for key in ENDOSCOPE_IMAGE_ROI_REQUIRED_KEYS if key not in roi]
    invalid_value_keys: list[str] = []
    complete_roi: dict[str, int] = {}
    for key, value in roi.items():
        if isinstance(value, bool) or not isinstance(value, int):
            invalid_value_keys.append(key)
            continue
        complete_roi[key] = value

    invalid_positive_keys = [
        key
        for key in ENDOSCOPE_IMAGE_ROI_POSITIVE_KEYS
        if key in complete_roi and complete_roi[key] <= 0
    ]
    invalid_non_negative_keys = [
        key
        for key in ENDOSCOPE_IMAGE_ROI_NON_NEGATIVE_KEYS
        if key in complete_roi and complete_roi[key] < 0
    ]

    if (
        missing_keys
        or invalid_value_keys
        or invalid_positive_keys
        or invalid_non_negative_keys
    ):
        details: list[str] = []
        if missing_keys:
            details.append(f"missing keys: {', '.join(missing_keys)}")
        if invalid_value_keys:
            details.append(
                f"non-integer or null values: {', '.join(invalid_value_keys)}"
            )
        if invalid_positive_keys:
            details.append(
                f"non-positive dimensions: {', '.join(invalid_positive_keys)}"
            )
        if invalid_non_negative_keys:
            details.append(
                f"negative coordinates: {', '.join(invalid_non_negative_keys)}"
            )
        raise RuntimeError(
            f"Endoscopy processor {processor_name!r} has invalid endoscope image ROI "
            f"({'; '.join(details)})."
        )

    return complete_roi


def _first_video_stream(
    stream_info: JsonObject | None,
) -> FfmpegStreamProbeEntry | None:
    if stream_info is None:
        return None
    probe_info = validate_ffmpeg_stream_info(stream_info)
    video_streams = probe_info.video_streams
    return video_streams[0] if video_streams else None


def _critical_source_mismatch(
    *,
    video_hash: str,
    source_path: Path,
    reason: str,
    expected: object,
    actual: object,
) -> None:
    quarantine_path = _quarantine_anonymizer_source(
        source_path=source_path,
        video_hash=video_hash,
        reason=reason,
    )
    logger.critical(
        json.dumps(
            {
                "event": "video.anonymizer_source_integrity_mismatch",
                "video_hash": video_hash,
                "path": str(source_path),
                "quarantined_path": str(quarantine_path),
                "reason": reason,
                "expected": expected,
                "actual": actual,
            },
            sort_keys=True,
        )
    )


def _quarantine_anonymizer_source(
    *,
    source_path: Path,
    video_hash: str,
    reason: str,
) -> Path:
    quarantine_dir = ensure_directory(_quarantine_dir())
    safe_reason = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_" for char in reason
    )
    destination = quarantine_dir / (
        f"{video_hash}.anonymizer-input.{safe_reason}.{uuid.uuid4().hex}"
        f"{source_path.suffix or '.bin'}"
    )
    return atomic_copy_file(source=source_path, destination=destination)


def _log_anonymizer_source_verified(
    *, video_hash: str, snapshot: AnonymizerSourceSnapshot
) -> None:
    logger.info(
        json.dumps(
            {
                "event": "video.anonymizer_source_verified",
                "video_hash": video_hash,
                **snapshot,
            },
            sort_keys=True,
        )
    )


def _verify_anonymizer_source(
    ctx: ImportContext,
    source_path: Path,
    *,
    video_hash: str,
) -> AnonymizerSourceSnapshot:
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Video anonymization source not found: {source_path}")
    if not source_path.is_file():
        raise RuntimeError(
            f"Video anonymization source is not a regular file: {source_path}"
        )

    stat_result = source_path.stat()
    if stat_result.st_size <= 0:
        raise RuntimeError(f"Video anonymization source is empty: {source_path}")

    expected_path = getattr(ctx, "validated_raw_source_path", None)
    if expected_path is not None:
        expected_path = Path(expected_path).resolve()
        if expected_path != source_path:
            _critical_source_mismatch(
                video_hash=video_hash,
                source_path=source_path,
                reason="path",
                expected=str(expected_path),
                actual=str(source_path),
            )
            raise RuntimeError(
                "Anonymizer source path differs from validated raw materialization."
            )

    expected_size = getattr(ctx, "validated_raw_source_size_bytes", None)
    if expected_size is not None and int(expected_size) != int(stat_result.st_size):
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="size_bytes",
            expected=int(expected_size),
            actual=int(stat_result.st_size),
        )
        raise RuntimeError(
            "Anonymizer source size differs from validated raw materialization."
        )

    expected_mtime_ns = getattr(ctx, "validated_raw_source_mtime_ns", None)
    if expected_mtime_ns is not None and int(expected_mtime_ns) != int(
        stat_result.st_mtime_ns
    ):
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="mtime_ns",
            expected=int(expected_mtime_ns),
            actual=int(stat_result.st_mtime_ns),
        )
        raise RuntimeError(
            "Anonymizer source timestamp differs from validated raw materialization."
        )

    expected_sha256 = getattr(ctx, "validated_raw_source_sha256", None)
    source_sha256 = sha256_file(source_path)
    if expected_sha256 and str(expected_sha256) != source_sha256:
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="sha256",
            expected=str(expected_sha256),
            actual=source_sha256,
        )
        raise RuntimeError(
            "Anonymizer source hash differs from validated raw materialization."
        )

    stream_info = get_stream_info(source_path)
    video_stream = _first_video_stream(stream_info)
    if video_stream is None:
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="video_stream",
            expected="readable video stream",
            actual="missing",
        )
        raise RuntimeError(
            f"Anonymizer source has no readable video stream: {source_path}"
        )

    width = video_stream.width
    height = video_stream.height
    if width is None or height is None:
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="dimensions",
            expected="positive width and height",
            actual={
                "width": width,
                "height": height,
            },
        )
        raise RuntimeError(
            f"Anonymizer source has unreadable structural dimensions: {source_path}"
        )

    raw_expected_stream = getattr(ctx, "validated_raw_source_stream", None)
    expected_stream: JsonObject = (
        cast(JsonObject, raw_expected_stream)
        if isinstance(raw_expected_stream, dict)
        else {}
    )
    expected_width = _positive_int(expected_stream.get("width"))
    expected_height = _positive_int(expected_stream.get("height"))
    if expected_width is not None and width != expected_width:
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="width",
            expected=expected_width,
            actual=width,
        )
        raise RuntimeError("Anonymizer source width differs from VideoMeta validation.")
    if expected_height is not None and height != expected_height:
        _critical_source_mismatch(
            video_hash=video_hash,
            source_path=source_path,
            reason="height",
            expected=expected_height,
            actual=height,
        )
        raise RuntimeError(
            "Anonymizer source height differs from VideoMeta validation."
        )

    snapshot: AnonymizerSourceSnapshot = {
        "path": str(source_path),
        "size_bytes": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
        "sha256": str(source_sha256),
        "width": width,
        "height": height,
        "codec_name": video_stream.codec_name or None,
    }
    _log_anonymizer_source_verified(video_hash=video_hash, snapshot=snapshot)
    ctx.anonymizer_source_snapshot = snapshot
    return snapshot


class VideoAnonymizer:
    def __init__(self):
        _ensure_ffmpeg_tools_on_path()
        self._ensure_frame_cleaning_available()
        self._frame_cleaning_available = None
        self._frame_cleaning_class = None

    def anonymize_video(self, ctx: ImportContext):
        _ensure_ffmpeg_tools_on_path()
        # Setup anonymized directory
        anonymized_dir = ensure_directory(_processed_video_dir())
        assert ctx.current_video is not None
        video = cast(_VideoAnonymizationVideo, ctx.current_video)
        state = get_or_create_video_state(ctx.current_video)
        meta = video.meta or {}
        if getattr(state, "processing_error", False) or (
            meta.get("integrity_status") == "lost"
        ):
            raise RuntimeError(
                f"Video {video.video_hash} is marked failed/lost and cannot be anonymized."
            )
        # Generate output path for anonymized report

        video_hash = video.video_hash
        anonymized_output_path = anonymized_dir / f"{video_hash}.mp4"
        temp_output_path = _temp_media_path(anonymized_output_path)
        safe_unlink_file(temp_output_path, missing_ok=True)

        self._frame_cleaning_class = cast(_FrameCleaner, FrameCleaner())

        endoscope_roi, endoscope_roi_nested = self._get_processor_roi_info(ctx)
        explicit_source_path = getattr(ctx, "local_source_path", None)
        if explicit_source_path is not None:
            source_context = nullcontext(Path(explicit_source_path))
        else:
            source_context = video.ensure_local_raw_file()

        # Process with enhanced process_report method (returns 4-tuple now)
        with source_context as source_path:
            verified_source = Path(source_path).resolve()
            _verify_anonymizer_source(ctx, verified_source, video_hash=video_hash)
            ctx.anonymized_path, extracted_metadata = (
                self._frame_cleaning_class.clean_video(
                    video_path=verified_source,
                    endoscope_image_roi=endoscope_roi,
                    endoscope_data_roi_nested=endoscope_roi_nested,
                    output_path=temp_output_path,
                )
            )
        if extracted_metadata is None:
            extracted_metadata = {}
        temp_result_path = ctx.anonymized_path
        if not temp_result_path.exists():
            raise RuntimeError(
                f"Video anonymization output does not exist: {temp_result_path}"
            )
        if temp_result_path.stat().st_size <= 0:
            raise RuntimeError(
                f"Video anonymization output is empty: {temp_result_path}"
            )

        atomic_move_file(source=temp_result_path, destination=anonymized_output_path)
        ctx.anonymized_path = anonymized_output_path

        lx_sensitive_payload = {
            key: value
            for key, value in extracted_metadata.items()
            if key in SensitiveMeta.model_fields
        }
        sensitive_meta_storage(
            SensitiveMeta.model_validate(lx_sensitive_payload), ctx.current_video
        )
        self._persist_phi_region_proposals(ctx.current_video, extracted_metadata)
        return ctx

    def _persist_phi_region_proposals(
        self,
        video: VideoFile,
        extracted_metadata: JsonObject,
    ) -> int:
        try:
            observations = validate_video_phi_frame_observations(
                extracted_metadata.get("frame_observations")
            )
            if not observations:
                return 0
            return self._persist_phi_region_proposals_unchecked(video, observations)
        except Exception as exc:
            logger.warning(
                "Failed to persist lx-anonymizer PHI region proposals for video %s: %s",
                getattr(video, "video_hash", None),
                exc,
                exc_info=True,
            )
            return 0

    def _persist_phi_region_proposals_unchecked(
        self,
        video: VideoFile,
        observations: list[VideoPhiFrameObservationPayload],
    ) -> int:
        label = Label.get_or_create_from_name(PHI_REGION_LABEL_NAME)[0]
        information_source_manager = cast(
            _InformationSourceManager,
            InformationSource.objects,
        )
        information_source = information_source_manager.get_or_create_by_name(
            PHI_REGION_INFORMATION_SOURCE_NAME,
            description="PHI region proposals generated by lx-anonymizer.",
        )[0]
        video_hash = str(getattr(video, "video_hash", "") or "")
        persisted_count = 0

        for observation in observations:
            frame_number = observation.resolved_frame_number
            if frame_number is None:
                continue
            frame = (
                Frame.objects.filter(video=video, frame_number=frame_number)
                .order_by("pk")
                .first()
            )
            if frame is None:
                logger.debug(
                    "Skipping PHI proposals for video=%s frame=%s because no Frame row exists.",
                    video_hash,
                    frame_number,
                )
                continue

            image_width = observation.image_width
            image_height = observation.image_height

            for region in observation.phi_regions:
                box = self._normalize_phi_region_box(region, image_width, image_height)
                if box is None:
                    continue
                x, y, width, height = box
                source = region.source
                external_annotation_id = self._phi_external_annotation_id(
                    video_hash=video_hash,
                    frame_number=frame_number,
                    source=source,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )
                confidence = self._region_confidence(region)
                annotation, _ = FrameBoxAnnotation.objects.get_or_create(
                    frame=frame,
                    information_source=information_source,
                    annotator=PHI_REGION_ANNOTATOR,
                    external_annotation_id=external_annotation_id,
                    defaults={
                        "label": label,
                        "value": True,
                        "float_value": confidence,
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "image_width": image_width,
                        "image_height": image_height,
                    },
                )
                updates: dict[str, Label | bool | float | int | None] = {
                    "label": label,
                    "value": True,
                    "float_value": confidence,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "image_width": image_width,
                    "image_height": image_height,
                }
                changed = False
                for field, value in updates.items():
                    if getattr(annotation, field) != value:
                        setattr(annotation, field, value)
                        changed = True
                if changed:
                    annotation.save()
                persisted_count += 1

        if persisted_count:
            logger.info(
                "Persisted %d lx-anonymizer PHI region proposals for video %s.",
                persisted_count,
                video_hash,
            )
        return persisted_count

    @staticmethod
    def _region_confidence(region: VideoPhiRegionPayload) -> float | None:
        return region.confidence

    @staticmethod
    def _normalize_phi_region_box(
        region: VideoPhiRegionPayload,
        image_width: int,
        image_height: int,
    ) -> tuple[float, float, float, float] | None:
        x = region.x
        y = region.y
        width = region.width
        height = region.height
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            return None
        if x >= image_width or y >= image_height:
            return None
        width = min(width, image_width - x)
        height = min(height, image_height - y)
        if width <= 0 or height <= 0:
            return None
        return x, y, width, height

    @staticmethod
    def _phi_external_annotation_id(
        *,
        video_hash: str,
        frame_number: int,
        source: str,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> str:
        box_payload = f"{x:.3f}:{y:.3f}:{width:.3f}:{height:.3f}"
        box_hash = hashlib.sha256(box_payload.encode("utf-8")).hexdigest()[:16]
        return f"{video_hash}:{frame_number}:{source}:{box_hash}"

    def _ensure_frame_cleaning_available(self):
        """
        Ensure frame cleaning modules are available by adding lx-anonymizer to path.

        Returns:
            Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
        """
        try:
            from lx_anonymizer import FrameCleaner
        except Exception as e:
            logger.warning(
                f"Frame cleaning not available: {e} Please install or update lx_anonymizer."
            )
            raise

        assert FrameCleaner is not None
        self._frame_cleaning_class = FrameCleaner()
        self._frame_cleaning_available = True

    def _get_processor_roi_info(
        self,
        ctx: ImportContext,
    ) -> tuple[dict[str, int], dict[str, dict[str, int | None]]]:
        """Get processor ROI information for masking and data extraction."""
        video = ctx.current_video
        assert isinstance(video, VideoFile)
        video_hash = str(cast(_VideoAnonymizationVideo, video).video_hash)

        processor_name = str(getattr(ctx, "processor_name", "") or "").strip()
        if not processor_name:
            raise RuntimeError(
                f"Video {video_hash} requires a processor_name for anonymization ROI masking."
            )

        processor_class = cast(_EndoscopyProcessorClass, EndoscopyProcessor)
        try:
            processor = processor_class.get_by_name(processor_name)
        except processor_class.DoesNotExist as exc:
            raise RuntimeError(
                f"Endoscopy processor {processor_name!r} not found for video {video_hash}."
            ) from exc

        endoscope_image_roi = _require_endoscope_image_roi(
            processor.get_roi_endoscope_image(),
            processor_name=processor_name,
        )
        endoscope_data_roi_nested = {
            name: roi
            for name, roi in processor.get_sensitive_rois().items()
            if roi is not None
        }
        logger.info(
            "Retrieved processor ROI information: endoscope_image_roi=%s",
            endoscope_image_roi,
        )

        # IMPORTANT: return order must match clean_video signature
        return endoscope_image_roi, endoscope_data_roi_nested
