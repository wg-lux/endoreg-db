from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, TypedDict, cast

from django.db.models import Q, QuerySet
from endoreg_db.utils.ffmpeg_wrapper import (
    extract_frame_range as ffmpeg_extract_frame_range,
    extract_frames as ffmpeg_extract_frames,
    extract_frames_by_presentation_timestamp as ffmpeg_extract_frames_by_pts,
)
from lx_dtypes.models.contracts import (
    VideoFrameAnnotationExportConfigPayload,
    export_config,
    export_result,
    load_video_frame_annotation_export_config,
)
from pydantic import ValidationError

from endoreg_db.helpers.data_load_orchestrator import load_base_db_data
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import VideoSourceTimelineEvidence
from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
from endoreg_db.services.seekable_media_input import (
    SeekableFieldFile,
    serve_seekable_media_input,
)
from endoreg_db.services.video_files import get_video_frame_dir_path
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import (
    ensure_within_protected_media_root,
    normalize_protected_media_relative_path,
    resolve_existing_protected_media_path,
)
from endoreg_db.utils import ensure_local_file
from endoreg_db.utils.storage_streaming import (
    field_file_has_decrypted_range_storage,
    local_plaintext_path_from_name,
    maybe_local_plaintext_path,
)

logger = logging.getLogger(__name__)

VideoFrameAnnotationExportProfile: TypeAlias = Literal[
    "legacy_table_v1",
    "pts_dataset_v1",
]
type ConfigScalar = str | int | float | bool | None
type AnnotationCell = str | int | float | bool | None
type AnnotationFieldName = Literal[
    "annotation_id",
    "video_id",
    "video_hash",
    "frame_id",
    "frame_number",
    "frame_relative_path",
    "frame_timestamp",
    "presentation_timestamp",
    "presentation_time_seconds",
    "stream_time_base_num",
    "stream_time_base_den",
    "timeline_version",
    "export_frame_index",
    "artifact_kind",
    "artifact_sha256",
    "label_id",
    "label_name",
    "value",
    "float_value",
    "annotator",
    "information_source_id",
    "information_source_name",
    "model_meta_id",
    "date_created",
    "date_modified",
]

DEFAULT_FIELDNAMES: tuple[AnnotationFieldName, ...] = (
    "annotation_id",
    "video_id",
    "video_hash",
    "frame_id",
    "frame_number",
    "frame_relative_path",
    "frame_timestamp",
    "presentation_timestamp",
    "presentation_time_seconds",
    "stream_time_base_num",
    "stream_time_base_den",
    "timeline_version",
    "export_frame_index",
    "artifact_kind",
    "artifact_sha256",
    "label_id",
    "label_name",
    "value",
    "float_value",
    "annotator",
    "information_source_id",
    "information_source_name",
    "model_meta_id",
    "date_created",
    "date_modified",
)


class AnnotationRow(TypedDict):
    annotation_id: int
    video_id: int | None
    video_hash: str | None
    frame_id: int | None
    frame_number: int | None
    frame_relative_path: str | None
    frame_timestamp: float | None
    presentation_timestamp: int | None
    presentation_time_seconds: float | None
    stream_time_base_num: int | None
    stream_time_base_den: int | None
    timeline_version: str | None
    export_frame_index: int
    artifact_kind: str
    artifact_sha256: str | None
    label_name: str | None
    label_id: int | None
    value: bool | None
    float_value: float | None
    annotator: str | None
    information_source_id: int | None
    information_source_name: str | None
    model_meta_id: int | None
    date_created: str | None
    date_modified: str | None


class _LabelExportModel(Protocol):
    pk: int
    name: str


class _InformationSourceExportModel(Protocol):
    pk: int
    name: str


class _ModelMetaExportModel(Protocol):
    pk: int


class _FrameExportModel(Protocol):
    pk: int
    frame_number: int
    relative_path: str
    timestamp: float | None
    presentation_timestamp: int | None
    file_path: str
    video: VideoFile


class _FrameQuerySetLike(Protocol):
    def only(self, *fields: str) -> Iterable[_FrameExportModel]: ...


class _VideoFramesExportModel(Protocol):
    pk: int
    frames: _FrameQuerySetLike


class _VideoHashExportModel(Protocol):
    pk: int
    video_hash: str | None
    meta: dict[str, object] | None
    state: object | None


class _AnnotationExportModel(Protocol):
    pk: int
    frame: _FrameExportModel
    label: _LabelExportModel
    value: bool | None
    float_value: float | None
    annotator: str | None
    information_source: _InformationSourceExportModel | None
    model_meta: _ModelMetaExportModel | None
    date_created: datetime | None
    date_modified: datetime | None


class _LabelVideoSegmentExportModel(Protocol):
    pk: int
    label: _LabelExportModel | None
    video_file: VideoFile
    start_frame_number: int
    end_frame_number: int
    source_id: int | None

    def get_model_meta(self) -> _ModelMetaExportModel | None: ...


class _FrameValueRow(TypedDict):
    frame__video_id: int | None
    frame_id: int | None


@dataclass(frozen=True)
class _RequestedFrameCoordinates:
    requested_frame_pks: set[int]
    requested_frame_numbers: list[int]
    frames_by_presentation_timestamp: list[tuple[int, int]] | None
    extract_all_frames: bool


DEFAULT_TRANSCODE_FPS = 50.0
DEFAULT_TRANSCODE_QUALITY = 2
DEFAULT_TRANSCODE_EXT = "jpg"


def _config_bool(value: ConfigScalar, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _config_optional_bool(value: ConfigScalar) -> bool | None:
    if value is None:
        return None
    return _config_bool(value)


def _assert_video_media_export_ready(video: VideoFile) -> None:
    from endoreg_db.services.video_files import get_or_create_video_state

    state = getattr(video, "state", None)
    if state is None:
        state = get_or_create_video_state(video)

    if bool(getattr(state, "processing_error", False)):
        raise ValueError(
            f"Video {video.pk} is marked failed/lost; refusing media export"
        )

    if not bool(getattr(state, "anonymization_validated", False)):
        raise ValueError(
            f"Video {video.pk} is not human anonymization validated; "
            "refusing media export"
        )

    if not local_study_server_mode_enabled():
        return

    if not bool(getattr(state, "outside_segments_removed", False)):
        raise ValueError(
            f"Video {video.pk} has not had outside segments removed; "
            "refusing local_study_server media export"
        )
    if not bool(getattr(state, "ready_for_export", False)):
        raise ValueError(
            f"Video {video.pk} is not marked ready_for_export; "
            "refusing local_study_server media export"
        )

    expected_sha = str(getattr(state, "processed_file_sha256", "") or "").strip()
    if len(expected_sha) != 64:
        raise ValueError(
            f"Video {video.pk} has no processed_file_sha256 readiness proof; "
            "refusing local_study_server media export"
        )
    processed_file = getattr(video, "processed_file", None)
    if not processed_file or not getattr(processed_file, "name", None):
        raise FileNotFoundError(f"processed video artifact missing for {video.pk}")
    actual_sha = sha256_file(processed_file)
    if actual_sha != expected_sha:
        raise ValueError(
            f"Video {video.pk} processed_file_sha256 does not match the current "
            "processed artifact; refusing local_study_server media export"
        )


def _resolve_processed_video_source_path(video: VideoFile) -> Path | None:
    """
    Prefer the central protected-media path resolution used by streaming.
    Fall back to storage-backed downloads (ensure_local_file) elsewhere.
    """
    try:
        field_file = video.processed_file
    except Exception:
        return None

    name = getattr(field_file, "name", None)
    if not name:
        return None

    local_path = maybe_local_plaintext_path(field_file)
    if local_path is not None:
        return local_path
    return local_plaintext_path_from_name(
        name,
        resolver=resolve_existing_protected_media_path,
        # The real resolver enforces the protected-media boundary.
        require_protected_root=False,
    )


class export_job_failed_error(RuntimeError):
    def __init__(self, message: str, *, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


def _normalize_export_config(config: export_config) -> export_config:
    load_base_data = _config_bool(config.load_base_data)
    export_videos = _config_bool(config.export_videos)
    export_frames = _config_bool(config.export_frames, default=True)
    transcode_frames = _config_bool(config.transcode_frames)
    transcode_overwrite = _config_bool(config.transcode_overwrite)
    use_export_flags = _config_bool(config.use_export_flags, default=True)
    all_centers = _config_bool(config.all_centers)
    only_validated = _config_bool(config.only_validated, default=True)
    only_true = _config_optional_bool(config.only_true)
    center_key = str(config.center_key or "").strip() or None

    if config.export_profile == "pts_dataset_v1" and export_frames:
        # The PTS manifest names the processed artifact explicitly, so its images
        # must be regenerated from that same artifact rather than copied from a
        # legacy frame cache with unknown provenance.
        transcode_frames = True
        transcode_overwrite = True
        use_frame_pk_paths = True
    else:
        use_frame_pk_paths = config.use_frame_pk_paths

    if local_study_server_mode_enabled() and export_frames:
        # Local study server frame-image exports are generated from processed media
        # at export time. Reusing older frame files would not prove anonymized source.
        transcode_frames = True
        transcode_overwrite = True
        use_frame_pk_paths = True

    return replace(
        config,
        load_base_data=load_base_data,
        export_videos=export_videos,
        export_frames=export_frames,
        transcode_frames=transcode_frames,
        transcode_overwrite=transcode_overwrite,
        use_export_flags=use_export_flags,
        all_centers=all_centers,
        only_validated=only_validated,
        only_true=only_true,
        center_key=center_key,
        use_frame_pk_paths=use_frame_pk_paths,
    )


class annotation_exporter_client:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def run_export(self, config: export_config) -> export_result:
        config = _normalize_export_config(config)
        _validate_export_scope(config)
        output_path = _resolve_output_path(config)
        output_dir = _resolve_output_dir(config, output_path)
        self._logger.info(
            "Starting annotation export to %s (transcode_frames=%s, limit=%s)",
            output_path,
            config.transcode_frames,
            config.limit,
        )
        load_base_data = config.load_base_data
        if load_base_data:
            self._logger.info("Loading base data before export")
            load_base_db_data()
            load_base_data = False
        try:
            row_count = _count_annotations_for_config(config)
            if row_count == 0:
                self._logger.warning(
                    "Export query returned no rows for output %s", output_path
                )
            else:
                self._logger.info("Exporting %s rows to %s", row_count, output_path)

            if config.output_format == "json":
                exported_path = export_frames_with_labels_to_json(
                    output_path=output_path,
                    export_profile=config.export_profile,
                    video_id=config.video_id,
                    label_id=config.label_id,
                    information_source_name=config.information_source_name,
                    only_true=config.only_true,
                    limit=config.limit,
                    load_base_data=load_base_data,
                    use_export_flags=config.use_export_flags,
                    segment_ids=config.segment_ids,
                    center_key=config.center_key,
                    all_centers=config.all_centers,
                    only_validated=config.only_validated,
                    transcode_frames=False,
                    transcode_fps=config.transcode_fps,
                    transcode_quality=config.transcode_quality,
                    transcode_ext=config.transcode_ext,
                    transcode_overwrite=config.transcode_overwrite,
                    use_frame_pk_paths=config.use_frame_pk_paths,
                )
            else:
                exported_path = export_frames_with_labels_to_csv(
                    output_path=output_path,
                    export_profile=config.export_profile,
                    video_id=config.video_id,
                    label_id=config.label_id,
                    information_source_name=config.information_source_name,
                    only_true=config.only_true,
                    limit=config.limit,
                    load_base_data=load_base_data,
                    use_export_flags=config.use_export_flags,
                    segment_ids=config.segment_ids,
                    center_key=config.center_key,
                    all_centers=config.all_centers,
                    only_validated=config.only_validated,
                    transcode_frames=False,
                    transcode_fps=config.transcode_fps,
                    transcode_quality=config.transcode_quality,
                    transcode_ext=config.transcode_ext,
                    transcode_overwrite=config.transcode_overwrite,
                    use_frame_pk_paths=config.use_frame_pk_paths,
                )

            exported_video_count = 0
            exported_frame_count = 0
            video_output_dir = None
            frame_output_dir = None

            if config.export_videos or config.export_frames:
                annotations = _build_annotations_queryset()
                annotations = _apply_filters(
                    annotations,
                    video_id=config.video_id,
                    label_id=config.label_id,
                    information_source_name=config.information_source_name,
                    only_true=config.only_true,
                    use_export_flags=config.use_export_flags,
                    segment_ids=config.segment_ids,
                    center_key=config.center_key,
                    all_centers=config.all_centers,
                    only_validated=config.only_validated,
                )
                if config.limit is not None:
                    annotations = annotations[: config.limit]

                export_result_data = _export_media_assets(
                    annotations,
                    output_dir=output_dir,
                    export_videos=config.export_videos,
                    export_frames=config.export_frames,
                    transcode_frames=config.transcode_frames,
                    transcode_fps=config.transcode_fps,
                    transcode_quality=config.transcode_quality,
                    transcode_ext=config.transcode_ext,
                    transcode_overwrite=config.transcode_overwrite,
                    use_frame_pk_paths=config.use_frame_pk_paths,
                    strict_media_validation=True,
                )
                exported_video_count = export_result_data["exported_video_count"]
                exported_frame_count = export_result_data["exported_frame_count"]
                video_output_dir = export_result_data["video_output_dir"]
                frame_output_dir = export_result_data["frame_output_dir"]

            self._logger.info(
                "Annotation export completed successfully: %s", exported_path
            )
            return export_result(
                output_path=Path(exported_path),
                row_count=row_count,
                success=True,
                exported_video_count=exported_video_count,
                exported_frame_count=exported_frame_count,
                video_output_dir=video_output_dir,
                frame_output_dir=frame_output_dir,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            self._logger.error(
                "Annotation export failed for %s: %s",
                output_path,
                exc,
                exc_info=True,
            )
            raise export_job_failed_error(
                "annotation export failed", original_error=exc
            ) from exc
        except Exception as exc:
            self._logger.exception(
                "Annotation export failed unexpectedly for %s", output_path
            )
            raise export_job_failed_error(
                "annotation export failed", original_error=exc
            ) from exc
        finally:
            self._logger.info("Annotation export finished for %s", output_path)


def load_export_config(
    config_path: Path | str,
) -> VideoFrameAnnotationExportConfigPayload:
    try:
        return load_video_frame_annotation_export_config(config_path)
    except ValidationError as exc:
        raise ValueError(f"invalid export config: {exc}") from exc


def export_frames_with_labels_from_yaml(config_path: Path | str) -> Path:
    config_data = load_export_config(config_path)

    return export_frames_with_labels_to_csv(
        output_path=config_data.output_path,
        export_profile=config_data.export_profile,
        video_id=config_data.video_id,
        label_id=config_data.label_id,
        information_source_name=config_data.information_source_name,
        only_true=config_data.only_true,
        limit=config_data.limit,
        load_base_data=config_data.load_base_data,
        export_videos=config_data.export_videos,
        export_frames=config_data.export_frames,
        use_export_flags=config_data.use_export_flags,
        segment_ids=config_data.segment_ids,
        center_key=config_data.center_key,
        all_centers=config_data.all_centers,
        only_validated=config_data.only_validated,
        transcode_frames=config_data.transcode_frames,
        transcode_fps=config_data.transcode_fps,
        transcode_quality=config_data.transcode_quality,
        transcode_ext=config_data.transcode_ext,
        transcode_overwrite=config_data.transcode_overwrite,
        use_frame_pk_paths=config_data.use_frame_pk_paths,
    )


def export_frames_with_labels_to_csv(
    output_path: Path | str,
    *,
    export_profile: VideoFrameAnnotationExportProfile = "legacy_table_v1",
    annotations: QuerySet[ImageClassificationAnnotation] | None = None,
    video_id: int | None = None,
    label_id: int | None = None,
    information_source_name: str | None = None,
    only_true: bool | None = None,
    limit: int | None = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = True,
    use_export_flags: bool = True,
    segment_ids: list[int] | None = None,
    center_key: str | None = None,
    all_centers: bool = False,
    only_validated: bool = True,
    transcode_frames: bool = False,
    transcode_fps: float = DEFAULT_TRANSCODE_FPS,
    transcode_quality: int = DEFAULT_TRANSCODE_QUALITY,
    transcode_ext: str = DEFAULT_TRANSCODE_EXT,
    transcode_overwrite: bool = False,
    use_frame_pk_paths: bool | None = None,
) -> Path:
    if load_base_data:
        load_base_db_data()

    output_file = Path(output_path)
    ensure_directory(output_file.parent)

    if annotations is None:
        annotations = _build_annotations_queryset()
    annotations = _apply_filters(
        annotations,
        video_id=video_id,
        label_id=label_id,
        information_source_name=information_source_name,
        only_true=only_true,
        use_export_flags=use_export_flags,
        segment_ids=segment_ids,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    )
    if limit is not None:
        annotations = annotations[:limit]

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames
    if local_study_server_mode_enabled() and export_frames:
        transcode_frames = True
        transcode_overwrite = True
        use_frame_pk_paths = True

    if transcode_frames:
        transcode_videos_for_annotations(
            annotations,
            fps=transcode_fps,
            quality=transcode_quality,
            ext=transcode_ext,
            overwrite=transcode_overwrite,
            export_frame_root=(
                output_file.parent / "generated_frames"
                if local_study_server_mode_enabled()
                else None
            ),
        )

    rows = _build_annotation_rows(
        annotations,
        export_profile=export_profile,
        use_frame_pk_paths=use_frame_pk_paths,
        frame_ext=transcode_ext,
    )
    csv_buffer = io.StringIO()
    writer = csv.DictWriter[AnnotationFieldName](
        csv_buffer,
        fieldnames=DEFAULT_FIELDNAMES,
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(_annotation_row_to_csv_dict(row))
    atomic_write_file(
        destination=output_file,
        content=[csv_buffer.getvalue().encode("utf-8")],
    )

    return output_file


def export_frames_with_labels_to_json(
    output_path: Path | str,
    *,
    export_profile: VideoFrameAnnotationExportProfile = "legacy_table_v1",
    annotations: QuerySet[ImageClassificationAnnotation] | None = None,
    video_id: int | None = None,
    label_id: int | None = None,
    information_source_name: str | None = None,
    only_true: bool | None = None,
    limit: int | None = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = True,
    use_export_flags: bool = True,
    segment_ids: list[int] | None = None,
    center_key: str | None = None,
    all_centers: bool = False,
    only_validated: bool = True,
    transcode_frames: bool = False,
    transcode_fps: float = DEFAULT_TRANSCODE_FPS,
    transcode_quality: int = DEFAULT_TRANSCODE_QUALITY,
    transcode_ext: str = DEFAULT_TRANSCODE_EXT,
    transcode_overwrite: bool = False,
    use_frame_pk_paths: bool | None = None,
) -> Path:
    if load_base_data:
        load_base_db_data()

    output_file = Path(output_path)
    ensure_directory(output_file.parent)

    if annotations is None:
        annotations = _build_annotations_queryset()

    annotations = _apply_filters(
        annotations,
        video_id=video_id,
        label_id=label_id,
        information_source_name=information_source_name,
        only_true=only_true,
        use_export_flags=use_export_flags,
        segment_ids=segment_ids,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    )

    if limit is not None:
        annotations = annotations[:limit]

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames
    if local_study_server_mode_enabled() and export_frames:
        transcode_frames = True
        transcode_overwrite = True
        use_frame_pk_paths = True

    if transcode_frames:
        transcode_videos_for_annotations(
            annotations,
            fps=transcode_fps,
            quality=transcode_quality,
            ext=transcode_ext,
            overwrite=transcode_overwrite,
            export_frame_root=(
                output_file.parent / "generated_frames"
                if local_study_server_mode_enabled()
                else None
            ),
        )

    rows = _build_annotation_rows(
        annotations,
        export_profile=export_profile,
        use_frame_pk_paths=use_frame_pk_paths,
        frame_ext=transcode_ext,
    )

    atomic_write_file(
        destination=output_file,
        content=[json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")],
    )

    return output_file


def _resolve_output_path(config: export_config) -> Path:
    output_path = Path(config.output_path)
    output_dir = config.output_dir
    if output_dir:
        base_dir = Path(output_dir)
        if not output_path.is_absolute():
            return base_dir / output_path
    return output_path


def _resolve_output_dir(config: export_config, output_path: Path) -> Path:
    if config.output_dir:
        return Path(config.output_dir)
    return output_path.parent


def _validate_export_scope(config: export_config) -> None:
    normalized_center_key = (config.center_key or "").strip()
    config_center_key = normalized_center_key or None

    if config_center_key and config.all_centers:
        raise ValueError("Export scope must use center_key or all_centers, not both")

    if config_center_key:
        if not Center.objects.filter(center_key=config_center_key).exists():
            raise ValueError(f"Unknown center_key: {config_center_key}")

    if local_study_server_mode_enabled() and not (
        bool(config_center_key) ^ bool(config.all_centers)
    ):
        raise ValueError(
            "local_study_server exports require exactly one center scope: "
            "center_key or all_centers"
        )


class ExportAssetResult(TypedDict):
    exported_video_count: int
    exported_frame_count: int
    video_output_dir: Path | None
    frame_output_dir: Path | None


def _export_media_assets(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    output_dir: Path,
    export_videos: bool,
    export_frames: bool,
    transcode_frames: bool,
    transcode_fps: float,
    transcode_quality: int,
    transcode_ext: str,
    transcode_overwrite: bool,
    use_frame_pk_paths: bool | None,
    strict_media_validation: bool,
) -> ExportAssetResult:
    ensure_directory(output_dir)
    video_output_dir = output_dir / "videos" if export_videos else None
    frame_output_dir = output_dir / "frames" if export_frames else None

    if export_videos and video_output_dir:
        ensure_directory(video_output_dir)
    if export_frames and frame_output_dir:
        ensure_directory(frame_output_dir)

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames

    exported_video_count = 0
    exported_frame_count = 0
    generated_frame_root = output_dir / "generated_frames" if transcode_frames else None

    if export_videos:
        exported_video_count = _export_videos_from_annotations(
            annotations,
            output_dir=video_output_dir,
            strict_media_validation=strict_media_validation,
        )

    if export_frames:
        if transcode_frames:
            transcode_videos_for_annotations(
                annotations,
                fps=transcode_fps,
                quality=transcode_quality,
                ext=transcode_ext,
                overwrite=transcode_overwrite,
                export_frame_root=generated_frame_root,
            )
        exported_frame_count = _export_frames_from_annotations(
            annotations,
            output_dir=frame_output_dir,
            use_frame_pk_paths=use_frame_pk_paths,
            frame_ext=transcode_ext,
            strict_media_validation=strict_media_validation,
            generated_frame_root=generated_frame_root,
        )

    return {
        "exported_video_count": exported_video_count,
        "exported_frame_count": exported_frame_count,
        "video_output_dir": video_output_dir,
        "frame_output_dir": frame_output_dir,
    }


def _export_videos_from_annotations(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    output_dir: Path | None,
    strict_media_validation: bool = True,
) -> int:
    if output_dir is None:
        return 0

    video_ids = (
        annotations.values_list("frame__video_id", flat=True)
        .distinct()
        .order_by("frame__video_id")
    )
    videos = VideoFile.objects.filter(pk__in=video_ids)
    exported_count = 0

    for video in videos:
        try:
            try:
                _assert_video_media_export_ready(video)
            except (ValueError, FileNotFoundError) as exc:
                if strict_media_validation:
                    raise
                logger.warning(str(exc))
                continue

            source_path = _resolve_processed_video_source_path(video)
            if source_path is not None:
                suffix = source_path.suffix or ".mp4"
                video_export = cast(_VideoHashExportModel, video)
                video_hash = video_export.video_hash or "unknown"
                target_path = output_dir / f"video_{video.pk}_{video_hash}{suffix}"
                try:
                    atomic_copy_file(source=source_path, destination=target_path)
                    exported_count += 1
                    continue
                except OSError as exc:
                    logger.warning(
                        "Failed to export video %s from %s: %s",
                        video.pk,
                        source_path,
                        exc,
                    )

            processed_file = getattr(video, "processed_file", None)
            if not processed_file or not getattr(processed_file, "name", None):
                raise FileNotFoundError(
                    f"processed video artifact missing for {video.pk}"
                )

            # Fallback: attempt to download processed media via Django storage backend.
            with ensure_local_file(processed_file) as source_path_fallback:
                suffix = source_path_fallback.suffix or ".mp4"
                video_export = cast(_VideoHashExportModel, video)
                video_hash = video_export.video_hash or "unknown"
                target_path = output_dir / f"video_{video.pk}_{video_hash}{suffix}"
                try:
                    atomic_copy_file(
                        source=source_path_fallback,
                        destination=target_path,
                    )
                    exported_count += 1
                except OSError as exc:
                    logger.warning(
                        "Failed to export video %s from %s: %s",
                        video.pk,
                        source_path_fallback,
                        exc,
                    )
        except (ValueError, FileNotFoundError, IOError) as exc:
            if strict_media_validation:
                raise
            logger.warning("Skipping video %s: %s", video.pk, exc)

    return exported_count


def _export_frames_from_annotations(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    output_dir: Path | None,
    use_frame_pk_paths: bool,
    frame_ext: str,
    strict_media_validation: bool = True,
    generated_frame_root: Path | None = None,
) -> int:
    if output_dir is None:
        return 0
    if local_study_server_mode_enabled() and generated_frame_root is None:
        raise ValueError(
            "local_study_server frame media export requires export-scoped "
            "generated frames from processed_file"
        )
    if local_study_server_mode_enabled() and not use_frame_pk_paths:
        raise ValueError(
            "local_study_server frame media export requires frame-pk paths "
            "generated from processed_file"
        )

    exported_count = 0
    copied_frames: set[tuple[int, str]] = set()

    for annotation in annotations.iterator():
        annotation_export = cast(_AnnotationExportModel, annotation)
        frame = annotation_export.frame
        video = frame.video
        try:
            _assert_video_media_export_ready(video)
        except (ValueError, FileNotFoundError) as exc:
            if strict_media_validation:
                raise
            logger.warning(str(exc))
            continue

        frame_relative_path = (
            _frame_pk_filename(frame.pk, frame_ext)
            if use_frame_pk_paths
            else frame.relative_path
        )
        if not frame_relative_path:
            continue
        try:
            frame_relative_path = normalize_protected_media_relative_path(
                frame_relative_path
            )
        except ValueError as exc:
            logger.warning(
                "Skipping frame %s with unsafe relative path %r: %s",
                frame.pk,
                frame_relative_path,
                exc,
            )
            continue

        frame_key = (video.pk, frame_relative_path)
        if frame_key in copied_frames:
            continue

        source_path = _resolve_frame_source_path(
            frame,
            frame_relative_path=frame_relative_path,
            use_frame_pk_paths=use_frame_pk_paths,
            frame_ext=frame_ext,
            generated_frame_root=generated_frame_root,
        )
        if source_path is None or not source_path.exists():
            logger.warning(
                "Frame source missing for video %s frame %s",
                video.pk,
                frame.pk,
            )
            continue

        target_dir = output_dir / f"video_{video.pk}"
        target_path = target_dir / frame_relative_path
        ensure_directory(target_path.parent)
        try:
            atomic_copy_file(source=source_path, destination=target_path)
            exported_count += 1
            copied_frames.add(frame_key)
        except OSError as exc:
            logger.warning(
                "Failed to export frame %s from %s: %s",
                frame.pk,
                source_path,
                exc,
            )

    return exported_count


def _resolve_frame_source_path(
    frame: _FrameExportModel,
    *,
    frame_relative_path: str,
    use_frame_pk_paths: bool,
    frame_ext: str,
    generated_frame_root: Path | None = None,
) -> Path | None:
    video = frame.video

    if use_frame_pk_paths:
        if generated_frame_root is not None:
            generated_path = (
                generated_frame_root / f"video_{video.pk}" / frame_relative_path
            )
            if local_study_server_mode_enabled():
                return generated_path
            if generated_path.exists():
                return generated_path
        frame_dir = get_video_frame_dir_path(video)
        if frame_dir is None:
            return None
        pk_path = frame_dir / frame_relative_path
        resolved_pk_path = resolve_existing_protected_media_path(pk_path)
        if resolved_pk_path is not None:
            return resolved_pk_path

    return resolve_existing_protected_media_path(frame.file_path)


def transcode_videos_for_annotations(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    fps: float = DEFAULT_TRANSCODE_FPS,
    quality: int = DEFAULT_TRANSCODE_QUALITY,
    ext: str = DEFAULT_TRANSCODE_EXT,
    overwrite: bool = False,
    export_frame_root: Path | None = None,
) -> None:
    frame_rows = cast(
        Iterable[_FrameValueRow],
        annotations.values("frame__video_id", "frame_id"),
    )
    video_frame_pks: dict[int, set[int]] = {}
    for row in frame_rows:
        video_id = row["frame__video_id"]
        frame_id = row["frame_id"]
        if not video_id or not frame_id:
            continue
        video_frame_pks.setdefault(video_id, set()).add(frame_id)

    if not video_frame_pks:
        return

    overwrite = overwrite or local_study_server_mode_enabled()
    videos = VideoFile.objects.filter(pk__in=video_frame_pks.keys())
    for video in videos:
        _transcode_video_to_frame_dir(
            video,
            frame_pks=video_frame_pks.get(video.pk),
            fps=fps,
            quality=quality,
            ext=ext,
            overwrite=overwrite,
            export_frame_root=export_frame_root,
        )


def _transcode_video_to_frame_dir(
    video: VideoFile,
    *,
    frame_pks: set[int] | None,
    fps: float,
    quality: int,
    ext: str,
    overwrite: bool,
    export_frame_root: Path | None = None,
) -> None:
    if export_frame_root is not None:
        frame_dir = export_frame_root / f"video_{video.pk}"
    else:
        video_frame_dir = get_video_frame_dir_path(video)
        if video_frame_dir is None:
            raise ValueError(f"frame dir not available for video {video.pk}")
        frame_dir = ensure_within_protected_media_root(video_frame_dir)

    ensure_directory(frame_dir)

    if frame_pks and not overwrite:
        expected_names = {_frame_pk_filename(pk, ext) for pk in frame_pks}
        existing_names = {path.name for path in frame_dir.glob(f"frame_*.{ext}")}
        if expected_names.issubset(existing_names):
            logger.info(
                "Skipping transcode for video %s: frames already present.",
                video.pk,
            )
            return

    try:
        _assert_video_media_export_ready(video)
        source_path = _resolve_processed_video_source_path(video)
        if source_path is None:
            processed_file = getattr(video, "processed_file", None)
            if not processed_file or not getattr(processed_file, "name", None):
                raise FileNotFoundError(
                    f"processed video artifact missing for {video.pk}"
                )
            if field_file_has_decrypted_range_storage(processed_file):
                with serve_seekable_media_input(
                    cast(SeekableFieldFile, processed_file)
                ) as seekable_source:
                    _extract_and_move_transcoded_frames(
                        video,
                        source_path=seekable_source.url,
                        frame_dir=frame_dir,
                        frame_pks=frame_pks,
                        fps=fps,
                        quality=quality,
                        ext=ext,
                        overwrite=overwrite,
                    )
                return
            with ensure_local_file(processed_file) as source_path_fallback:
                _extract_and_move_transcoded_frames(
                    video,
                    source_path=source_path_fallback,
                    frame_dir=frame_dir,
                    frame_pks=frame_pks,
                    fps=fps,
                    quality=quality,
                    ext=ext,
                    overwrite=overwrite,
                )
                return

        _extract_and_move_transcoded_frames(
            video,
            source_path=source_path,
            frame_dir=frame_dir,
            frame_pks=frame_pks,
            fps=fps,
            quality=quality,
            ext=ext,
            overwrite=overwrite,
        )
    except (ValueError, FileNotFoundError, IOError) as exc:
        raise ValueError(f"processed video path missing for {video.pk}: {exc}") from exc


def _extract_and_move_transcoded_frames(
    video: VideoFile,
    *,
    source_path: Path | str,
    frame_dir: Path,
    frame_pks: set[int] | None,
    fps: float,
    quality: int,
    ext: str,
    overwrite: bool,
) -> None:
    if fps > 0:
        logger.info(
            "Ignoring legacy transcode_fps=%s for video %s; annotation frame "
            "identity is preserved using source frame coordinates.",
            fps,
            video.pk,
        )
    tmp_dir = ensure_directory(frame_dir / f"transcode_tmp_{uuid.uuid4().hex}")
    try:
        coordinates = _requested_frame_coordinates(video, frame_pks=frame_pks)
        if not coordinates.requested_frame_numbers:
            return
        moved_frame_pks = _extract_requested_frames(
            video,
            source_path=source_path,
            tmp_dir=tmp_dir,
            frame_dir=frame_dir,
            coordinates=coordinates,
            quality=quality,
            ext=ext,
            overwrite=overwrite,
        )
        _require_all_requested_frames(video, coordinates, moved_frame_pks)
    finally:
        safe_rmtree(tmp_dir, missing_ok=True)


def _requested_frame_coordinates(
    video: VideoFile,
    *,
    frame_pks: set[int] | None,
) -> _RequestedFrameCoordinates:
    persisted_coordinates = {
        int(frame.pk): (int(frame.frame_number), frame.presentation_timestamp)
        for frame in cast(_VideoFramesExportModel, video).frames.only(
            "pk", "frame_number", "presentation_timestamp"
        )
    }
    all_frame_pks = set(persisted_coordinates)
    requested_frame_pks = all_frame_pks if frame_pks is None else set(frame_pks)
    missing_pks = requested_frame_pks - all_frame_pks
    if missing_pks:
        raise ValueError(
            f"Requested annotation frames are missing for video {video.pk}: "
            f"{sorted(missing_pks)[:10]}"
        )

    requested_frame_numbers = sorted(
        persisted_coordinates[frame_pk][0] for frame_pk in requested_frame_pks
    )
    frames_by_presentation_timestamp = _ordered_presentation_timestamps(
        video,
        frame_pks=frame_pks,
        requested_frame_pks=requested_frame_pks,
        persisted_coordinates=persisted_coordinates,
    )
    return _RequestedFrameCoordinates(
        requested_frame_pks=requested_frame_pks,
        requested_frame_numbers=requested_frame_numbers,
        frames_by_presentation_timestamp=frames_by_presentation_timestamp,
        extract_all_frames=frame_pks is None,
    )


def _ordered_presentation_timestamps(
    video: VideoFile,
    *,
    frame_pks: set[int] | None,
    requested_frame_pks: set[int],
    persisted_coordinates: dict[int, tuple[int, int | None]],
) -> list[tuple[int, int]] | None:
    if frame_pks is None:
        return None
    timestamps = [
        persisted_coordinates[frame_pk][1] for frame_pk in requested_frame_pks
    ]
    if any(timestamp is None for timestamp in timestamps):
        return None

    ordered = sorted(
        (cast(int, persisted_coordinates[frame_pk][1]), frame_pk)
        for frame_pk in requested_frame_pks
    )
    if len({timestamp for timestamp, _ in ordered}) != len(ordered):
        raise ValueError(f"Duplicate presentation timestamps for video {video.pk}")
    return ordered


def _extract_requested_frames(
    video: VideoFile,
    *,
    source_path: Path | str,
    tmp_dir: Path,
    frame_dir: Path,
    coordinates: _RequestedFrameCoordinates,
    quality: int,
    ext: str,
    overwrite: bool,
) -> set[int]:
    if coordinates.frames_by_presentation_timestamp is not None:
        return _extract_sparse_presentation_timestamp_frames(
            video,
            source_path=source_path,
            tmp_dir=tmp_dir,
            frame_dir=frame_dir,
            frames_by_presentation_timestamp=(
                coordinates.frames_by_presentation_timestamp
            ),
            quality=quality,
            ext=ext,
            overwrite=overwrite,
        )
    if isinstance(source_path, str):
        raise ValueError(
            "Seekable encrypted export requires exact presentation timestamps"
        )

    extracted_paths = _extract_local_frame_paths(
        source_path,
        tmp_dir,
        requested_frame_numbers=coordinates.requested_frame_numbers,
        extract_all=coordinates.extract_all_frames,
        quality=quality,
        ext=ext,
    )
    return _move_extracted_frames_to_pk_names(
        video,
        extracted_paths,
        frame_dir,
        frame_pks=coordinates.requested_frame_pks,
        ext=ext,
        overwrite=overwrite,
    )


def _extract_sparse_presentation_timestamp_frames(
    video: VideoFile,
    *,
    source_path: Path | str,
    tmp_dir: Path,
    frame_dir: Path,
    frames_by_presentation_timestamp: list[tuple[int, int]],
    quality: int,
    ext: str,
    overwrite: bool,
) -> set[int]:
    time_base_num, time_base_den = _required_video_time_base(video)
    extracted_paths = ffmpeg_extract_frames_by_pts(
        source_path,
        tmp_dir,
        [timestamp for timestamp, _ in frames_by_presentation_timestamp],
        time_base_num=time_base_num,
        time_base_den=time_base_den,
        quality=quality,
        ext=ext,
    )
    return _move_sparse_pts_frames_to_pk_names(
        extracted_paths,
        frame_dir,
        ordered_frame_pks=[
            frame_pk for _, frame_pk in frames_by_presentation_timestamp
        ],
        ext=ext,
        overwrite=overwrite,
    )


def _extract_local_frame_paths(
    source_path: Path,
    tmp_dir: Path,
    *,
    requested_frame_numbers: list[int],
    extract_all: bool,
    quality: int,
    ext: str,
) -> list[Path]:
    if extract_all:
        return ffmpeg_extract_frames(
            source_path,
            tmp_dir,
            quality=quality,
            ext=ext,
            fps=None,
        )
    return ffmpeg_extract_frame_range(
        source_path,
        tmp_dir,
        start_frame=requested_frame_numbers[0],
        end_frame=requested_frame_numbers[-1] + 1,
        quality=quality,
        ext=ext,
    )


def _require_all_requested_frames(
    video: VideoFile,
    coordinates: _RequestedFrameCoordinates,
    moved_frame_pks: set[int],
) -> None:
    missing_outputs = sorted(coordinates.requested_frame_pks - moved_frame_pks)
    if missing_outputs:
        raise RuntimeError(
            f"Identity-preserving extraction did not produce requested frames "
            f"for video {video.pk}: {missing_outputs[:10]}"
        )


def _move_sparse_pts_frames_to_pk_names(
    extracted_paths: list[Path],
    frame_dir: Path,
    *,
    ordered_frame_pks: list[int],
    ext: str,
    overwrite: bool,
) -> set[int]:
    if len(extracted_paths) != len(ordered_frame_pks):
        raise RuntimeError("Sparse presentation-timestamp frame count mismatch")
    moved: set[int] = set()
    for extracted_path, frame_pk in zip(
        sorted(extracted_paths), ordered_frame_pks, strict=True
    ):
        target_path = frame_dir / _frame_pk_filename(frame_pk, ext)
        if target_path.exists() and not overwrite:
            safe_unlink_file(extracted_path, missing_ok=True)
        else:
            atomic_move_file(source=extracted_path, destination=target_path)
        moved.add(frame_pk)
    return moved


def _required_video_time_base(video: VideoFile) -> tuple[int, int]:
    evidence = _video_timeline_evidence(cast(_VideoHashExportModel, video))
    if evidence is None:
        raise ValueError(f"Video {video.pk} has no pts_v1 timeline evidence")
    numerator = evidence.source.timeline.time_base_num
    denominator = evidence.source.timeline.time_base_den
    if numerator is None or denominator is None:
        raise ValueError(f"Video {video.pk} has no stream time base")
    return numerator, denominator


def _move_extracted_frames_to_pk_names(
    video: VideoFile,
    extracted_paths: list[Path],
    frame_dir: Path,
    *,
    frame_pks: set[int] | None,
    ext: str,
    overwrite: bool,
) -> set[int]:
    video_export = cast(_VideoFramesExportModel, video)
    frames_by_number = {
        frame.frame_number: frame
        for frame in video_export.frames.only("pk", "frame_number")
    }
    if not frames_by_number:
        raise ValueError(f"No persisted frames available for video {video.pk}")

    moved_frame_pks: set[int] = set()
    for extracted_path in sorted(extracted_paths):
        frame_number = _parse_extracted_frame_number(extracted_path)
        if frame_number is None:
            safe_unlink_file(extracted_path, missing_ok=True)
            continue

        frame = frames_by_number.get(frame_number)
        if frame is None:
            safe_unlink_file(extracted_path, missing_ok=True)
            continue

        if frame_pks is not None and frame.pk not in frame_pks:
            safe_unlink_file(extracted_path, missing_ok=True)
            continue

        target_path = frame_dir / _frame_pk_filename(frame.pk, ext)
        if target_path.exists() and not overwrite:
            safe_unlink_file(extracted_path, missing_ok=True)
            moved_frame_pks.add(int(frame.pk))
            continue

        atomic_move_file(source=extracted_path, destination=target_path)
        moved_frame_pks.add(int(frame.pk))
    return moved_frame_pks


def _parse_extracted_frame_number(frame_path: Path) -> int | None:
    try:
        return int(frame_path.stem.split("_")[-1])
    except (ValueError, IndexError):
        return None


def _frame_pk_filename(frame_pk: int, ext: str) -> str:
    return f"frame_{frame_pk}.{ext}"


def _annotation_row_to_csv_dict(
    row: AnnotationRow,
) -> dict[AnnotationFieldName, AnnotationCell]:
    return {
        "annotation_id": row["annotation_id"],
        "video_id": row["video_id"],
        "video_hash": row["video_hash"],
        "frame_id": row["frame_id"],
        "frame_number": row["frame_number"],
        "frame_relative_path": row["frame_relative_path"],
        "frame_timestamp": row["frame_timestamp"],
        "presentation_timestamp": row["presentation_timestamp"],
        "presentation_time_seconds": row["presentation_time_seconds"],
        "stream_time_base_num": row["stream_time_base_num"],
        "stream_time_base_den": row["stream_time_base_den"],
        "timeline_version": row["timeline_version"],
        "export_frame_index": row["export_frame_index"],
        "artifact_kind": row["artifact_kind"],
        "artifact_sha256": row["artifact_sha256"],
        "label_id": row["label_id"],
        "label_name": row["label_name"],
        "value": row["value"],
        "float_value": row["float_value"],
        "annotator": row["annotator"],
        "information_source_id": row["information_source_id"],
        "information_source_name": row["information_source_name"],
        "model_meta_id": row["model_meta_id"],
        "date_created": row["date_created"],
        "date_modified": row["date_modified"],
    }


def _build_annotations_queryset() -> QuerySet[ImageClassificationAnnotation]:
    return ImageClassificationAnnotation.objects.select_related(
        "frame",
        "frame__video",
        "label",
        "information_source",
        "model_meta",
    ).order_by("frame__video_id", "frame__frame_number", "label_id", "id")


def _apply_filters(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    video_id: int | None,
    label_id: int | None,
    information_source_name: str | None,
    only_true: bool | None,
    use_export_flags: bool = False,
    segment_ids: list[int] | None = None,
    center_key: str | None = None,
    all_centers: bool = False,
    only_validated: bool = True,
) -> QuerySet[ImageClassificationAnnotation]:
    normalized_center_key = (center_key or "").strip()
    if normalized_center_key:
        annotations = annotations.filter(
            frame__video__center__center_key=normalized_center_key
        )
    elif not all_centers and local_study_server_mode_enabled():
        raise ValueError(
            "local_study_server exports require center_key unless all_centers is true"
        )
    if video_id is not None:
        annotations = annotations.filter(frame__video_id=video_id)
    if label_id is not None:
        annotations = annotations.filter(label_id=label_id)
    if information_source_name is not None:
        annotations = annotations.filter(
            information_source__name=information_source_name
        )
    if only_true is not None:
        annotations = annotations.filter(value=only_true)
    if only_validated:
        annotations = annotations.filter(
            frame__video__state__anonymization_validated=True
        )
        if local_study_server_mode_enabled():
            annotations = annotations.filter(
                frame__video__state__outside_segments_removed=True,
                frame__video__state__ready_for_export=True,
            ).exclude(frame__video__state__processed_file_sha256="")
    if use_export_flags or segment_ids:
        annotations = _filter_annotations_by_segments(
            annotations,
            video_id=video_id,
            segment_ids=segment_ids,
            use_export_flags=use_export_flags,
        )
    return annotations


def _count_annotations_for_config(config: export_config) -> int:
    annotations = _build_annotations_queryset()
    annotations = _apply_filters(
        annotations,
        video_id=config.video_id,
        label_id=config.label_id,
        information_source_name=config.information_source_name,
        only_true=config.only_true,
        use_export_flags=config.use_export_flags,
        segment_ids=config.segment_ids,
        center_key=config.center_key,
        all_centers=config.all_centers,
        only_validated=config.only_validated,
    )
    if config.limit is None:
        return annotations.count()
    if config.limit <= 0:
        return 0
    total = annotations.count()
    return min(total, config.limit)


def _filter_annotations_by_segments(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    video_id: int | None,
    segment_ids: list[int] | None,
    use_export_flags: bool,
) -> QuerySet[ImageClassificationAnnotation]:
    segments = LabelVideoSegment.objects.select_related(
        "video_file",
        "label",
        "source",
        "prediction_meta",
    )
    if video_id is not None:
        segments = segments.filter(video_file_id=video_id)

    if segment_ids:
        segments = segments.filter(pk__in=segment_ids)
    elif use_export_flags:
        segments = segments.filter(
            Q(export_segment=True) | Q(video_file__export_segments_by_video=True)
        )

    segment_list = list(segments)
    if not segment_list:
        return annotations.none()

    segment_filter = Q()
    for segment_row in segment_list:
        segment = cast(_LabelVideoSegmentExportModel, segment_row)
        if segment.label is None:
            continue

        seg_filter = Q(
            frame__video=segment.video_file,
            frame__frame_number__gte=segment.start_frame_number,
            frame__frame_number__lt=segment.end_frame_number,
            label=segment.label,
        )

        if segment.source_id is None:
            seg_filter &= Q(information_source__isNone=True)
        else:
            seg_filter &= Q(information_source_id=segment.source_id)

        model_meta = segment.get_model_meta()
        if model_meta is None:
            seg_filter &= Q(model_meta__isNone=True)
        else:
            seg_filter &= Q(model_meta_id=model_meta.pk)

        segment_filter |= seg_filter

    if not segment_filter:
        return annotations.none()

    return annotations.filter(segment_filter)


def _annotation_to_row(
    annotation: ImageClassificationAnnotation,
    *,
    use_frame_pk_paths: bool,
    frame_ext: str,
    export_frame_index: int,
    export_profile: VideoFrameAnnotationExportProfile,
) -> AnnotationRow:
    annotation_export = cast(_AnnotationExportModel, annotation)
    frame = annotation_export.frame
    video = cast(_VideoHashExportModel, frame.video)
    label = annotation_export.label
    information_source = annotation_export.information_source

    if information_source is None:
        raise ValueError("Information Source is None.")

    if use_frame_pk_paths:
        frame_relative_path = _frame_pk_filename(frame.pk, frame_ext)
    else:
        frame_relative_path = frame.relative_path

    model_meta = annotation_export.model_meta
    date_created = annotation_export.date_created
    date_modified = annotation_export.date_modified
    timeline = _video_timeline_evidence(video)
    presentation_timestamp = frame.presentation_timestamp
    presentation_time_seconds = frame.timestamp
    time_base_num = (
        timeline.source.timeline.time_base_num if timeline is not None else None
    )
    time_base_den = (
        timeline.source.timeline.time_base_den if timeline is not None else None
    )
    state = getattr(video, "state", None)
    artifact_sha256_value = str(
        getattr(state, "processed_file_sha256", "") or ""
    ).strip()
    artifact_sha256 = artifact_sha256_value or None
    if export_profile == "pts_dataset_v1" and (
        presentation_timestamp is None
        or presentation_time_seconds is None
        or time_base_num is None
        or time_base_den is None
        or artifact_sha256 is None
    ):
        raise ValueError(
            f"Frame {frame.pk} has no complete pts_v1 timeline and processed-artifact identity"
        )

    return {
        "annotation_id": annotation_export.pk,
        "video_id": video.pk,
        "video_hash": video.video_hash,
        "frame_id": frame.pk,
        "frame_number": frame.frame_number,
        "frame_relative_path": frame_relative_path,
        "frame_timestamp": frame.timestamp,
        "presentation_timestamp": presentation_timestamp,
        "presentation_time_seconds": presentation_time_seconds,
        "stream_time_base_num": time_base_num,
        "stream_time_base_den": time_base_den,
        "timeline_version": timeline.timeline_version if timeline is not None else None,
        "export_frame_index": export_frame_index,
        "artifact_kind": "processed",
        "artifact_sha256": artifact_sha256,
        "label_id": label.pk,
        "label_name": label.name,
        "value": annotation_export.value,
        "float_value": annotation_export.float_value,
        "annotator": annotation_export.annotator,
        "information_source_id": information_source.pk,
        "information_source_name": information_source.name,
        "model_meta_id": model_meta.pk if model_meta is not None else None,
        "date_created": date_created.isoformat() if date_created is not None else None,
        "date_modified": date_modified.isoformat()
        if date_modified is not None
        else None,
    }


def _video_timeline_evidence(
    video: _VideoHashExportModel,
) -> VideoSourceTimelineEvidence | None:
    meta = video.meta
    if not isinstance(meta, dict):
        return None
    payload = meta.get("source_timeline")
    if payload is None:
        return None
    try:
        return VideoSourceTimelineEvidence.model_validate(payload)
    except ValueError as exc:
        raise ValueError(
            f"Video {video.pk} has invalid source timeline evidence"
        ) from exc


def _build_annotation_rows(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    export_profile: VideoFrameAnnotationExportProfile,
    use_frame_pk_paths: bool,
    frame_ext: str,
) -> list[AnnotationRow]:
    annotation_rows = list(annotations.iterator())
    annotation_rows.sort(key=_annotation_export_sort_key)
    frame_indices: dict[tuple[int, int], int] = {}
    next_index_by_video: dict[int, int] = {}
    rows: list[AnnotationRow] = []
    for annotation in annotation_rows:
        annotation_export = cast(_AnnotationExportModel, annotation)
        frame = annotation_export.frame
        video_id = int(frame.video.pk)
        frame_key = (video_id, int(frame.pk))
        export_frame_index = frame_indices.get(frame_key)
        if export_frame_index is None:
            export_frame_index = next_index_by_video.get(video_id, 0) + 1
            next_index_by_video[video_id] = export_frame_index
            frame_indices[frame_key] = export_frame_index
        rows.append(
            _annotation_to_row(
                annotation,
                use_frame_pk_paths=use_frame_pk_paths,
                frame_ext=frame_ext,
                export_frame_index=export_frame_index,
                export_profile=export_profile,
            )
        )
    return rows


def _annotation_export_sort_key(
    annotation: ImageClassificationAnnotation,
) -> tuple[int, int, int, int, int]:
    annotation_export = cast(_AnnotationExportModel, annotation)
    frame = annotation_export.frame
    timestamp_order = (
        frame.presentation_timestamp
        if frame.presentation_timestamp is not None
        else frame.frame_number
    )
    return (
        int(frame.video.pk),
        int(timestamp_order),
        int(frame.frame_number),
        int(annotation_export.label.pk),
        int(annotation_export.pk),
    )


"""
csv:
python manage.py export_frame_annot \
  --output-path data/export/frames.csv

  
  json:
  python manage.py export_frame_annot \
  --output-path data/export/frames.json \
  --format json


"""
