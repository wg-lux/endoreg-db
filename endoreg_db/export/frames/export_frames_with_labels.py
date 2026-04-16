from __future__ import annotations

import csv
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast, Literal

import yaml
import json
from django.db.models import Q, QuerySet

from endoreg_db.helpers.data_load_orchestrator import load_base_db_data
from endoreg_db.models import (
    ImageClassificationAnnotation,
    LabelVideoSegment,
    VideoFile,
    Frame,
)
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.video.ffmpeg_wrapper import (
    extract_frames as ffmpeg_extract_frames,
)

logger = logging.getLogger(__name__)

DEFAULT_FIELDNAMES = (
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
    video_id: int | None
    video_hash: str | None
    frame_id: int | None
    frame_number: int | None
    frame_relative_path: str | None
    frame_timestamp: float | None
    label: int
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


DEFAULT_TRANSCODE_FPS = 50.0
DEFAULT_TRANSCODE_QUALITY = 2
DEFAULT_TRANSCODE_EXT = "jpg"


def _resolve_video_source_path(video: VideoFile) -> Path | None:
    """
    Prefer the on-disk path resolution used by streaming (STORAGE_DIR + FileField.name).
    Fall back to storage-backed downloads (ensure_local_file) elsewhere.
    """
    try:
        field_file = video.active_file
    except Exception:
        return None

    name = getattr(field_file, "name", None)
    if not name:
        return None

    path = Path(str(name))
    if not path.is_absolute():
        path = STORAGE_DIR / str(name)

    return path if path.exists() else None


@dataclass(frozen=True, slots=True)
class export_config:
    output_path: Path | str
    output_dir: Path | str | None = None
    output_format: Literal["csv", "json"] = "csv"
    video_id: int | None = None
    label_id: int | None = None
    information_source_name: str | None = None
    only_true: bool | None = None
    limit: int | None = None
    load_base_data: bool = False
    export_videos: bool = False
    export_frames: bool = False
    transcode_frames: bool = False
    transcode_fps: float = DEFAULT_TRANSCODE_FPS
    transcode_quality: int = DEFAULT_TRANSCODE_QUALITY
    transcode_ext: str = DEFAULT_TRANSCODE_EXT
    transcode_overwrite: bool = False
    use_frame_pk_paths: bool | None = None
    use_export_flags: bool = False
    segment_ids: list[int] | None = None

    @classmethod
    def from_yaml(cls, config_path: Path | str) -> "export_config":
        config_data = load_export_config(config_path)
        output_path = config_data.get("output_path")
        if not output_path:
            raise ValueError("export config must include output_path")
        return cls(
            output_path=output_path,
            output_dir=config_data.get("output_dir"),
            output_format=config_data.get("output_format", "csv"),
            video_id=config_data.get("video_id"),
            label_id=config_data.get("label_id"),
            information_source_name=config_data.get("information_source_name"),
            only_true=config_data.get("only_true"),
            limit=config_data.get("limit"),
            load_base_data=config_data.get("load_base_data", False),
            export_videos=config_data.get("export_videos", False),
            export_frames=config_data.get("export_frames", False),
            transcode_frames=config_data.get("transcode_frames", False),
            transcode_fps=config_data.get("transcode_fps", DEFAULT_TRANSCODE_FPS),
            transcode_quality=config_data.get(
                "transcode_quality", DEFAULT_TRANSCODE_QUALITY
            ),
            transcode_ext=config_data.get("transcode_ext", DEFAULT_TRANSCODE_EXT),
            transcode_overwrite=config_data.get("transcode_overwrite", False),
            use_frame_pk_paths=config_data.get("use_frame_pk_paths"),
            use_export_flags=config_data.get("use_export_flags", False),
            segment_ids=config_data.get("segment_ids"),
        )


@dataclass(frozen=True, slots=True)
class export_result:
    output_path: Path
    row_count: int
    success: bool
    exported_video_count: int = 0
    exported_frame_count: int = 0
    video_output_dir: Path | None = None
    frame_output_dir: Path | None = None


class export_job_failed_error(RuntimeError):
    def __init__(self, message: str, *, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class annotation_exporter_client:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def run_export(self, config: export_config) -> export_result:
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
                    transcode_frames=config.transcode_frames,
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
                    transcode_frames=config.transcode_frames,
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


def load_export_config(config_path: Path | str) -> dict[str, Any]:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"config file not found: {config_file}")
    config_data = yaml.safe_load(config_file.read_text()) or {}
    if not isinstance(config_data, dict):
        raise ValueError("export config must be a mapping")
    return config_data


def export_frames_with_labels_from_yaml(config_path: Path | str) -> Path:
    config_data = load_export_config(config_path)
    output_path = config_data.get("output_path")
    if not output_path:
        raise ValueError("export config must include output_path")

    return export_frames_with_labels_to_csv(
        output_path=output_path,
        video_id=config_data.get("video_id"),
        label_id=config_data.get("label_id"),
        information_source_name=config_data.get("information_source_name"),
        only_true=config_data.get("only_true"),
        limit=config_data.get("limit"),
        load_base_data=config_data.get("load_base_data", False),
        export_videos=config_data.get("export_videos", False),
        export_frames=config_data.get("export_frames", False),
        use_export_flags=config_data.get("use_export_flags", False),
        segment_ids=config_data.get("segment_ids"),
        transcode_frames=config_data.get("transcode_frames", False),
        transcode_fps=config_data.get("transcode_fps", DEFAULT_TRANSCODE_FPS),
        transcode_quality=config_data.get(
            "transcode_quality", DEFAULT_TRANSCODE_QUALITY
        ),
        transcode_ext=config_data.get("transcode_ext", DEFAULT_TRANSCODE_EXT),
        transcode_overwrite=config_data.get("transcode_overwrite", False),
        use_frame_pk_paths=config_data.get("use_frame_pk_paths"),
    )


def export_frames_with_labels_to_csv(
    output_path: Path | str,
    *,
    annotations: QuerySet[ImageClassificationAnnotation] | None = None,
    video_id: int | None = None,
    label_id: int | None = None,
    information_source_name: str | None = None,
    only_true: bool | None = None,
    limit: int | None = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = False,
    use_export_flags: bool = False,
    segment_ids: list[int] | None = None,
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
    output_file.parent.mkdir(parents=True, exist_ok=True)

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
    )
    if limit is not None:
        annotations = annotations[:limit]

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames

    if transcode_frames:
        transcode_videos_for_annotations(
            annotations,
            fps=transcode_fps,
            quality=transcode_quality,
            ext=transcode_ext,
            overwrite=transcode_overwrite,
        )

    with output_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DEFAULT_FIELDNAMES)
        writer.writeheader()
        for annotation in annotations.iterator():
            writer.writerow(
                _annotation_to_row(
                    annotation,
                    use_frame_pk_paths=use_frame_pk_paths,
                    frame_ext=transcode_ext,
                )
            )

    return output_file


def export_frames_with_labels_to_json(
    output_path: Path | str,
    *,
    annotations: QuerySet[ImageClassificationAnnotation] | None = None,
    video_id: int | None = None,
    label_id: int | None = None,
    information_source_name: str | None = None,
    only_true: bool | None = None,
    limit: int | None = None,
    load_base_data: bool = False,
    export_videos: bool = False,
    export_frames: bool = False,
    use_export_flags: bool = False,
    segment_ids: list[int] | None = None,
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
    output_file.parent.mkdir(parents=True, exist_ok=True)

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
    )

    if limit is not None:
        annotations = annotations[:limit]

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames

    if transcode_frames:
        transcode_videos_for_annotations(
            annotations,
            fps=transcode_fps,
            quality=transcode_quality,
            ext=transcode_ext,
            overwrite=transcode_overwrite,
        )

    rows = []
    for annotation in annotations.iterator():
        rows.append(
            _annotation_to_row(
                annotation,
                use_frame_pk_paths=use_frame_pk_paths,
                frame_ext=transcode_ext,
            )
        )

    output_file.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
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
) -> ExportAssetResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    video_output_dir = output_dir / "videos" if export_videos else None
    frame_output_dir = output_dir / "frames" if export_frames else None

    if export_videos and video_output_dir:
        video_output_dir.mkdir(parents=True, exist_ok=True)
    if export_frames and frame_output_dir:
        frame_output_dir.mkdir(parents=True, exist_ok=True)

    if use_frame_pk_paths is None:
        use_frame_pk_paths = transcode_frames

    exported_video_count = 0
    exported_frame_count = 0

    if export_videos:
        exported_video_count = _export_videos_from_annotations(
            annotations,
            output_dir=video_output_dir,
        )

    if export_frames:
        exported_frame_count = _export_frames_from_annotations(
            annotations,
            output_dir=frame_output_dir,
            use_frame_pk_paths=use_frame_pk_paths,
            frame_ext=transcode_ext,
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
            source_path = _resolve_video_source_path(video)
            if source_path is not None:
                suffix = source_path.suffix or ".mp4"
                video_hash = video.video_hash or "unknown"
                target_path = output_dir / f"video_{video.pk}_{video_hash}{suffix}"
                try:
                    shutil.copy2(source_path, target_path)
                    exported_count += 1
                    continue
                except (OSError, shutil.Error) as exc:
                    logger.warning(
                        "Failed to export video %s from %s: %s",
                        video.pk,
                        source_path,
                        exc,
                    )

            # Fallback: attempt to download via Django storage backend.
            with ensure_local_file(video.active_file) as source_path_fallback:
                suffix = source_path_fallback.suffix or ".mp4"
                video_hash = video.video_hash or "unknown"
                target_path = output_dir / f"video_{video.pk}_{video_hash}{suffix}"
                try:
                    shutil.copy2(source_path_fallback, target_path)
                    exported_count += 1
                except (OSError, shutil.Error) as exc:
                    logger.warning(
                        "Failed to export video %s from %s: %s",
                        video.pk,
                        source_path_fallback,
                        exc,
                    )
        except (ValueError, FileNotFoundError, IOError) as exc:
            logger.warning("Skipping video %s: %s", video.pk, exc)

    return exported_count


def _export_frames_from_annotations(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    output_dir: Path | None,
    use_frame_pk_paths: bool,
    frame_ext: str,
) -> int:
    if output_dir is None:
        return 0

    exported_count = 0
    copied_frames: set[tuple[int, str]] = set()

    for annotation in annotations.iterator():
        frame = annotation.frame
        if frame is None:
            continue
        video = frame.video
        if video is None:
            continue

        frame_relative_path = (
            _frame_pk_filename(frame.pk, frame_ext)
            if use_frame_pk_paths
            else frame.relative_path
        )
        if not frame_relative_path:
            continue

        frame_key = (video.pk, frame_relative_path)
        if frame_key in copied_frames:
            continue

        source_path = _resolve_frame_source_path(
            frame,
            frame_relative_path=frame_relative_path,
            use_frame_pk_paths=use_frame_pk_paths,
            frame_ext=frame_ext,
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
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, target_path)
            exported_count += 1
            copied_frames.add(frame_key)
        except (OSError, shutil.Error) as exc:
            logger.warning(
                "Failed to export frame %s from %s: %s",
                frame.pk,
                source_path,
                exc,
            )

    return exported_count


def _resolve_frame_source_path(
    frame: "Frame",
    *,
    frame_relative_path: str,
    use_frame_pk_paths: bool,
    frame_ext: str,
) -> Path | None:
    video = frame.video
    if not video:
        return None

    if use_frame_pk_paths:
        frame_dir = video.get_frame_dir_path()
        if frame_dir is None:
            return None
        pk_path = frame_dir / frame_relative_path
        if pk_path.exists():
            return pk_path

    return frame.file_path


def transcode_videos_for_annotations(
    annotations: QuerySet[ImageClassificationAnnotation],
    *,
    fps: float = DEFAULT_TRANSCODE_FPS,
    quality: int = DEFAULT_TRANSCODE_QUALITY,
    ext: str = DEFAULT_TRANSCODE_EXT,
    overwrite: bool = False,
) -> None:
    frame_rows = annotations.values("frame__video_id", "frame_id")
    video_frame_pks: dict[int, set[int]] = {}
    for row in frame_rows:
        video_id = row["frame__video_id"]
        frame_id = row["frame_id"]
        if not video_id or not frame_id:
            continue
        video_frame_pks.setdefault(video_id, set()).add(frame_id)

    if not video_frame_pks:
        return

    videos = VideoFile.objects.filter(pk__in=video_frame_pks.keys())
    for video in videos:
        _transcode_video_to_frame_dir(
            video,
            frame_pks=video_frame_pks.get(video.pk),
            fps=fps,
            quality=quality,
            ext=ext,
            overwrite=overwrite,
        )


def _transcode_video_to_frame_dir(
    video: VideoFile,
    *,
    frame_pks: set[int] | None,
    fps: float,
    quality: int,
    ext: str,
    overwrite: bool,
) -> None:
    frame_dir = video.get_frame_dir_path()
    if not frame_dir:
        raise ValueError(f"frame dir not available for video {video.pk}")

    frame_dir.mkdir(parents=True, exist_ok=True)

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
        source_path = _resolve_video_source_path(video)
        if source_path is None:
            with ensure_local_file(video.active_file) as source_path_fallback:
                source_path = source_path_fallback
                with tempfile.TemporaryDirectory(
                    prefix="transcode_tmp_", dir=frame_dir
                ) as tmp:
                    tmp_dir = Path(tmp)
                    extracted_paths = ffmpeg_extract_frames(
                        source_path,
                        tmp_dir,
                        quality=quality,
                        ext=ext,
                        fps=fps,
                    )
                    _move_extracted_frames_to_pk_names(
                        video,
                        extracted_paths,
                        frame_dir,
                        frame_pks=frame_pks,
                        ext=ext,
                        overwrite=overwrite,
                    )
                return

        with tempfile.TemporaryDirectory(prefix="transcode_tmp_", dir=frame_dir) as tmp:
            tmp_dir = Path(tmp)
            extracted_paths = ffmpeg_extract_frames(
                source_path,
                tmp_dir,
                quality=quality,
                ext=ext,
                fps=fps,
            )
            _move_extracted_frames_to_pk_names(
                video,
                extracted_paths,
                frame_dir,
                frame_pks=frame_pks,
                ext=ext,
                overwrite=overwrite,
            )
    except (ValueError, FileNotFoundError, IOError) as exc:
        raise ValueError(f"active video path missing for {video.pk}: {exc}") from exc


def _move_extracted_frames_to_pk_names(
    video: VideoFile,
    extracted_paths: list[Path],
    frame_dir: Path,
    *,
    frame_pks: set[int] | None,
    ext: str,
    overwrite: bool,
) -> None:
    frames_by_number = {
        frame.frame_number: frame for frame in video.frames.only("pk", "frame_number")
    }
    if not frames_by_number:
        logger.warning("No frames available for video %s", video.pk)
        return

    for extracted_path in sorted(extracted_paths):
        frame_number = _parse_extracted_frame_number(extracted_path)
        if frame_number is None:
            extracted_path.unlink(missing_ok=True)
            continue

        frame = frames_by_number.get(frame_number)
        if frame is None:
            frame = frames_by_number.get(frame_number - 1)
        if frame is None:
            extracted_path.unlink(missing_ok=True)
            continue

        if frame_pks is not None and frame.pk not in frame_pks:
            extracted_path.unlink(missing_ok=True)
            continue

        target_path = frame_dir / _frame_pk_filename(frame.pk, ext)
        if target_path.exists() and not overwrite:
            extracted_path.unlink(missing_ok=True)
            continue

        extracted_path.replace(target_path)


def _parse_extracted_frame_number(frame_path: Path) -> int | None:
    try:
        return int(frame_path.stem.split("_")[-1])
    except (ValueError, IndexError):
        return None


def _frame_pk_filename(frame_pk: int, ext: str) -> str:
    return f"frame_{frame_pk}.{ext}"


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
) -> QuerySet[ImageClassificationAnnotation]:
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
    for segment in segment_list:
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
    frame = annotation.frame
    video = frame.video if frame else None
    information_source = annotation.information_source

    if information_source is None:
        raise ValueError("Information Source is None.")

    frame_relative_path = None
    if frame:
        if use_frame_pk_paths:
            frame_relative_path = _frame_pk_filename(frame.pk, frame_ext)
        else:
            frame_relative_path = frame.relative_path

    return cast(
        AnnotationRow,
        {
            "annotation_id": annotation.pk,
            "video_id": video.pk if video else None,
            "video_hash": video.video_hash if video else None,
            "frame_id": frame.pk if frame else None,
            "frame_number": frame.frame_number if frame else None,
            "frame_relative_path": frame_relative_path,
            "frame_timestamp": frame.timestamp if frame else None,
            "label_id": annotation.label.pk,
            "label_name": annotation.label.name if annotation.label else None,
            "value": annotation.value,
            "float_value": annotation.float_value,
            "annotator": annotation.annotator,
            "information_source_id": information_source.pk
            if information_source
            else None,
            "information_source_name": (
                information_source.name if information_source else None
            ),
            "model_meta_id": annotation.model_meta.pk
            if annotation.model_meta
            else None,
            "date_created": (
                annotation.date_created.isoformat() if annotation.date_created else None
            ),
            "date_modified": (
                annotation.date_modified.isoformat()
                if annotation.date_modified
                else None
            ),
        },
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
