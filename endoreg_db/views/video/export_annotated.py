# endoreg_db/views/video/export_annotated.py

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import (
    PermissionDenied,
    ValidationError as DjangoValidationError,
)
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.export.frames.export_frames_with_labels import export_job_failed_error
from endoreg_db.services.export_annotated import (
    ExportAnnotatedService,
    ExportConflictError,
)
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from lx_dtypes.models.contracts.json_types import JsonValue


def _request_payload(data: object) -> Mapping[str, JsonValue]:
    if isinstance(data, Mapping):
        return cast(Mapping[str, JsonValue], data)
    return {}


def _export_result_payload(result: object) -> dict[str, Any]:
    return {
        "success": getattr(result, "success"),
        "output_path": str(getattr(result, "output_path")),
        "row_count": getattr(result, "row_count"),
        "exported_video_count": getattr(result, "exported_video_count"),
        "exported_frame_count": getattr(result, "exported_frame_count"),
        "video_output_dir": (
            str(getattr(result, "video_output_dir"))
            if getattr(result, "video_output_dir") is not None
            else None
        ),
        "frame_output_dir": (
            str(getattr(result, "frame_output_dir"))
            if getattr(result, "frame_output_dir") is not None
            else None
        ),
    }


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def export_annotated(request: Request) -> Response:
    payload = _request_payload(request.data)
    service = ExportAnnotatedService.default()

    try:
        result = service.run_api_export(
            payload=payload,
            user=cast(User | AnonymousUser, request.user),
        )
    except PydanticValidationError as exc:
        return Response(
            {"errors": exc.errors(include_context=False)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except DjangoValidationError as exc:
        return Response(
            {"errors": exc.message_dict if hasattr(exc, "message_dict") else str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except PermissionDenied as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_403_FORBIDDEN,
        )
    except ExportConflictError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_409_CONFLICT,
        )
    except FileNotFoundError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except export_job_failed_error as exc:
        original = getattr(exc, "original_error", None)
        return Response(
            {
                "error": str(exc),
                "detail": str(original) if original is not None else "",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        _export_result_payload(result),
        status=status.HTTP_200_OK,
    )
