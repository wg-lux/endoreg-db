from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import NoneType
from typing import Literal, Protocol, TypedDict, cast

from django.db.models import Q, QuerySet
from endoreg_db.utils.ffmpeg_wrapper import (
    extract_frame_range as ffmpeg_extract_frame_range,
    extract_frames as ffmpeg_extract_frames,
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
from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
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
    local_plaintext_path_from_name,
    maybe_local_plaintext_path,
)

logger = logging.getLogger(__name__)

type Null = NoneType
type ConfigScalar = str | int | float | bool | Null
type ExportConfigFieldValue = Path | str | int | float | bool | list[int] | Null
type AnnotationCell = str | int | float | bool | Null
type AnnotationFieldName = Literal[
    "annotation_id",
    "video_id",
    "video_hash",
    "frame_id",
    "frame_number",
    "frame_relative_path",
    "frame_timestamp",
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
    video_id: int | Null
    video_hash: str | Null
    frame_id: int | Null
    frame_number: int | Null
    frame_relative_path: str | Null
    frame_timestamp: float | Null
    label_name: str | Null
    label_id: int | Null
    value: bool | Null
    float_value: float | Null
    annotator: str | Null
    information_source_id: int | Null
    information_source_name: str | Null
    model_meta_id: int | Null
    date_created: str | Null
    date_modified: str | Null


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
    timestamp: float
    file_path: str
    video: VideoFile


class _FrameQuerySetLike(Protocol):
    def only(self, *fields: str) -> Iterable[_FrameExportModel]: ...


class _VideoFramesExportModel(Protocol):
    pk: int
    frames: _FrameQuerySetLike


class _VideoHashExportModel(Protocol):
    pk: int
    video_hash: str | Null


class _AnnotationExportModel(Protocol):
    pk: int
    frame: _FrameExportModel
    label: _LabelExportModel
    value: bool | Null
    float_value: float | Null
    annotator: str | Null
    information_source: _InformationSourceExportModel | Null
    model_meta: _ModelMetaExportModel | Null
    date_created: datetime | Null
    date_modified: datetime | Null


class _LabelVideoSegmentExportModel(Protocol):
    pk: int
    label: _LabelExportModel | Null
    video_file: VideoFile
    start_frame_number: int
    end_frame_number: int
    source_id: int | Null

    def get_model_meta(self) -> _ModelMetaExportModel | Null: ...


class _FrameValueRow(TypedDict):
    frame__video_id: int | Null
    frame_id: int | Null


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


def _config_optional_bool(value: ConfigScalar) -> bool | Null:
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


def _resolve_processed_video_source_path(video: VideoFile) -> Path | Null:
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
    def __init__(self, message: str, *, original_error: Exception | Null = None):
        super().__init__(message)
        self.original_error = original_error


def _normalize_export_config(config: export_config) -> export_config:
    updates: dict[str, ExportConfigFieldValue] = {
        "load_base_data": _config_bool(config.load_base_data),
        "export_videos": _config_bool(config.export_videos),
        "export_frames": _config_bool(config.export_frames, default=True),
        "transcode_frames": _config_bool(config.transcode_frames),
        "transcode_overwrite": _config_bool(config.transcode_overwrite),
        "use_export_flags": _config_bool(config.use_export_flags, default=True),
        "all_centers": _config_bool(config.all_centers),
        "only_validated": _config_bool(config.only_validated, default=True),
    }

    updates["only_true"] = _config_optional_bool(config.only_true)
    center_key = str(config.center_key or "").strip() or None
    updates["center_key"] = center_key

    if local_study_server_mode_enabled() and _config_bool(config.export_frames):
        # Local study server frame-image exports are generated from processed media
        # at export time. Reusing older frame files would not prove anonymized source.
        updates["transcode_frames"] = True
        updates["transcode_overwrite"] = True
        updates["use_frame_pk_paths"] = True

    if updates:
        return replace(config, **updates)
    return config


class annotation_exporter_client:
    def __init__(self, *, logger: logging.Logger | Null = None) -> None:
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
    annotations: QuerySet[ImageClassificationAnnotation] | Null = None,
    video_id: int | Null = None,
    label_id: int | Null = None,
    information_source_name: str | Null = None,
    only_true: bool | Null = None,
    limit: int | Null = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = True,
    use_export_flags: bool = True,
    segment_ids: list[int] | Null = None,
    center_key: str | Null = None,
    all_centers: bool = False,
    only_validated: bool = True,
    transcode_frames: bool = False,
    transcode_fps: float = DEFAULT_TRANSCODE_FPS,
    transcode_quality: int = DEFAULT_TRANSCODE_QUALITY,
    transcode_ext: str = DEFAULT_TRANSCODE_EXT,
    transcode_overwrite: bool = False,
    use_frame_pk_paths: bool | Null = None,
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

    rows: list[AnnotationRow] = [
        _annotation_to_row(
            annotation,
            use_frame_pk_paths=use_frame_pk_paths,
            frame_ext=transcode_ext,
        )
        for annotation in annotations.iterator()
    ]
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
    annotations: QuerySet[ImageClassificationAnnotation] | Null = None,
    video_id: int | Null = None,
    label_id: int | Null = None,
    information_source_name: str | Null = None,
    only_true: bool | Null = None,
    limit: int | Null = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = True,
    use_export_flags: bool = True,
    segment_ids: list[int] | Null = None,
    center_key: str | Null = None,
    all_centers: bool = False,
    only_validated: bool = True,
    transcode_frames: bool = False,
    transcode_fps: float = DEFAULT_TRANSCODE_FPS,
    transcode_quality: int = DEFAULT_TRANSCODE_QUALITY,
    transcode_ext: str = DEFAULT_TRANSCODE_EXT,
    transcode_overwrite: bool = False,
    use_frame_pk_paths: bool | Null = None,
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

    rows: list[AnnotationRow] = []
    for annotation in annotations.iterator():
        rows.append(
            _annotation_to_row(
                annotation,
                use_frame_pk_paths=use_frame_pk_paths,
                frame_ext=transcode_ext,
            )
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
    video_output_dir: Path | Null
    frame_output_dir: Path | Null


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
    use_frame_pk_paths: bool | Null,
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
    output_dir: Path | Null,
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
    output_dir: Path | Null,
    use_frame_pk_paths: bool,
    frame_ext: str,
    strict_media_validation: bool = True,
    generated_frame_root: Path | Null = None,
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
    generated_frame_root: Path | Null = None,
) -> Path | Null:
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
    export_frame_root: Path | Null = None,
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
    frame_pks: set[int] | Null,
    fps: float,
    quality: int,
    ext: str,
    overwrite: bool,
    export_frame_root: Path | Null = None,
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
    source_path: Path,
    frame_dir: Path,
    frame_pks: set[int] | Null,
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
        frames_by_pk = {
            int(frame.pk): int(frame.frame_number)
            for frame in cast(_VideoFramesExportModel, video).frames.only(
                "pk", "frame_number"
            )
        }
        requested_numbers = sorted(
            frames_by_pk[frame_pk]
            for frame_pk in frame_pks or frames_by_pk.keys()
            if frame_pk in frames_by_pk
        )
        if frame_pks is not None and len(requested_numbers) != len(frame_pks):
            missing_pks = sorted(frame_pks - frames_by_pk.keys())
            raise ValueError(
                f"Requested annotation frames are missing for video {video.pk}: "
                f"{missing_pks[:10]}"
            )
        if not requested_numbers:
            return
        if frame_pks is None:
            extracted_paths = ffmpeg_extract_frames(
                source_path,
                tmp_dir,
                quality=quality,
                ext=ext,
                fps=None,
            )
        else:
            extracted_paths = ffmpeg_extract_frame_range(
                source_path,
                tmp_dir,
                start_frame=requested_numbers[0],
                end_frame=requested_numbers[-1] + 1,
                quality=quality,
                ext=ext,
            )
        moved_frame_pks = _move_extracted_frames_to_pk_names(
            video,
            extracted_paths,
            frame_dir,
            frame_pks=frame_pks,
            ext=ext,
            overwrite=overwrite,
        )
        missing_outputs = sorted((frame_pks or set(frames_by_pk)) - moved_frame_pks)
        if missing_outputs:
            raise RuntimeError(
                f"Identity-preserving extraction did not produce requested frames "
                f"for video {video.pk}: {missing_outputs[:10]}"
            )
    finally:
        safe_rmtree(tmp_dir, missing_ok=True)


def _move_extracted_frames_to_pk_names(
    video: VideoFile,
    extracted_paths: list[Path],
    frame_dir: Path,
    *,
    frame_pks: set[int] | Null,
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


def _parse_extracted_frame_number(frame_path: Path) -> int | Null:
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
    video_id: int | Null,
    label_id: int | Null,
    information_source_name: str | Null,
    only_true: bool | Null,
    use_export_flags: bool = False,
    segment_ids: list[int] | Null = None,
    center_key: str | Null = None,
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
    video_id: int | Null,
    segment_ids: list[int] | Null,
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
            seg_filter &= Q(information_source__isnull=True)
        else:
            seg_filter &= Q(information_source_id=segment.source_id)

        model_meta = segment.get_model_meta()
        if model_meta is None:
            seg_filter &= Q(model_meta__isnull=True)
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

    return {
        "annotation_id": annotation_export.pk,
        "video_id": video.pk,
        "video_hash": video.video_hash,
        "frame_id": frame.pk,
        "frame_number": frame.frame_number,
        "frame_relative_path": frame_relative_path,
        "frame_timestamp": frame.timestamp,
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


"""
csv:
python manage.py export_frame_annot \
  --output-path data/export/frames.csv

  
  json:
  python manage.py export_frame_annot \
  --output-path data/export/frames.json \
  --format json


"""
