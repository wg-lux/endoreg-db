import hashlib
import json
import logging
import math
import os
import uuid
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from pydantic import ValidationError
from endoreg_db.utils.ffmpeg_wrapper import (
    get_stream_info,
    resolve_ffmpeg_executable as _resolve_ffmpeg_executable,
    resolve_ffprobe_executable as _resolve_ffprobe_executable,
)
from lx_anonymizer.frame_cleaner import FrameCleaner  # pyright: ignore[reportMissingTypeStubs]
from lx_dtypes.models import SensitiveMeta
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue
from lx_dtypes.models.contracts.media_streaming import (
    FfmpegStreamProbeEntry,
    validate_ffmpeg_stream_info,
)
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject
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
from endoreg_db.services.video_files import (
    ensure_local_raw_video_file,
    get_or_create_video_state,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)

logger = logging.getLogger(__name__)
PHI_REGION_LABEL_NAME = "phi_region"
PHI_REGION_INFORMATION_SOURCE_NAME = "lx_anonymizer_phi_detector"
PHI_REGION_ANNOTATOR = "system:lx_anonymizer"
PAPER_EVALUATION_METRICS_KEY = "paper_evaluation_metrics"
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


if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


class _VideoAnonymizationVideo(Protocol):
    video_hash: str
    meta: JsonObject | None


class _FrameCleaner(Protocol):
    def clean_video(
        self,
        *,
        video_path: Path,
        endoscope_image_roi: dict[str, int],
        endoscope_data_roi_nested: dict[str, dict[str, int | None]],
        source_frame_rate: Fraction,
        output_path: Path,
    ) -> tuple[Path, JsonObject | None]: ...


@runtime_checkable
class _PydanticDumpable(Protocol):
    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class _InformationSourceManager(Protocol):
    def get_or_create_by_name(
        self,
        name: str,
        **defaults: str,
    ) -> tuple[InformationSource, bool]: ...


class _EndoscopyProcessor(Protocol):
    def get_roi_endoscope_image(self) -> object: ...

    def get_sensitive_rois(self) -> dict[str, object]: ...


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


def _json_compatible_value(value: object, *, field_name: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} does not allow NaN or infinite floats")
        return value
    if isinstance(value, list):
        values = cast(list[object], value)
        return [_json_compatible_value(item, field_name=field_name) for item in values]
    if isinstance(value, dict):
        return _json_compatible_mapping(
            cast(dict[object, object], value),
            field_name=field_name,
        )
    raise ValueError(
        f"{field_name} contains unsupported JSON value type: {type(value).__name__}"
    )


def _json_compatible_mapping(
    value: dict[object, object],
    *,
    field_name: str,
) -> JsonObject:
    payload: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        payload[key] = _json_compatible_value(item, field_name=field_name)
    return payload


def _paper_evaluation_metrics_payload(
    extracted_metadata: JsonObject,
) -> JsonObject | None:
    raw_metrics = extracted_metadata.get(PAPER_EVALUATION_METRICS_KEY)
    if raw_metrics is None:
        return None
    if not isinstance(raw_metrics, dict):
        raise ValueError(f"{PAPER_EVALUATION_METRICS_KEY} must be a JSON object")
    return _json_compatible_mapping(
        cast(dict[object, object], raw_metrics),
        field_name=PAPER_EVALUATION_METRICS_KEY,
    )


def _require_endoscope_image_roi(
    roi: object,
    *,
    processor_name: str,
) -> dict[str, int]:
    if roi is None:
        raise RuntimeError(
            f"Endoscopy processor {processor_name!r} has no endoscope image ROI configured."
        )
    if isinstance(roi, _PydanticDumpable):
        roi = roi.model_dump(mode="python")
    if not isinstance(roi, dict):
        raise RuntimeError(
            f"Endoscopy processor {processor_name!r} has invalid endoscope image ROI type."
        )
    roi_data = cast(dict[str, object], roi)

    missing_keys = [
        key for key in ENDOSCOPE_IMAGE_ROI_REQUIRED_KEYS if key not in roi_data
    ]
    invalid_value_keys: list[str] = []
    complete_roi: dict[str, int] = {}
    for key, value in roi_data.items():
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


def _scale_coordinate(value: int, *, source_size: int, target_size: int) -> int:
    return round((value * target_size) / source_size)


def _scale_length(value: int, *, source_size: int, target_size: int) -> int:
    return max(1, round((value * target_size) / source_size))


def _scale_roi_box(
    roi: dict[str, int],
    *,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> dict[str, int]:
    x = _scale_coordinate(
        roi["x"],
        source_size=source_width,
        target_size=target_width,
    )
    y = _scale_coordinate(
        roi["y"],
        source_size=source_height,
        target_size=target_height,
    )
    width = _scale_length(
        roi["width"],
        source_size=source_width,
        target_size=target_width,
    )
    height = _scale_length(
        roi["height"],
        source_size=source_height,
        target_size=target_height,
    )

    if x >= target_width or y >= target_height:
        raise RuntimeError(
            "Scaled endoscope ROI starts outside anonymizer source dimensions."
        )
    return {
        **roi,
        "x": x,
        "y": y,
        "width": min(width, target_width - x),
        "height": min(height, target_height - y),
    }


def _normalize_roi_to_source_dimensions(
    *,
    endoscope_roi: dict[str, int],
    sensitive_rois: dict[str, dict[str, int | None]],
    source_width: int,
    source_height: int,
) -> tuple[dict[str, int], dict[str, dict[str, int | None]]]:
    roi_width = _positive_int(endoscope_roi.get("image_width"))
    roi_height = _positive_int(endoscope_roi.get("image_height"))
    if roi_width is None or roi_height is None:
        normalized_endoscope_roi = {
            **endoscope_roi,
            "image_width": source_width,
            "image_height": source_height,
        }
        return normalized_endoscope_roi, sensitive_rois
    if roi_width == source_width and roi_height == source_height:
        return endoscope_roi, sensitive_rois

    normalized_endoscope_roi = _scale_roi_box(
        endoscope_roi,
        source_width=roi_width,
        source_height=roi_height,
        target_width=source_width,
        target_height=source_height,
    )
    normalized_endoscope_roi["image_width"] = source_width
    normalized_endoscope_roi["image_height"] = source_height

    normalized_sensitive_rois: dict[str, dict[str, int | None]] = {}
    for name, roi in sensitive_rois.items():
        normalized_roi: dict[str, int | None] = roi.copy()
        if (
            isinstance(roi.get("x"), int)
            and isinstance(roi.get("y"), int)
            and isinstance(roi.get("width"), int)
            and isinstance(roi.get("height"), int)
        ):
            scaled_roi = _scale_roi_box(
                cast(dict[str, int], roi),
                source_width=roi_width,
                source_height=roi_height,
                target_width=source_width,
                target_height=source_height,
            )
            normalized_roi.update(scaled_roi)
        normalized_sensitive_rois[name] = normalized_roi

    logger.info(
        "Scaled processor ROI from configured dimensions %sx%s to anonymizer source %sx%s.",
        roi_width,
        roi_height,
        source_width,
        source_height,
    )
    return normalized_endoscope_roi, normalized_sensitive_rois


def _require_sensitive_roi_box(
    name: str,
    roi: object,
    *,
    processor_name: str,
) -> dict[str, int | None]:
    if isinstance(roi, _PydanticDumpable):
        roi = roi.model_dump(mode="python")
    if not isinstance(roi, dict):
        raise RuntimeError(
            f"Endoscopy processor {processor_name!r} has invalid sensitive ROI {name!r} type."
        )
    roi_data = cast(dict[str, object], roi)
    normalized: dict[str, int | None] = {}
    for key, value in roi_data.items():
        if value is None:
            normalized[key] = None
        elif isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError(
                f"Endoscopy processor {processor_name!r} has non-integer value for sensitive ROI {name!r} key {key!r}."
            )
        else:
            normalized[key] = value
    return normalized


def _first_video_stream(
    stream_info: JsonObject | None,
) -> FfmpegStreamProbeEntry | None:
    if stream_info is None:
        return None
    probe_info = validate_ffmpeg_stream_info(stream_info)
    video_streams = probe_info.video_streams
    return video_streams[0] if video_streams else None


def _source_frame_rate(stream: FfmpegStreamProbeEntry) -> Fraction:
    for field_name, value in (
        ("avg_frame_rate", stream.avg_frame_rate),
        ("r_frame_rate", stream.r_frame_rate),
    ):
        if value is None:
            continue
        try:
            frame_rate = Fraction(value)
        except ZeroDivisionError:
            continue
        except ValueError as exc:
            raise RuntimeError(
                f"Anonymizer source has invalid {field_name}: {value!r}"
            ) from exc
        if frame_rate > 0:
            return frame_rate
    raise RuntimeError(
        "Anonymizer source has no positive rational average or nominal frame rate."
    )


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
    source_frame_rate = _source_frame_rate(video_stream)

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
        "fps_num": source_frame_rate.numerator,
        "fps_den": source_frame_rate.denominator,
        "codec_name": video_stream.codec_name or None,
    }
    _log_anonymizer_source_verified(video_hash=video_hash, snapshot=snapshot)
    ctx.anonymizer_source_snapshot = snapshot
    return snapshot


class VideoAnonymizer:
    def __init__(self):
        _ensure_ffmpeg_tools_on_path()
        self._frame_cleaning_available: bool = False
        self._ensure_frame_cleaning_available()

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
        temp_output_path = _temp_media_path(
            anonymized_output_path,
            marker=f"attempt-{ctx.attempt_id}",
        )
        safe_unlink_file(temp_output_path, missing_ok=True)
        ensure_directory(temp_output_path.parent)

        if not self._frame_cleaning_available:
            self._ensure_frame_cleaning_available()
        if not self._frame_cleaning_available:
            raise RuntimeError("Frame cleaning is unavailable.")
        # FrameCleaner owns mutable frame, metadata, OCR, and LLM run state.
        # A fresh instance is mandatory for every attempt.
        frame_cleaner = cast(_FrameCleaner, FrameCleaner())

        endoscope_roi, endoscope_roi_nested = self._get_processor_roi_info(ctx)
        explicit_source_path = getattr(ctx, "local_source_path", None)
        if explicit_source_path is None:
            explicit_source_path = getattr(ctx, "file_path", None)
        if explicit_source_path is not None:
            source_context = nullcontext(Path(explicit_source_path))
        else:
            source_context = ensure_local_raw_video_file(ctx.current_video)

        # Process with enhanced process_report method (returns 4-tuple now)
        with source_context as source_path:
            verified_source = Path(source_path).resolve()
            source_snapshot = _verify_anonymizer_source(
                ctx, verified_source, video_hash=video_hash
            )
            source_width = _positive_int(source_snapshot.get("width"))
            source_height = _positive_int(source_snapshot.get("height"))
            if source_width is None or source_height is None:
                raise RuntimeError(
                    "Anonymizer source dimensions are unavailable after verification."
                )
            fps_num = _positive_int(source_snapshot.get("fps_num"))
            fps_den = _positive_int(source_snapshot.get("fps_den"))
            if fps_num is None or fps_den is None:
                raise RuntimeError(
                    "Anonymizer source rational frame rate is unavailable after verification."
                )
            source_frame_rate = Fraction(fps_num, fps_den)
            endoscope_roi, endoscope_roi_nested = _normalize_roi_to_source_dimensions(
                endoscope_roi=endoscope_roi,
                sensitive_rois=endoscope_roi_nested,
                source_width=source_width,
                source_height=source_height,
            )
            ctx.anonymized_path, extracted_metadata = frame_cleaner.clean_video(
                video_path=verified_source,
                endoscope_image_roi=endoscope_roi,
                endoscope_data_roi_nested=endoscope_roi_nested,
                source_frame_rate=source_frame_rate,
                output_path=temp_output_path,
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

        try:
            candidate_is_canonical = (
                temp_result_path.resolve() == anonymized_output_path.resolve()
            )
        except FileNotFoundError:
            candidate_is_canonical = False
        if candidate_is_canonical:
            raise RuntimeError(
                "Video anonymization must remain in attempt-local staging until "
                "validation and fenced publication succeed."
            )
        ctx.anonymized_path = temp_result_path
        logger.info(
            "Retained anonymized video in attempt-local staging pending validation: "
            "video=%s attempt=%s path=%s",
            video_hash,
            ctx.attempt_id,
            temp_result_path,
        )

        if ctx.execution_guard is not None:
            ctx.execution_guard()

        lx_sensitive_payload = {
            key: value
            for key, value in extracted_metadata.items()
            if key in SensitiveMeta.model_fields
        }
        sensitive_meta_storage(
            SensitiveMeta.model_validate(lx_sensitive_payload), ctx.current_video
        )
        self._persist_paper_evaluation_metrics(ctx.current_video, extracted_metadata)
        self._persist_phi_region_proposals(ctx.current_video, extracted_metadata)
        return ctx

    def _persist_paper_evaluation_metrics(
        self,
        video: VideoFile,
        extracted_metadata: JsonObject,
    ) -> bool:
        try:
            metrics_payload = _paper_evaluation_metrics_payload(extracted_metadata)
        except ValueError as exc:
            logger.warning(
                "Failed to persist lx-anonymizer paper evaluation metrics for video %s: %s",
                getattr(video, "video_hash", None),
                exc,
            )
            return False
        if metrics_payload is None:
            return False

        current_meta = video.meta if isinstance(video.meta, dict) else {}
        try:
            next_meta = _json_compatible_mapping(
                cast(dict[object, object], current_meta),
                field_name="VideoFile.meta",
            )
        except ValueError as exc:
            logger.warning(
                "Failed to merge lx-anonymizer paper evaluation metrics for video %s: %s",
                getattr(video, "video_hash", None),
                exc,
            )
            return False
        next_meta[PAPER_EVALUATION_METRICS_KEY] = metrics_payload
        video.meta = cast(VideoFileMetaJsonObject, next_meta)
        video.save(update_fields=["meta"])
        return True

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
        assert FrameCleaner is not None
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

        try:
            raw_endoscope_image_roi = processor.get_roi_endoscope_image()
            raw_sensitive_rois = processor.get_sensitive_rois()
        except ValidationError as exc:
            raise RuntimeError(
                f"Endoscopy processor {processor_name!r} has invalid endoscope image ROI."
            ) from exc

        endoscope_image_roi = _require_endoscope_image_roi(
            raw_endoscope_image_roi,
            processor_name=processor_name,
        )
        endoscope_data_roi_nested = {
            name: _require_sensitive_roi_box(
                name,
                roi,
                processor_name=processor_name,
            )
            for name, roi in raw_sensitive_rois.items()
            if roi is not None
        }
        logger.info(
            "Retrieved processor ROI information: endoscope_image_roi=%s",
            endoscope_image_roi,
        )

        # IMPORTANT: return order must match clean_video signature
        return endoscope_image_roi, endoscope_data_roi_nested
