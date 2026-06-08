from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from django.db.models import QuerySet
from pydantic import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.export.frames.export_frames_with_labels import (
    annotation_exporter_client,
    export_config,
    export_job_failed_error,
    export_result,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.video_segment_validation import (
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.schemas import (
    VideoAnnotationExportConfigUpdateData,
    VideoAnnotationExportErrorPayload,
    VideoAnnotationExportRequestPayload,
    VideoAnnotationExportResultPayload,
    dump_video_annotation_export_update_payload,
)
from endoreg_db.services.hub import (
    local_study_server_mode_enabled,
    resolve_allowed_center_id,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission


def _error_response(message: str, status_code: int) -> Response[dict[str, object]]:
    payload = VideoAnnotationExportErrorPayload(error=message)
    return Response(payload.model_dump(mode="json"), status=status_code)


def _request_payload_data(request: Request) -> Mapping[str, object]:
    payload = cast(object, request.data)
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("Request payload must be a JSON object.")
    return cast(Mapping[str, object], payload)


def _api_export_scope_error(
    request: Request, config: export_config
) -> tuple[str, int] | None:
    center_key = str(config.center_key or "").strip()
    all_centers = bool(config.all_centers)
    local_study_server = local_study_server_mode_enabled()
    user = cast(Any, request.user)
    authenticated = bool(user and getattr(user, "is_authenticated", False))
    privileged = bool(
        authenticated
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )

    if local_study_server and not authenticated:
        return "Authentication is required for local_study_server exports.", 403
    if center_key and all_centers:
        return "Export scope must use center_key or all_centers, not both.", 400
    if local_study_server and not (bool(center_key) ^ all_centers):
        return (
            "local_study_server exports require exactly one center scope: "
            "center_key or all_centers.",
            400,
        )
    if all_centers and local_study_server and not privileged:
        return "all_centers export requires staff or superuser privileges.", 403
    if local_study_server and not bool(config.only_validated):
        return "local_study_server exports require only_validated=true.", 400

    if center_key:
        center = Center.objects.filter(center_key=center_key).first()
        if center is None:
            return f"Unknown center_key: {center_key}", 400
        allowed_center_id = resolve_allowed_center_id(user)
        center_id = cast(int, center.pk)
        if allowed_center_id == -1:
            return "You do not have access to export center data.", 403
        if allowed_center_id is not None and center_id != allowed_center_id:
            return "Export center is outside the authenticated scope.", 403

    return None


def _scoped_video_queryset(
    queryset: QuerySet[VideoFile], config: export_config
) -> QuerySet[VideoFile]:
    center_key = str(config.center_key or "").strip()
    if center_key:
        return queryset.filter(center__center_key=center_key)
    return queryset


def _scoped_segment_queryset(
    queryset: QuerySet[LabelVideoSegment], config: export_config
) -> QuerySet[LabelVideoSegment]:
    center_key = str(config.center_key or "").strip()
    if center_key:
        return queryset.filter(video_file__center__center_key=center_key)
    return queryset


def _selected_video_ids_for_cleanup_preflight(config: export_config) -> set[int]:
    video_ids: set[int] = set()
    if config.video_id is not None:
        video_ids.add(int(config.video_id))

    if config.segment_ids:
        segment_queryset = LabelVideoSegment.objects.filter(pk__in=config.segment_ids)
        segment_queryset = _scoped_segment_queryset(segment_queryset, config)
        video_ids.update(
            int(video_id)
            for video_id in cast(
                Iterable[int | None],
                segment_queryset.values_list("video_file_id", flat=True),
            )
            if video_id is not None
        )
    elif config.use_export_flags:
        flagged_videos = _scoped_video_queryset(
            VideoFile.objects.filter(export_segments_by_video=True),
            config,
        )
        video_ids.update(
            int(video_id)
            for video_id in cast(
                Iterable[int],
                flagged_videos.values_list("pk", flat=True),
            )
        )

        flagged_segments = _scoped_segment_queryset(
            LabelVideoSegment.objects.filter(export_segment=True),
            config,
        )
        video_ids.update(
            int(video_id)
            for video_id in cast(
                Iterable[int | None],
                flagged_segments.values_list("video_file_id", flat=True),
            )
            if video_id is not None
        )

    return video_ids


def _api_segment_cleanup_error(config: export_config) -> tuple[str, int] | None:
    video_ids = _selected_video_ids_for_cleanup_preflight(config)
    if not video_ids:
        return None

    videos = cast(
        Iterable[VideoFile],
        VideoFile.objects.select_related("state").filter(pk__in=video_ids),
    )
    for video in videos:
        if segment_annotations_are_final(video):
            continue
        segment_status = resolve_segment_annotation_status(video)
        return (
            f"Video {video.pk} segment cleanup is not complete: {segment_status}.",
            409,
        )

    return None


def _apply_api_default_updates(
    updates: VideoAnnotationExportConfigUpdateData,
) -> VideoAnnotationExportConfigUpdateData:
    if "export_frames" not in updates:
        updates["export_frames"] = True
    if "use_export_flags" not in updates and "segment_ids" not in updates:
        updates["use_export_flags"] = True
    if "export_videos" not in updates:
        updates["export_videos"] = False
    return updates


def _apply_export_config_updates(
    config: export_config,
    updates: VideoAnnotationExportConfigUpdateData,
) -> export_config:
    if "output_path" in updates:
        config = replace(config, output_path=updates["output_path"])
    if "output_dir" in updates:
        config = replace(config, output_dir=updates["output_dir"])
    if "output_format" in updates:
        config = replace(config, output_format=updates["output_format"])
    if "video_id" in updates:
        config = replace(config, video_id=updates["video_id"])
    if "label_id" in updates:
        config = replace(config, label_id=updates["label_id"])
    if "information_source_name" in updates:
        config = replace(
            config,
            information_source_name=updates["information_source_name"],
        )
    if "only_true" in updates:
        config = replace(config, only_true=updates["only_true"])
    if "limit" in updates:
        config = replace(config, limit=updates["limit"])
    if "load_base_data" in updates:
        config = replace(config, load_base_data=updates["load_base_data"])
    if "export_videos" in updates:
        config = replace(config, export_videos=updates["export_videos"])
    if "export_frames" in updates:
        config = replace(config, export_frames=updates["export_frames"])
    if "transcode_frames" in updates:
        config = replace(config, transcode_frames=updates["transcode_frames"])
    if "transcode_fps" in updates:
        config = replace(config, transcode_fps=updates["transcode_fps"])
    if "transcode_quality" in updates:
        config = replace(config, transcode_quality=updates["transcode_quality"])
    if "transcode_ext" in updates:
        config = replace(config, transcode_ext=updates["transcode_ext"])
    if "transcode_overwrite" in updates:
        config = replace(config, transcode_overwrite=updates["transcode_overwrite"])
    if "use_frame_pk_paths" in updates:
        config = replace(config, use_frame_pk_paths=updates["use_frame_pk_paths"])
    if "use_export_flags" in updates:
        config = replace(config, use_export_flags=updates["use_export_flags"])
    if "segment_ids" in updates:
        config = replace(config, segment_ids=updates["segment_ids"])
    if "center_key" in updates:
        config = replace(config, center_key=updates["center_key"])
    if "all_centers" in updates:
        config = replace(config, all_centers=updates["all_centers"])
    if "only_validated" in updates:
        config = replace(config, only_validated=updates["only_validated"])
    return config


def _success_payload(result: export_result) -> VideoAnnotationExportResultPayload:
    return VideoAnnotationExportResultPayload(
        success=result.success,
        output_path=str(result.output_path),
        row_count=result.row_count,
        exported_video_count=result.exported_video_count,
        exported_frame_count=result.exported_frame_count,
        video_output_dir=(
            str(result.video_output_dir)
            if result.video_output_dir is not None
            else None
        ),
        frame_output_dir=(
            str(result.frame_output_dir)
            if result.frame_output_dir is not None
            else None
        ),
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def export_annotated_data(request: Request) -> Response[dict[str, object]]:
    try:
        payload = VideoAnnotationExportRequestPayload.model_validate(
            _request_payload_data(request)
        )
    except (ValidationError, ValueError) as exc:
        return _error_response(
            f"Invalid export request payload: {exc}",
            status.HTTP_400_BAD_REQUEST,
        )

    config_path = payload.config_path
    if config_path:
        try:
            config = export_config.from_yaml(config_path)
        except (FileNotFoundError, ValueError) as exc:
            return _error_response(str(exc), status.HTTP_400_BAD_REQUEST)
    else:
        output_dir = payload.output_dir
        output_path = payload.output_path
        if not output_path:
            output_path = "frames.csv" if output_dir else "data/export/frames.csv"
        config = export_config(output_path=Path(output_path))

    updates = dump_video_annotation_export_update_payload(payload)
    if not config_path:
        updates = _apply_api_default_updates(updates)
    config = _apply_export_config_updates(config, updates)

    scope_error = _api_export_scope_error(request, config)
    if scope_error is not None:
        error_message, status_code = scope_error
        return _error_response(error_message, status_code)

    cleanup_error = _api_segment_cleanup_error(config)
    if cleanup_error is not None:
        error_message, status_code = cleanup_error
        return _error_response(error_message, status_code)

    client = annotation_exporter_client()
    try:
        result = client.run_export(config)
    except export_job_failed_error as exc:
        return _error_response(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)

    response_payload = _success_payload(result)
    return Response(
        response_payload.model_dump(mode="json"),
        status=status.HTTP_200_OK,
    )
