from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import Center, LabelVideoSegment, VideoFile
from endoreg_db.models.state.video_segment_validation import (
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.export.frames.export_frames_with_labels import (
    annotation_exporter_client,
    export_config,
    export_job_failed_error,
)
from endoreg_db.services.hub import (
    local_study_server_mode_enabled,
    resolve_allowed_center_id,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

_BOOLEAN_PAYLOAD_KEYS = {
    "only_true",
    "load_base_data",
    "export_videos",
    "export_frames",
    "use_export_flags",
    "all_centers",
    "only_validated",
    "transcode_frames",
    "transcode_overwrite",
    "use_frame_pk_paths",
}


def _payload_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _api_export_scope_error(request, config: export_config) -> tuple[str, int] | None:
    center_key = str(config.center_key or "").strip()
    all_centers = bool(config.all_centers)
    local_study_server = local_study_server_mode_enabled()
    user = getattr(request, "user", None)
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
        if allowed_center_id == -1:
            return "You do not have access to export center data.", 403
        if allowed_center_id is not None and center.id != allowed_center_id:
            return "Export center is outside the authenticated scope.", 403

    return None


def _scoped_video_queryset(queryset, config: export_config):
    center_key = str(config.center_key or "").strip()
    if center_key:
        return queryset.filter(center__center_key=center_key)
    return queryset


def _scoped_segment_queryset(queryset, config: export_config):
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
            for video_id in segment_queryset.values_list("video_file_id", flat=True)
            if video_id is not None
        )
    elif config.use_export_flags:
        flagged_videos = _scoped_video_queryset(
            VideoFile.objects.filter(export_segments_by_video=True),
            config,
        )
        video_ids.update(
            int(video_id) for video_id in flagged_videos.values_list("pk", flat=True)
        )

        flagged_segments = _scoped_segment_queryset(
            LabelVideoSegment.objects.filter(export_segment=True),
            config,
        )
        video_ids.update(
            int(video_id)
            for video_id in flagged_segments.values_list("video_file_id", flat=True)
            if video_id is not None
        )

    return video_ids


def _api_segment_cleanup_error(config: export_config) -> tuple[str, int] | None:
    video_ids = _selected_video_ids_for_cleanup_preflight(config)
    if not video_ids:
        return None

    for video in VideoFile.objects.select_related("state").filter(pk__in=video_ids):
        if segment_annotations_are_final(video):
            continue
        segment_status = resolve_segment_annotation_status(video)
        return (
            f"Video {video.pk} segment cleanup is not complete: {segment_status}.",
            409,
        )

    return None


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def export_annotated_data(request):
    payload: dict[str, Any] = request.data or {}
    config_path = payload.get("config_path")
    if config_path:
        try:
            config = export_config.from_yaml(config_path)
        except (FileNotFoundError, ValueError) as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        output_dir = payload.get("output_dir")
        output_path = payload.get("output_path")
        if not output_path:
            output_path = "frames.csv" if output_dir else "data/export/frames.csv"
        config = export_config(output_path=Path(output_path))

    if payload.get("output_format"):
        config = replace(config, output_format=payload["output_format"])
    elif payload.get("format"):
        config = replace(config, output_format=payload["format"])

    updates: dict[str, Any] = {}
    for key in (
        "output_path",
        "output_dir",
        "video_id",
        "label_id",
        "information_source_name",
        "only_true",
        "limit",
        "load_base_data",
        "export_videos",
        "export_frames",
        "use_export_flags",
        "center_key",
        "all_centers",
        "only_validated",
        "segment_ids",
        "transcode_frames",
        "transcode_fps",
        "transcode_quality",
        "transcode_ext",
        "transcode_overwrite",
        "use_frame_pk_paths",
    ):
        if key in payload and payload[key] is not None:
            if key in _BOOLEAN_PAYLOAD_KEYS:
                updates[key] = _payload_bool(payload[key])
            elif key == "center_key":
                updates[key] = str(payload[key]).strip() or None
            else:
                updates[key] = payload[key]

    if not config_path:
        if "export_frames" not in updates:
            updates["export_frames"] = True
        if "use_export_flags" not in updates and "segment_ids" not in updates:
            updates["use_export_flags"] = True
        if "export_videos" not in updates:
            updates["export_videos"] = False

    if updates:
        config = replace(config, **updates)

    scope_error = _api_export_scope_error(request, config)
    if scope_error is not None:
        error_message, status_code = scope_error
        return Response({"success": False, "error": error_message}, status=status_code)

    cleanup_error = _api_segment_cleanup_error(config)
    if cleanup_error is not None:
        error_message, status_code = cleanup_error
        return Response({"success": False, "error": error_message}, status=status_code)

    client = annotation_exporter_client()
    try:
        result = client.run_export(config)
    except export_job_failed_error as exc:
        return Response(
            {"success": False, "error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "success": result.success,
            "output_path": str(result.output_path),
            "row_count": result.row_count,
            "exported_video_count": result.exported_video_count,
            "exported_frame_count": result.exported_frame_count,
            "video_output_dir": (
                str(result.video_output_dir)
                if result.video_output_dir is not None
                else None
            ),
            "frame_output_dir": (
                str(result.frame_output_dir)
                if result.frame_output_dir is not None
                else None
            ),
        },
        status=status.HTTP_200_OK,
    )
