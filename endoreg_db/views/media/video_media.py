"""
Video Media Management View (Phase 1.2)

Provides CRUD-like operations for video files including listing, detail
retrieval, and metadata management for the media management system.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol, TypeAlias, cast
from urllib.parse import urlencode

from django.db import models
from django.db.models import Prefetch, Q
from django.db.models.query import QuerySet
from django.http import Http404
from lx_dtypes.models.contracts.video_file import VideoFilePayload
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.serializers.video.video_file_detail import VideoDetailSerializer
from endoreg_db.serializers.video.video_file_list import VideoFileListSerializer

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _SerializerLike(Protocol):
    @property
    def data(self) -> JsonValue: ...


def _query_params(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.query_params)


def _serialize_response_data(serializer: _SerializerLike) -> JsonValue:
    return serializer.data


def _query_str_param(params: Mapping[str, str], key: str, default: str = "") -> str:
    return params.get(key, default)


def _query_int_param(params: Mapping[str, str], key: str, default: int) -> int:
    raw_value = params.get(key)
    if raw_value in ("", None):
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _query_bool_param(params: Mapping[str, str], key: str) -> bool | None:
    raw_value = params.get(key)
    if raw_value is None:
        return None
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return None


def _video_contract_payload(video: VideoFile) -> VideoFilePayload:
    """
    Validate stable scalar VideoFile fields through the shared lx_dtypes contract.

    The endpoint still returns the existing serializer payload; this contract
    call keeps the view aligned with the cross-package VideoFile boundary.
    """
    return VideoFilePayload.model_validate(
        {
            "pk": video.pk,
            "id": video.pk,
            "video_hash": str(video.video_hash),
            "original_file_name": getattr(video, "original_file_name", None),
            "fps": getattr(video, "fps", None),
            "duration": getattr(video, "duration", None),
            "frame_count": getattr(video, "frame_count", None),
            "width": getattr(video, "width", None),
            "height": getattr(video, "height", None),
            "suffix": getattr(video, "suffix", None),
            "frame_dir": getattr(video, "frame_dir", ""),
            "storage_mode": getattr(video, "storage_mode", ""),
            "raw_streamable_relative_path": getattr(
                video,
                "raw_streamable_relative_path",
                "",
            ),
            "processed_streamable_relative_path": getattr(
                video,
                "processed_streamable_relative_path",
                "",
            ),
            "has_raw": bool(getattr(video, "raw_file", None)),
            "is_processed": bool(getattr(video, "processed_file", None)),
            "uploaded_at": getattr(video, "uploaded_at", None),
            "date_created": getattr(video, "date_created", None),
            "date_modified": getattr(video, "date_modified", None),
            "meta": getattr(video, "meta", None),
        }
    )


class VideoMediaView(APIView):
    """
    Video media management API for listing and detail operations.
    """

    permission_classes = [IsAuthenticated, PolicyPermission]

    def get(self, request: Request, pk: int | None = None) -> Response:
        if pk is not None:
            return self._get_video_detail(request=request, pk=pk)
        return self._list_videos(request)

    def patch(self, request: Request, pk: int | None = None) -> Response:
        if pk is None:
            return Response(
                {"error": "Video ID is required for update."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            video: VideoFile = VideoFile.objects.get(pk=pk)
        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")

        payload = cast(Mapping[str, JsonValue], request.data)
        raw_export_flag = payload.get("export_segments_by_video")
        if isinstance(raw_export_flag, bool):
            video.export_segments_by_video = raw_export_flag
            models.Model.save(video, update_fields=["export_segments_by_video"])
            serializer = VideoDetailSerializer(video, context={"request": request})
            return Response(
                _serialize_response_data(cast(_SerializerLike, serializer)),
                status=status.HTTP_200_OK,
            )

        return Response(
            {"error": "No supported fields provided for update."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _get_video_detail(self, request: Request, pk: int) -> Response:
        try:
            video = VideoFile.objects.select_related("state", "sensitive_meta").get(
                pk=pk
            )
            _video_contract_payload(video)
            serializer = VideoDetailSerializer(video, context={"request": request})
            return Response(_serialize_response_data(cast(_SerializerLike, serializer)))
        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")
        except Exception as exc:
            logger.error(
                "Unexpected error in video detail view for ID %s: %s",
                pk,
                exc,
            )
            return Response(
                {"error": "Failed to retrieve video details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _list_videos(self, request: Request) -> Response:
        try:
            segments_queryset: QuerySet[LabelVideoSegment] = (
                LabelVideoSegment.objects.select_related(
                    "label",
                    "video_file",
                ).order_by("start_frame_number")
            )
            segments_prefetch: Prefetch[
                str,
                QuerySet[LabelVideoSegment],
                str,
            ] = Prefetch(
                "label_video_segments",
                queryset=segments_queryset,
            )
            queryset: QuerySet[VideoFile] = (
                VideoFile.objects.select_related("state", "sensitive_meta")
                .prefetch_related(segments_prefetch)
                .all()
            )

            query_params = _query_params(request)
            queryset = self._apply_filters(queryset, query_params)

            search = _query_str_param(query_params, "search", "").strip()
            if search:
                queryset = queryset.filter(Q(original_file_name__icontains=search))

            queryset = queryset.order_by("-uploaded_at")

            limit = min(_query_int_param(query_params, "limit", 50), 100)
            offset = _query_int_param(query_params, "offset", 0)
            total_count = queryset.count()
            videos = list(queryset[offset : offset + limit])

            for video in videos:
                _video_contract_payload(video)

            serializer = VideoFileListSerializer(videos, many=True)
            serialized_data = _serialize_response_data(
                cast(_SerializerLike, serializer)
            )
            include_unresolved = _query_bool_param(query_params, "include_unresolved")
            return Response(
                {
                    "count": total_count,
                    "next": self._get_next_url(request, offset, limit, total_count),
                    "previous": self._get_previous_url(request, offset, limit),
                    "results": serialized_data,
                    "include_unresolved": bool(include_unresolved),
                }
            )

        except ValueError as exc:
            return Response(
                {"error": f"Invalid query parameter: {str(exc)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.error("Unexpected error in video list view: %s", exc)
            return Response(
                {"error": "Failed to retrieve video list"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _apply_filters(
        self,
        queryset: QuerySet[VideoFile],
        query_params: Mapping[str, str],
    ) -> QuerySet[VideoFile]:
        status_filter = _query_str_param(query_params, "status", "").strip().lower()
        failed_query = Q(state__processing_error=True) | Q(
            meta__integrity_status="lost"
        )

        if status_filter == "not_started":
            queryset = queryset.filter(
                Q(state__isnull=True)
                | Q(
                    state__frames_extracted=False,
                    state__sensitive_meta_processed=False,
                )
            ).exclude(failed_query)
        elif status_filter == "processing":
            queryset = queryset.filter(
                state__frames_extracted=True,
                state__sensitive_meta_processed=False,
            ).exclude(failed_query)
        elif status_filter == "done_processing_anonymization":
            queryset = queryset.filter(
                state__anonymized=True,
                sensitive_meta__is_verified=False,
            ).exclude(failed_query)
        elif status_filter == "validated":
            queryset = queryset.filter(
                state__anonymized=True,
                sensitive_meta__is_verified=True,
            ).exclude(failed_query)
        elif status_filter == "failed":
            queryset = queryset.filter(failed_query)

        return queryset

    def _get_next_url(
        self,
        request: Request,
        offset: int,
        limit: int,
        total_count: int,
    ) -> str | None:
        if offset + limit >= total_count:
            return None
        return self._build_paginated_url(request, offset + limit, limit)

    def _get_previous_url(
        self,
        request: Request,
        offset: int,
        limit: int,
    ) -> str | None:
        if offset <= 0:
            return None
        return self._build_paginated_url(request, max(0, offset - limit), limit)

    def _build_paginated_url(self, request: Request, offset: int, limit: int) -> str:
        params: dict[str, str] = dict(_query_params(request))
        params["offset"] = str(offset)
        params["limit"] = str(limit)

        base_url = request.build_absolute_uri(request.path)
        return f"{base_url}?{urlencode(params)}"
