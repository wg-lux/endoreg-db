"""HTTP adapter for browser-started frame annotation dataset export jobs.

Request bodies are the lx_dtypes ``VideoFrameAnnotationExportConfigPayload``
contract; scope, path and lifecycle rules live in
``endoreg_db.services.media.frame_annotation_export_jobs``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from lx_dtypes.models.contracts.json_types import JsonValue
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.video.frame_annotation_export_job import (
    FrameAnnotationExportJob,
)
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.media.export_annotated import ExportConflictError
from endoreg_db.services.media.frame_annotation_export_jobs import (
    create_frame_annotation_export_job,
    frame_annotation_export_scope_options,
    prepare_frame_annotation_export,
    recover_stale_frame_annotation_export_jobs,
    visible_frame_annotation_export_jobs,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

FRAME_ANNOTATION_EXPORT_JOB_LIST_LIMIT = 20


class FrameAnnotationExportJobResultSerializer(
    serializers.Serializer[dict[str, object]]
):
    output_path = serializers.CharField()
    row_count = serializers.IntegerField()
    exported_video_count = serializers.IntegerField()
    exported_frame_count = serializers.IntegerField()
    video_output_dir = serializers.CharField(allow_null=True)
    frame_output_dir = serializers.CharField(allow_null=True)


class FrameAnnotationExportJobSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=FrameAnnotationExportJob.Status.values)
    center_key = serializers.CharField(allow_null=True)
    all_centers = serializers.BooleanField()
    output_dir = serializers.CharField()
    config = serializers.DictField()
    result = FrameAnnotationExportJobResultSerializer(allow_null=True)
    error = serializers.CharField(allow_blank=True)
    requested_by = serializers.CharField()
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)


class FrameAnnotationExportCenterOptionSerializer(
    serializers.Serializer[dict[str, object]]
):
    center_key = serializers.CharField()
    name = serializers.CharField()


class FrameAnnotationExportJobListSerializer(serializers.Serializer[dict[str, object]]):
    results = FrameAnnotationExportJobSerializer(many=True)
    center_options = FrameAnnotationExportCenterOptionSerializer(many=True)
    all_centers_allowed = serializers.BooleanField()


class FrameAnnotationExportValidationSerializer(
    serializers.Serializer[dict[str, object]]
):
    output_dir = serializers.CharField()
    center_key = serializers.CharField(allow_null=True)
    all_centers = serializers.BooleanField()
    config = serializers.DictField()


def _job_payload(job: FrameAnnotationExportJob) -> dict[str, object]:
    return {
        "id": job.pk,
        "status": job.status,
        "center_key": job.center.center_key if job.center is not None else None,
        "all_centers": job.all_centers,
        "output_dir": job.output_dir,
        "config": job.config,
        "result": job.result,
        "error": job.error,
        "requested_by": job.requested_by.get_username(),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _request_payload(request: Request) -> Mapping[str, JsonValue]:
    data = cast(object, request.data)
    if not isinstance(data, Mapping):
        raise DjangoValidationError("Request body must be a JSON object.")
    return cast(Mapping[str, JsonValue], data)


def _authenticated_user(request: Request) -> User:
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        raise NotAuthenticated("Export jobs require a signed-in user.")
    return user


def _rejected(exc: PydanticValidationError | DjangoValidationError) -> Response:
    errors: object = (
        exc.errors(include_context=False, include_url=False)
        if isinstance(exc, PydanticValidationError)
        else exc.messages
    )
    return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)


def _jobs(request: Request) -> QuerySet[FrameAnnotationExportJob]:
    return visible_frame_annotation_export_jobs(
        _authenticated_user(request)
    ).select_related("requested_by")


class FrameAnnotationExportJobListView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    serializer_class = FrameAnnotationExportJobListSerializer

    def get(self, request: Request) -> Response:
        jobs = _jobs(request)
        recover_stale_frame_annotation_export_jobs()
        options = frame_annotation_export_scope_options(_authenticated_user(request))
        return Response(
            {
                "results": [
                    _job_payload(job)
                    for job in jobs[:FRAME_ANNOTATION_EXPORT_JOB_LIST_LIMIT]
                ],
                "center_options": [
                    {"center_key": center.center_key, "name": center.name}
                    for center in options.centers
                ],
                "all_centers_allowed": options.all_centers_allowed,
            }
        )


class FrameAnnotationExportJobDetailView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    serializer_class = FrameAnnotationExportJobSerializer

    def get(self, request: Request, job_id: UUID) -> Response:
        recover_stale_frame_annotation_export_jobs()
        job = _jobs(request).filter(pk=job_id).first()
        if job is None:
            raise NotFound("Export job not found.")
        return Response(_job_payload(job))


class FrameAnnotationExportJobStartView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    serializer_class = FrameAnnotationExportJobSerializer

    def post(self, request: Request) -> Response:
        user = _authenticated_user(request)
        try:
            job = create_frame_annotation_export_job(
                _request_payload(request), user=user
            )
        except (PydanticValidationError, DjangoValidationError) as exc:
            return _rejected(exc)
        except ExportConflictError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        job.refresh_from_db()
        return Response(_job_payload(job), status=status.HTTP_202_ACCEPTED)


class FrameAnnotationExportJobValidateView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    serializer_class = FrameAnnotationExportValidationSerializer

    def post(self, request: Request) -> Response:
        user = _authenticated_user(request)
        try:
            prepared = prepare_frame_annotation_export(
                _request_payload(request), user=user
            )
        except (PydanticValidationError, DjangoValidationError) as exc:
            return _rejected(exc)
        return Response(
            {
                "output_dir": prepared.run_name,
                "center_key": (
                    prepared.center.center_key if prepared.center is not None else None
                ),
                "all_centers": prepared.payload.all_centers,
                "config": prepared.payload.model_dump(mode="json", exclude_none=True),
            }
        )
