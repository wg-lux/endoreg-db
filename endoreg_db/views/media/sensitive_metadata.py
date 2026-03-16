from __future__ import annotations

# Modern Media Framework: Sensitive Metadata Management

from typing import Literal, cast

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
import json
import logging

from rest_framework import status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from lx_dtypes.models.contracts import (
    CaseResolutionRequest,
    CaseResolutionResponse,
    ValidationError,
)

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import (
    Examination,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.services.case_resolution_state import (
    get_case_resolution_meta,
    persist_case_resolution_state,
)
from endoreg_db.services.auto_case_resolution import link_media_to_patient_examination
from endoreg_db.services.report_materialization import (
    upsert_anonym_examination_report_from_pdf,
)
from endoreg_db.serializers.meta import (
    SensitiveMetaDetailSerializer,
    SensitiveMetaUpdateSerializer,
)
from endoreg_db.serializers.patient import PatientSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


def _serialize_patient_examination_match(
    patient_examination: PatientExamination,
) -> dict[str, object]:
    examination_name = None
    examination = getattr(patient_examination, "examination", None)
    if examination is not None:
        examination_name = examination.name
    return {
        "id": patient_examination.pk,
        "patient_id": patient_examination.patient_id,
        "examination_name": examination_name,
        "date_start": patient_examination.date_start.isoformat()
        if patient_examination.date_start
        else None,
        "date_end": patient_examination.date_end.isoformat()
        if patient_examination.date_end
        else None,
        "hash": patient_examination.hash,
    }


def _case_resolution_payload(
    *,
    media_type: Literal["video", "pdf"],
    media_pk: int,
    media_obj: RawPdfFile | VideoFile,
    sensitive_meta: SensitiveMeta,
    linked_patient_examination_id: int | None,
) -> dict[str, object]:
    patient_hash = getattr(sensitive_meta, "patient_hash", None)
    examination_hash = getattr(sensitive_meta, "examination_hash", None)
    case_resolution_meta = get_case_resolution_meta(media_obj)
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
        or getattr(media_obj, "examination_id", None)
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
        examination_matches[0].pk if examination_matches_count == 1 else None
    )

    return {
        "media_type": media_type,
        "media_id": media_pk,
        "sensitive_meta_id": sensitive_meta.pk,
        "linked_patient_examination_id": resolved_linked_patient_examination_id,
        "linked_patient_id": (
            case_resolution_meta.get("linked_patient_id")
            if (is_explicitly_resolved or is_auto_resolved)
            else getattr(media_obj, "patient_id", None)
        ),
        "current_patient_examination_id": getattr(media_obj, "examination_id", None),
        "current_patient_id": getattr(media_obj, "patient_id", None),
        "pseudo_patient_id": sensitive_meta.pseudo_patient_id,
        "pseudo_examination_id": sensitive_meta.pseudo_examination_id,
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
            "id": sensitive_meta.pseudo_patient_id,
            "match_count": patient_matches_count,
        },
        "pseudo_examination": {
            "id": sensitive_meta.pseudo_examination_id,
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


def _resolve_case_resolution_request(request) -> CaseResolutionRequest:
    payload = request.data or {}
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
    new_patient_payload,
    sensitive_meta: SensitiveMeta,
) -> Patient:
    if patient_id is not None:
        return get_object_or_404(Patient, pk=patient_id)

    if new_patient_payload is not None:
        patient_payload = new_patient_payload.model_dump()
        if (
            patient_payload.get("gender") is None
            and sensitive_meta.patient_gender is not None
        ):
            patient_payload["gender"] = sensitive_meta.patient_gender.name
        if patient_payload.get("center") is None and sensitive_meta.center is not None:
            patient_payload["center"] = sensitive_meta.center.name
        patient_serializer = PatientSerializer(data=patient_payload)
        patient_serializer.is_valid(raise_exception=True)
        return patient_serializer.save()

    raise ValueError("patient_id or new_patient is required for create action")


def _resolve_case_resolution_examination(
    *, examination_name: str | None, sensitive_meta: SensitiveMeta
) -> Examination | None:
    if examination_name:
        return get_object_or_404(Examination, name=examination_name)

    pseudo_examination = sensitive_meta.pseudo_examination
    if pseudo_examination is not None:
        return pseudo_examination.examination
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

    return PatientExamination.objects.create(
        patient=patient,
        examination=examination,
        date_start=payload.date_start or sensitive_meta.examination_date,
        date_end=payload.date_end,
    )


def _handle_case_resolution_post(
    *,
    request,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
) -> Response:
    sensitive_meta = media_obj.sensitive_meta
    if sensitive_meta is None:
        return Response(
            {"error": f"No sensitive metadata found for {media_type} {media_obj.pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

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
                    patient_examination_id=patient_examination.pk,
                    patient_id=patient_examination.patient_id,
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
                    patient_examination_id=patient_examination.pk,
                    patient_id=patient_examination.patient_id,
                )
            else:
                if media_obj.examination_id is not None:
                    raise ValueError(
                        "cannot defer case resolution for already linked media"
                    )
                patient_examination = media_obj.examination
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
            action=cast(Literal["attach", "create", "defer"], payload.action),
            created=created,
            media_type=media_type,
            media_pk=media_obj.pk,
            media_obj=media_obj,
            patient_examination_id=(
                patient_examination.pk if patient_examination is not None else None
            ),
            patient_id=(
                patient_examination.patient_id
                if patient_examination is not None
                else media_obj.patient_id
            ),
            sensitive_meta=sensitive_meta,
        ),
        status=status.HTTP_200_OK,
    )


# === VIDEO SENSITIVE METADATA ===


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def get_sensitive_metadata_pk(request, pk: int, media_type: str) -> Response:
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
        if not video.sensitive_meta:
            return Response(
                {"error": f"No sensitive metadata found for video {pk}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        sm_id = video.sensitive_meta.pk
        return Response({"sm": sm_id})
    if media_type == "pdf":
        pdf = get_object_or_404(RawPdfFile, pk=pk)
        if not pdf.sensitive_meta:
            return Response(
                {"error": f"No sensitive metadata found for report {pk}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        sm_id = pdf.sensitive_meta.pk
        return Response({"sm": sm_id})
    return Response(
        {"error": f"Unsupported media_type '{media_type}'"},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def video_sensitive_metadata(request, pk):
    """
    GET /api/media/videos/<pk>/sensitive-metadata/
    PATCH /api/media/videos/<pk>/sensitive-metadata/

    Get or update sensitive metadata for a video.
    Video-scoped: Uses video ID to locate related sensitive metadata.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    if not video.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )
    sensitive_meta = video.sensitive_meta

    if request.method == "GET":
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(serializer.data, status=status.HTTP_200_OK)

    elif request.method == "PATCH":
        serializer = SensitiveMetaUpdateSerializer(
            sensitive_meta, data=request.data, partial=True
        )

        if serializer.is_valid():
            updated_instance = serializer.save()
            response_serializer = SensitiveMetaDetailSerializer(updated_instance)

            return Response(
                {
                    "message": "Sensitive metadata updated successfully",
                    "sensitive_meta": response_serializer.data,
                    "video_id": video.pk,
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def video_case_resolution(request, pk):
    """
    GET /api/media/videos/<pk>/case-resolution/

    Return read-only case resolution hints for a validated or pending video.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    if not video.sensitive_meta:
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
            media_pk=video.pk,
            media_obj=video,
            sensitive_meta=video.sensitive_meta,
            linked_patient_examination_id=video.examination_id,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
@transaction.atomic
def video_sensitive_metadata_verify(request, pk):
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

    if not video.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    sensitive_meta = video.sensitive_meta

    dob_verified = request.data.get("dob_verified")
    names_verified = request.data.get("names_verified")

    if dob_verified is None and names_verified is None:
        return Response(
            {
                "error": "At least one of dob_verified or names_verified must be provided"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    state = sensitive_meta.get_or_create_state()

    if dob_verified is not None:
        state.dob_verified = dob_verified
    if names_verified is not None:
        state.names_verified = names_verified

    state.save()

    response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
    return Response(
        {
            "message": "Verification state updated successfully",
            "sensitive_meta": response_serializer.data,
            "video_id": pk,
            "state_verified": state.is_verified,
        },
        status=status.HTTP_200_OK,
    )


# === report SENSITIVE METADATA ===


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_sensitive_metadata(request, pk):
    """
    GET /api/media/pdfs/<pk>/sensitive-metadata/
    PATCH /api/media/pdfs/<pk>/sensitive-metadata/

    Get or update sensitive metadata for a report.
    report-scoped: Uses report ID to locate related sensitive metadata.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    if not pdf.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for report {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )
    sensitive_meta = pdf.sensitive_meta

    if request.method == "GET":
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(serializer.data, status=status.HTTP_200_OK)

    elif request.method == "PATCH":
        serializer = SensitiveMetaUpdateSerializer(
            sensitive_meta, data=request.data, partial=True
        )

        if serializer.is_valid():
            updated_instance = serializer.save()
            response_serializer = SensitiveMetaDetailSerializer(updated_instance)
            sensitive_meta.update_from_dict(response_serializer.data)
            logger.info(
                "Updated sensitive metadata: %s", json.dumps(response_serializer.data)
            )
            return Response(
                {
                    "message": "Sensitive metadata updated successfully",
                    "sensitive_meta": response_serializer.data,
                    "pdf_id": pdf.pk,
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_case_resolution(request, pk):
    """
    GET /api/media/pdfs/<pk>/case-resolution/

    Return read-only case resolution hints for a validated or pending PDF.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    if not pdf.sensitive_meta:
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
            media_pk=pdf.pk,
            media_obj=pdf,
            sensitive_meta=pdf.sensitive_meta,
            linked_patient_examination_id=pdf.examination_id,
        ),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
@transaction.atomic
def pdf_sensitive_metadata_verify(request, pk):
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

    if not pdf.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for report {pk}"},
            status=status.HTTP_404_NOT_FOUND,
        )

    sensitive_meta = pdf.sensitive_meta

    dob_verified = request.data.get("dob_verified")
    names_verified = request.data.get("names_verified")

    if dob_verified is None and names_verified is None:
        return Response(
            {
                "error": "At least one of dob_verified or names_verified must be provided"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    state = sensitive_meta.get_or_create_state()

    if dob_verified is not None:
        state.dob_verified = dob_verified
    if names_verified is not None:
        state.names_verified = names_verified

    state.save()

    response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
    return Response(
        {
            "message": "Verification state updated successfully",
            "sensitive_meta": response_serializer.data,
            "pdf_id": pk,
            "state_verified": state.is_verified,
        },
        status=status.HTTP_200_OK,
    )


# === LIST ENDPOINTS (Collection-Level) ===


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def sensitive_metadata_list(request):
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
    from endoreg_db.serializers.meta import SensitiveMetaDetailSerializer

    # Get all sensitive metadata
    queryset = SensitiveMeta.objects.select_related("state").all()

    # Filter by content type
    content_type = request.query_params.get("content_type")
    if content_type == "pdf":
        # Only reports - filter by existence of related reports
        queryset = queryset.filter(raw_pdf_files__isnull=False).distinct()
    elif content_type == "video":
        # Only Videos - filter by existence of related video
        queryset = queryset.filter(video_file__isnull=False).distinct()

    # Filter by verification status
    verified = request.query_params.get("verified")
    if verified is not None:
        verified_bool = verified.lower() in ("true", "1", "yes")
        queryset = queryset.filter(state__is_verified=verified_bool)

    # Search in patient names
    search = request.query_params.get("search")
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search)
            | Q(patient_last_name__icontains=search)
        )

    # Ordering
    ordering = request.query_params.get("ordering", "-id")
    queryset = queryset.order_by(ordering)

    # Pagination
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 20
    page = paginator.paginate_queryset(queryset, request)

    if page is not None:
        serializer = SensitiveMetaDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    serializer = SensitiveMetaDetailSerializer(queryset, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission, PolicyPermission])
def pdf_sensitive_metadata_list(request):
    """
    GET /api/media/pdfs/sensitive-metadata/

    List sensitive metadata for reports only.
    Replaces legacy /api/pdf/sensitivemeta/list/
    """
    from endoreg_db.serializers.meta import SensitiveMetaDetailSerializer

    # Get all reports with sensitive metadata
    queryset = (
        SensitiveMeta.objects.select_related("state")
        .filter(raw_pdf_files__isnull=False)
        .distinct()
    )

    # Apply filters
    search = request.query_params.get("search")
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search)
            | Q(patient_last_name__icontains=search)
        )

    ordering = request.query_params.get("ordering", "-id")
    queryset = queryset.order_by(ordering)

    # Pagination
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 20
    page = paginator.paginate_queryset(queryset, request)

    if page is not None:
        serializer = SensitiveMetaDetailSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    serializer = SensitiveMetaDetailSerializer(queryset, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)
