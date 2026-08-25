from __future__ import annotations

# Modern Media Framework: Sensitive Metadata Management

from collections.abc import Mapping
from datetime import date, datetime
from typing import Literal, Protocol, cast
import logging

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from pydantic import ValidationError as PydanticValidationError
from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts import (
    CaseResolutionRequest,
    CaseResolutionNewPatient,
    CaseResolutionResponse,
    ValidationError,
)

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.views.access_control import assert_center_scope_allowed
from endoreg_db.services.case_resolution_state import (
    get_case_resolution_meta,
    persist_case_resolution_state,
)
from endoreg_db.services.auto_case_resolution import link_media_to_patient_examination
from endoreg_db.services.report_materialization import (
    upsert_anonym_examination_report_from_pdf,
)
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.services.sensitive_meta_verification import (
    update_sensitive_meta_verification,
)
from endoreg_db.services.sensitive_meta_update import (
    SensitiveMetaUpdateCenterNotFoundError,
    SensitiveMetaUpdateGenderNotFoundError,
    update_sensitive_meta,
)
from endoreg_db.serializers.meta import (
    SensitiveMetaDetailSerializer,
    SensitiveMetaUpdateSerializer,
)
from endoreg_db.serializers.patient import PatientSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


class _PageNumberPaginatorLike(Protocol):
    page_size: int

    def paginate_queryset(
        self, queryset: object, request: Request
    ) -> list[SensitiveMeta] | None: ...

    def get_paginated_response(self, data: object) -> Response: ...


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_date_or_datetime(value: object) -> date | datetime | None:
    return value if isinstance(value, (date, datetime)) else None


def _as_isoformat(value: object) -> str | None:
    value_ = _as_date_or_datetime(value)
    return value_.isoformat() if value_ is not None else None


def _request_payload(request: Request) -> Mapping[str, JsonValue]:
    payload = cast(object, request.data)
    if isinstance(payload, Mapping):
        return cast(Mapping[str, JsonValue], payload)
    return {}


def _query_params(request: Request) -> Mapping[str, object]:
    return cast(
        Mapping[str, object], cast(object, getattr(request, "query_params", {}))
    )


def _serialize_response_data(serializer: object) -> object:
    return cast(object, getattr(serializer, "data", {}))


def _serialize_response_errors(serializer: object) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], getattr(serializer, "errors", {}))


def _get_object_field(
    value: object | None, field: str, default: object | None = None
) -> object | None:
    if value is None:
        return default
    return getattr(value, field, default)


def _get_int_field(value: object | None, field: str) -> int | None:
    return _as_int(_get_object_field(value, field))


def _get_str_field(value: object | None, field: str) -> str | None:
    return _as_str(_get_object_field(value, field))


def _get_fk_id(value: object | None, relation_field: str) -> int | None:
    relation = _get_object_field(value, relation_field)
    return _get_int_field(relation, "pk")


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _query_bool_param(params: Mapping[str, object], key: str) -> bool | None:
    return _as_bool(params.get(key, None))


def _query_str_param(
    params: Mapping[str, object], key: str, default: str | None = None
) -> str | None:
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _verify_sensitive_metadata(
    *,
    request: Request,
    sensitive_meta: SensitiveMeta,
    media_id_key: Literal["video_id", "pdf_id"],
    media_id: int,
) -> Response:
    try:
        command = SensitiveMetaVerificationCommand.model_validate(
            _request_payload(request)
        )
    except PydanticValidationError:
        return Response(
            {
                "error": "At least one of dob_verified or names_verified must be provided"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    sensitive_meta_id = _get_int_field(sensitive_meta, "pk")
    if sensitive_meta_id is None:
        return Response(
            {"error": "Sensitive metadata has no id"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    result = update_sensitive_meta_verification(
        sensitive_meta_id=sensitive_meta_id,
        command=command,
    )
    response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
    return Response(
        {
            "message": "Verification state updated successfully",
            "sensitive_meta": _serialize_response_data(response_serializer),
            media_id_key: media_id,
            "state_verified": result.is_verified,
        },
        status=status.HTTP_200_OK,
    )


def _update_sensitive_metadata(
    *,
    request: Request,
    sensitive_meta: SensitiveMeta,
    media_id_key: Literal["video_id", "pdf_id"],
    media_id: int,
) -> Response:
    serializer = SensitiveMetaUpdateSerializer(
        data=_request_payload(request), partial=True
    )
    if not serializer.is_valid():
        return Response(
            _serialize_response_errors(serializer),
            status=status.HTTP_400_BAD_REQUEST,
        )

    sensitive_meta_id = _get_int_field(sensitive_meta, "pk")
    if sensitive_meta_id is None:
        return Response(
            {"error": "Sensitive metadata has no id"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        result = update_sensitive_meta(
            sensitive_meta_id=sensitive_meta_id,
            command=serializer.to_command(),
        )
    except SensitiveMetaUpdateCenterNotFoundError as exc:
        return Response({"center_name": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
    except SensitiveMetaUpdateGenderNotFoundError as exc:
        return Response(
            {"patient_gender_name": [str(exc)]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    response_serializer = SensitiveMetaDetailSerializer(result.sensitive_meta)
    return Response(
        {
            "message": "Sensitive metadata updated successfully",
            "sensitive_meta": _serialize_response_data(response_serializer),
            media_id_key: media_id,
        },
        status=status.HTTP_200_OK,
    )


def _serialize_patient_examination_match(
    patient_examination: object,
) -> dict[str, object]:
    examination_name = None
    examination = _get_object_field(patient_examination, "examination")
    if examination is not None:
        examination_name = _get_str_field(examination, "name")
    return {
        "id": _get_int_field(patient_examination, "pk"),
        "patient_id": _get_int_field(patient_examination, "patient_id"),
        "examination_name": examination_name,
        "date_start": _as_isoformat(
            _get_object_field(patient_examination, "date_start")
        ),
        "date_end": _as_isoformat(_get_object_field(patient_examination, "date_end")),
        "hash": _get_str_field(patient_examination, "hash"),
    }


def _case_resolution_payload(
    *,
    media_type: Literal["video", "pdf"],
    media_pk: int,
    media_obj: RawPdfFile | VideoFile,
    sensitive_meta: SensitiveMeta,
    linked_patient_examination_id: int | None,
) -> dict[str, object]:
    patient_hash = _get_str_field(sensitive_meta, "patient_hash")
    examination_hash = _get_str_field(sensitive_meta, "examination_hash")
    case_resolution_meta = cast(
        Mapping[str, object], get_case_resolution_meta(media_obj)
    )
    explicit_linked_patient_examination_id = linked_patient_examination_id
    if not case_resolution_meta.get("is_explicitly_resolved"):
        explicit_linked_patient_examination_id = None
    auto_linked_patient_examination_id = case_resolution_meta.get(
        "linked_patient_examination_id"
    )
    if not isinstance(auto_linked_patient_examination_id, int):
        auto_linked_patient_examination_id = None
    resolved_linked_patient_examination_id = (
        explicit_linked_patient_examination_id
        or auto_linked_patient_examination_id
        or _get_int_field(media_obj, "examination_id")
    )

    examination_matches_qs = PatientExamination.objects.none()

    patient_matches_count = 0
    if patient_hash:
        patient_matches_count = Patient.objects.filter(
            patient_hash=patient_hash
        ).count()

    if examination_hash:
        examination_matches_qs = PatientExamination.objects.select_related(
            "patient", "examination"
        ).filter(hash=examination_hash)
    examination_matches = list(examination_matches_qs.order_by("-id"))
    examination_matches_count = len(examination_matches)
    is_explicitly_resolved = bool(case_resolution_meta.get("is_explicitly_resolved"))
    is_auto_resolved = bool(case_resolution_meta.get("is_auto_resolved"))
    is_deferred = bool(case_resolution_meta.get("deferred"))

    if resolved_linked_patient_examination_id is not None:
        match_status = "linked"
    elif is_deferred:
        match_status = "deferred"
    elif examination_matches_count > 0:
        match_status = "suggested"
    else:
        match_status = "unresolved"

    recommended_patient_examination_id = (
        _get_int_field(examination_matches[0], "pk")
        if examination_matches_count == 1
        else None
    )

    pseudo_patient_id = _get_int_field(sensitive_meta, "pseudo_patient_id")
    if pseudo_patient_id is None:
        pseudo_patient_id = _get_fk_id(sensitive_meta, "pseudo_patient")
    pseudo_examination_id = _get_int_field(sensitive_meta, "pseudo_examination_id")
    if pseudo_examination_id is None:
        pseudo_examination_id = _get_fk_id(sensitive_meta, "pseudo_examination")
    current_patient_examination_id = _get_int_field(media_obj, "examination_id")
    current_patient_id = _get_int_field(media_obj, "patient_id")
    linked_patient_id = (
        _get_int_field(case_resolution_meta, "linked_patient_id")
        if (is_explicitly_resolved or is_auto_resolved)
        else current_patient_id
    )

    return {
        "media_type": media_type,
        "media_id": media_pk,
        "sensitive_meta_id": _get_int_field(sensitive_meta, "pk"),
        "linked_patient_examination_id": resolved_linked_patient_examination_id,
        "linked_patient_id": linked_patient_id,
        "current_patient_examination_id": current_patient_examination_id,
        "current_patient_id": current_patient_id,
        "pseudo_patient_id": pseudo_patient_id,
        "pseudo_examination_id": pseudo_examination_id,
        "patient_hash_display": (
            f"...{patient_hash[-8:]}"
            if isinstance(patient_hash, str) and patient_hash
            else None
        ),
        "examination_hash_display": (
            f"...{examination_hash[-8:]}"
            if isinstance(examination_hash, str) and examination_hash
            else None
        ),
        "pseudo_patient": {
            "id": pseudo_patient_id,
            "match_count": patient_matches_count,
        },
        "pseudo_examination": {
            "id": pseudo_examination_id,
            "linked_patient_examination_id": resolved_linked_patient_examination_id,
        },
        "match_status": match_status,
        "is_explicitly_resolved": is_explicitly_resolved,
        "is_auto_resolved": is_auto_resolved,
        "is_deferred": is_deferred,
        "suggested_match_count": examination_matches_count,
        "recommended_patient_examination_id": recommended_patient_examination_id,
        "patient_examination_matches": [
            _serialize_patient_examination_match(patient_examination)
            for patient_examination in examination_matches
        ],
    }


def _build_case_resolution_write_response(
    *,
    action: Literal["attach", "create", "defer"],
    created: bool,
    media_type: Literal["video", "pdf"],
    media_pk: int,
    media_obj: RawPdfFile | VideoFile,
    patient_examination_id: int | None,
    patient_id: int | None,
    sensitive_meta: SensitiveMeta,
) -> dict[str, object]:
    response_payload = CaseResolutionResponse(
        media_type=media_type,
        media_id=media_pk,
        action=action,
        status="deferred" if action == "defer" else "linked",
        patient_examination_id=patient_examination_id,
        patient_id=patient_id,
        created=created,
    ).model_dump()
    response_payload["case_resolution"] = _case_resolution_payload(
        media_type=media_type,
        media_pk=media_pk,
        media_obj=media_obj,
        sensitive_meta=sensitive_meta,
        linked_patient_examination_id=patient_examination_id,
    )
    return response_payload


def _resolve_case_resolution_request(request: Request) -> CaseResolutionRequest:
    payload = _request_payload(request)
    return CaseResolutionRequest.model_validate(payload)


def _resolve_target_patient_examination(
    *, patient_examination_id: int
) -> PatientExamination:
    return get_object_or_404(
        PatientExamination.objects.select_related("patient", "examination"),
        pk=patient_examination_id,
    )


def _resolve_case_resolution_patient(
    *,
    patient_id: int | None,
    new_patient_payload: CaseResolutionNewPatient | None,
    sensitive_meta: SensitiveMeta,
) -> Patient:
    if patient_id is not None:
        return get_object_or_404(Patient, pk=patient_id)

    if new_patient_payload is not None:
        patient_payload = new_patient_payload.model_dump()
        patient_gender_name = _get_str_field(
            _get_object_field(sensitive_meta, "patient_gender"), "name"
        )
        if patient_payload.get("gender") is None and patient_gender_name is not None:
            patient_payload["gender"] = patient_gender_name
        center = _get_object_field(sensitive_meta, "center")
        center_key = _get_str_field(center, "center_key")
        if patient_payload.get("center_key") is None and center is not None:
            patient_payload["center_key"] = center_key
        patient_serializer = PatientSerializer(data=patient_payload)
        patient_serializer.is_valid(raise_exception=True)
        return patient_serializer.save()

    raise ValueError("patient_id or new_patient is required for create action")


def _resolve_case_resolution_examination(
    *, examination_name: str | None, sensitive_meta: SensitiveMeta
) -> Examination | None:
    if examination_name:
        return get_object_or_404(Examination, name=examination_name)

    pseudo_examination = _get_object_field(sensitive_meta, "pseudo_examination")
    if pseudo_examination is not None:
        examination = _get_object_field(pseudo_examination, "examination")
        if isinstance(examination, Examination):
            return examination
    return None


def _create_patient_examination_for_case_resolution(
    *, payload: CaseResolutionRequest, sensitive_meta: SensitiveMeta
) -> PatientExamination:
    patient = _resolve_case_resolution_patient(
        patient_id=payload.patient_id,
        new_patient_payload=payload.new_patient,
        sensitive_meta=sensitive_meta,
    )
    examination = _resolve_case_resolution_examination(
        examination_name=payload.examination_name,
        sensitive_meta=sensitive_meta,
    )

    examination_date = _get_object_field(sensitive_meta, "examination_date")
    if isinstance(examination_date, datetime):
        normalized_examination_date: date | None = examination_date.date()
    elif isinstance(examination_date, date):
        normalized_examination_date = examination_date
    else:
        normalized_examination_date = None

    return PatientExamination.objects.create(
        patient=patient,
        examination=examination,
        date_start=payload.date_start or normalized_examination_date,
        date_end=payload.date_end,
    )


def _handle_case_resolution_post(
    *,
    request: Request,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
) -> Response:
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(media_obj, "sensitive_meta")
    )
    if sensitive_meta is None:
        media_obj_pk = _get_int_field(media_obj, "pk")
        return Response(
            {"error": f"No sensitive metadata found for {media_type} {media_obj_pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )
    media_obj_pk = _get_int_field(media_obj, "pk")

    try:
        payload = _resolve_case_resolution_request(request)
    except ValidationError as exc:
        return Response(
            {"error": "Invalid case resolution payload", "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    created = False
    patient_examination = None

    try:
        with transaction.atomic():
            if payload.action == "attach":
                assert payload.patient_examination_id is not None
                patient_examination = _resolve_target_patient_examination(
                    patient_examination_id=payload.patient_examination_id
                )
                link_media_to_patient_examination(
                    media_type=media_type,
                    media_obj=media_obj,
                    patient_examination=patient_examination,
                )
                persist_case_resolution_state(
                    media_obj=media_obj,
                    payload=payload,
                    patient_examination_id=_get_int_field(
                        patient_examination,
                        "pk",
                    ),
                    patient_id=_get_int_field(
                        patient_examination,
                        "patient_id",
                    ),
                )
            elif payload.action == "create":
                patient_examination = _create_patient_examination_for_case_resolution(
                    payload=payload,
                    sensitive_meta=sensitive_meta,
                )
                created = True
                link_media_to_patient_examination(
                    media_type=media_type,
                    media_obj=media_obj,
                    patient_examination=patient_examination,
                )
                persist_case_resolution_state(
                    media_obj=media_obj,
                    payload=payload,
                    patient_examination_id=_get_int_field(patient_examination, "pk"),
                    patient_id=_get_int_field(patient_examination, "patient_id"),
                )
            else:
                if _get_int_field(media_obj, "examination_id") is not None:
                    raise ValueError(
                        "cannot defer case resolution for already linked media"
                    )
                patient_examination = cast(
                    PatientExamination | None,
                    _get_object_field(media_obj, "examination"),
                )
                persist_case_resolution_state(
                    media_obj=media_obj,
                    payload=payload,
                    patient_examination_id=None,
                    patient_id=None,
                )

            if media_type == "pdf" and payload.action in {"attach", "create"}:
                assert isinstance(media_obj, RawPdfFile)
                upsert_anonym_examination_report_from_pdf(
                    pdf=media_obj,
                    validated_at_iso=None,
                    source="case_resolution",
                    allow_empty_text=True,
                )
    except drf_serializers.ValidationError as exc:
        return Response(
            {"error": "Case resolution failed", "detail": exc.detail},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ValueError as exc:
        return Response(
            {"error": "Case resolution failed", "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        _build_case_resolution_write_response(
            action=payload.action,
            created=created,
            media_type=media_type,
            media_pk=cast(int, media_obj_pk),
            media_obj=media_obj,
            patient_examination_id=(
                _get_int_field(patient_examination, "pk")
                if patient_examination is not None
                else None
            ),
            patient_id=(
                _get_int_field(patient_examination, "patient_id")
                if patient_examination is not None
                else _get_int_field(media_obj, "patient_id")
            ),
            sensitive_meta=sensitive_meta,
        ),
        status=status.HTTP_200_OK,
    )


# === VIDEO SENSITIVE METADATA ===


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def get_sensitive_metadata_pk(request: Request, pk: int, media_type: str) -> Response:
    """
    A route to get the sensitive meta pk for a media type quickly.

    GET api/media/sensitive-media-id/<pk>/<str:media_type>

    Args:
        request (_type_): _description_
        id (_type_): _description_

    Returns:
        Response | None: _description_
    """

    if media_type == "video":
        video = get_object_or_404(VideoFile, pk=pk)
        assert_center_scope_allowed(request=request, obj=video)
        sensitive_meta = cast(
            SensitiveMeta | None, _get_object_field(video, "sensitive_meta")
        )
        if sensitive_meta is None:
            return Response(
                {"error": f"No sensitive metadata found for video {pk}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        sm_id = _get_int_field(sensitive_meta, "pk")
        if sm_id is None:
            return Response(
                {"error": f"Sensitive metadata for video {pk} has no id"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"sm": sm_id})
    if media_type == "pdf":
        pdf = get_object_or_404(RawPdfFile, pk=pk)
        assert_center_scope_allowed(request=request, obj=pdf)
        sensitive_meta = cast(
            SensitiveMeta | None, _get_object_field(pdf, "sensitive_meta")
        )
        if sensitive_meta is None:
            return Response(
                {"error": f"No sensitive metadata found for report {pk}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        sm_id = _get_int_field(sensitive_meta, "pk")
        if sm_id is None:
            return Response(
                {"error": f"Sensitive metadata for report {pk} has no id"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"sm": sm_id})
    return Response(
        {"error": f"Unsupported media_type '{media_type}'"},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def video_sensitive_metadata(request: Request, pk: int) -> Response:
    """
    GET /api/media/videos/<pk>/sensitive-metadata/
    PATCH /api/media/videos/<pk>/sensitive-metadata/

    Get or update sensitive metadata for a video.
    Video-scoped: Uses video ID to locate related sensitive metadata.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=video)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(video, "sensitive_meta")
    )
    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(_serialize_response_data(serializer), status=status.HTTP_200_OK)

    elif request.method == "PATCH":
        video_pk = _get_int_field(video, "pk")
        if video_pk is None:
            return Response(
                {"error": "Could not resolve video id"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _update_sensitive_metadata(
            request=request,
            sensitive_meta=sensitive_meta,
            media_id_key="video_id",
            media_id=video_pk,
        )

    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def video_case_resolution(request: Request, pk: int) -> Response:
    """
    GET /api/media/videos/<pk>/case-resolution/

    Return read-only case resolution hints for a validated or pending video.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=video)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(video, "sensitive_meta")
    )
    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "POST":
        return _handle_case_resolution_post(
            request=request,
            media_type="video",
            media_obj=video,
        )

    return Response(
        _case_resolution_payload(
            media_type="video",
            media_pk=cast(int, _get_int_field(video, "pk")),
            media_obj=video,
            sensitive_meta=sensitive_meta,
            linked_patient_examination_id=_get_int_field(video, "examination_id"),
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def video_sensitive_metadata_verify(request: Request, pk: int) -> Response:
    """
    POST /api/media/videos/<pk>/sensitive-metadata/verify/

    Update verification state for video sensitive metadata.

    Expected payload:
    {
        "dob_verified": true,
        "names_verified": true
    }
    """
    video = get_object_or_404(VideoFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=video)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(video, "sensitive_meta")
    )

    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return _verify_sensitive_metadata(
        request=request,
        sensitive_meta=sensitive_meta,
        media_id_key="video_id",
        media_id=pk,
    )


# === report SENSITIVE METADATA ===


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_sensitive_metadata(request: Request, pk: int) -> Response:
    """
    GET /api/media/pdfs/<pk>/sensitive-metadata/
    PATCH /api/media/pdfs/<pk>/sensitive-metadata/

    Get or update sensitive metadata for a report.
    report-scoped: Uses report ID to locate related sensitive metadata.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=pdf)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(pdf, "sensitive_meta")
    )
    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for report {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(_serialize_response_data(serializer), status=status.HTTP_200_OK)

    elif request.method == "PATCH":
        pdf_pk = _get_int_field(pdf, "pk")
        if pdf_pk is None:
            return Response(
                {"error": "Could not resolve report id"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _update_sensitive_metadata(
            request=request,
            sensitive_meta=sensitive_meta,
            media_id_key="pdf_id",
            media_id=pdf_pk,
        )

    return Response(
        {"error": f"Method {request.method} not allowed"},
        status=status.HTTP_405_METHOD_NOT_ALLOWED,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_case_resolution(request: Request, pk: int) -> Response:
    """
    GET /api/media/pdfs/<pk>/case-resolution/

    Return read-only case resolution hints for a validated or pending PDF.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=pdf)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(pdf, "sensitive_meta")
    )
    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for report {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "POST":
        return _handle_case_resolution_post(
            request=request,
            media_type="pdf",
            media_obj=pdf,
        )

    return Response(
        _case_resolution_payload(
            media_type="pdf",
            media_pk=cast(int, _get_int_field(pdf, "pk")),
            media_obj=pdf,
            sensitive_meta=sensitive_meta,
            linked_patient_examination_id=_get_int_field(pdf, "examination_id"),
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_sensitive_metadata_verify(request: Request, pk: int) -> Response:
    """
    POST /api/media/pdfs/<pk>/sensitive-metadata/verify/

    Update verification state for report sensitive metadata.

    Expected payload:
    {
        "dob_verified": true,
        "names_verified": true
    }
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    assert_center_scope_allowed(request=request, obj=pdf)
    sensitive_meta = cast(
        SensitiveMeta | None, _get_object_field(pdf, "sensitive_meta")
    )

    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for report {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return _verify_sensitive_metadata(
        request=request,
        sensitive_meta=sensitive_meta,
        media_id_key="pdf_id",
        media_id=pk,
    )


# === LIST ENDPOINTS (Collection-Level) ===


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def sensitive_metadata_list(request: Request) -> Response:
    """
    GET /api/media/sensitive-metadata/

    List all sensitive metadata (combined reports and Videos).
    Supports filtering by content_type, status, etc.

    Query parameters:
    - content_type: 'pdf' | 'video' (optional)
    - verified: Filter by verification status
    - ordering: Sort field
    - search: Search in patient names
    """
    # Get all sensitive metadata
    queryset = SensitiveMeta.objects.select_related("state").all()
    query_params = _query_params(request)

    # Filter by content type
    content_type = _query_str_param(query_params, "content_type")
    if content_type == "pdf":
        # Only reports - filter by existence of related reports
        queryset = queryset.filter(raw_pdf_files__isnull=False).distinct()
    elif content_type == "video":
        # Only Videos - filter by existence of related video
        queryset = queryset.filter(video_file__isnull=False).distinct()

    # Filter by verification status
    verified = _query_bool_param(query_params, "verified")
    if verified is not None:
        queryset = queryset.filter(state__is_verified=verified)

    # Search in patient names
    search = _query_str_param(query_params, "search")
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search)
            | Q(patient_last_name__icontains=search)
        )

    # Ordering
    ordering = _query_str_param(query_params, "ordering", "-id")
    if ordering is None:
        ordering = "-id"
    queryset = queryset.order_by(ordering)

    # Pagination
    from rest_framework.pagination import PageNumberPagination

    paginator = cast(_PageNumberPaginatorLike, PageNumberPagination())
    paginator.page_size = 20
    page = paginator.paginate_queryset(queryset, request)

    if page is not None:
        serializer = SensitiveMetaDetailSerializer(page, many=True)
        serialized_data = _serialize_response_data(serializer)
        return paginator.get_paginated_response(serialized_data)

    serializer = SensitiveMetaDetailSerializer(queryset, many=True)
    return Response(_serialize_response_data(serializer), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_sensitive_metadata_list(request: Request) -> Response:
    """
    GET /api/media/pdfs/sensitive-metadata/

    List sensitive metadata for reports only.
    Replaces legacy /api/pdf/sensitivemeta/list/
    """
    # Get all reports with sensitive metadata
    queryset = (
        SensitiveMeta.objects.select_related("state")
        .filter(raw_pdf_files__isnull=False)
        .distinct()
    )
    query_params = _query_params(request)

    # Apply filters
    search = _query_str_param(query_params, "search")
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search)
            | Q(patient_last_name__icontains=search)
        )

    ordering = _query_str_param(query_params, "ordering", "-id")
    if ordering is None:
        ordering = "-id"
    queryset = queryset.order_by(ordering)

    # Pagination
    from rest_framework.pagination import PageNumberPagination

    paginator = cast(_PageNumberPaginatorLike, PageNumberPagination())
    paginator.page_size = 20
    page = paginator.paginate_queryset(queryset, request)

    if page is not None:
        serializer = SensitiveMetaDetailSerializer(page, many=True)
        serialized_data = _serialize_response_data(serializer)
        return paginator.get_paginated_response(serialized_data)

    serializer = SensitiveMetaDetailSerializer(queryset, many=True)
    return Response(_serialize_response_data(serializer), status=status.HTTP_200_OK)
