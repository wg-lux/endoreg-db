# Modern Media Framework: Sensitive Metadata Management
import string
from numpy import number
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.models import VideoFile, RawPdfFile, SensitiveMeta
from endoreg_db.serializers.meta import (
    SensitiveMetaDetailSerializer,
    SensitiveMetaUpdateSerializer,
)

# === VIDEO SENSITIVE METADATA ===

@api_view(['GET'])
@permission_classes([EnvironmentAwarePermission])
def get_sensitive_metadata_pk(request, pk: number, mediaType: str) -> Response | None:
    """
    Retrieve the sensitive metadata primary key for a video or PDF.
    
    Parameters:
        request: The HTTP request object.
        pk (int): Primary key of the media item.
        mediaType (str): Media type, either 'video' or 'pdf'.
    
    Returns:
        Response: JSON with key "sm" containing the sensitive metadata primary key, or a 404 Response with an error message if the media item or its sensitive metadata is not found.
    """
    
    if mediaType == 'video':
        video = get_object_or_404(VideoFile, pk=pk)
        if not video.sensitive_meta:
            return Response(
                {"error": f"No sensitive metadata found for video {pk}"},
                status=status.HTTP_404_NOT_FOUND
            )
        sm_id = video.sensitive_meta.pk
        return Response({
            "sm": sm_id
        })
    if mediaType == 'pdf':
        pdf = get_object_or_404(RawPdfFile, pk=pk)
        if not pdf.sensitive_meta:
            return Response(
                {"error": f"No sensitive metadata found for PDF {pk}"},
                status=status.HTTP_404_NOT_FOUND
            )
        sm_id = pdf.sensitive_meta.pk
        return Response({
            "sm": sm_id
        })
    

@api_view(['GET', 'PATCH'])
@permission_classes([EnvironmentAwarePermission])
def video_sensitive_metadata(request, pk):
    """
    Retrieve or partially update a video's sensitive metadata.
    
    GET returns the serialized sensitive metadata for the given sensitive-meta primary key.
    PATCH accepts partial fields to update the sensitive metadata; on success returns a JSON object containing a confirmation message, the updated `sensitive_meta` data, and the `video_id`.
    
    Returns:
        Response: For GET, the serialized sensitive metadata. For successful PATCH, a response containing
        a message, the updated `sensitive_meta`, and `video_id`. For PATCH validation failures,
        serializer errors. Returns 404 if the sensitive metadata with the given `pk` does not exist.
    """
    sensitive_meta = get_object_or_404(SensitiveMeta, pk=pk)
    
    # Get related sensitive metadata
    if not sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    
    if request.method == 'GET':
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    elif request.method == 'PATCH':
        serializer = SensitiveMetaUpdateSerializer(
            sensitive_meta,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            updated_instance = serializer.save()
            response_serializer = SensitiveMetaDetailSerializer(updated_instance)
            
            return Response({
                "message": "Sensitive metadata updated successfully",
                "sensitive_meta": response_serializer.data,
                "video_id": pk
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([EnvironmentAwarePermission])
@transaction.atomic
def video_sensitive_metadata_verify(request, pk):
    """
    Update the verification state for a video's sensitive metadata.
    
    Updates the sensitive metadata verification flags for the video identified by `pk`. At least one of `dob_verified` or `names_verified` must be present in the request data; the corresponding state fields are updated and saved. If the video has no associated sensitive metadata, a 404 response is returned.
    
    Parameters:
        request: The incoming request; expects JSON with optional boolean fields `dob_verified` and `names_verified`.
        pk (int): Primary key of the VideoFile to update.
    
    Returns:
        Response: JSON containing a success `message`, the serialized `sensitive_meta`, `video_id`, and boolean `state_verified`. Returns 404 if the video or its sensitive metadata is missing, or 400 if neither verification field is provided.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    
    if not video.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    sensitive_meta = video.sensitive_meta
    
    dob_verified = request.data.get('dob_verified')
    names_verified = request.data.get('names_verified')
    
    if dob_verified is None and names_verified is None:
        return Response(
            {"error": "At least one of dob_verified or names_verified must be provided"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    state = sensitive_meta.get_or_create_state()
    
    if dob_verified is not None:
        state.dob_verified = dob_verified
    if names_verified is not None:
        state.names_verified = names_verified
    
    state.save()
    
    response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
    return Response({
        "message": "Verification state updated successfully",
        "sensitive_meta": response_serializer.data,
        "video_id": pk,
        "state_verified": state.is_verified
    }, status=status.HTTP_200_OK)


# === PDF SENSITIVE METADATA ===

@api_view(['GET', 'PATCH'])
@permission_classes([EnvironmentAwarePermission])
def pdf_sensitive_metadata(request, pk):
    """
    Handle retrieval and partial updates of a PDF's sensitive metadata.
    
    Supports GET to return the detailed sensitive metadata for the given SensitiveMeta record, and PATCH to apply partial updates to that record and return the updated representation.
    
    Parameters:
        request: The incoming HTTP request object; for PATCH, request.data contains fields to update.
        pk (int): Primary key of the SensitiveMeta record to retrieve or update.
    
    Returns:
        On GET: a 200 response containing the serialized sensitive metadata.
        On successful PATCH: a 200 response with a success message, the serialized updated sensitive metadata under `sensitive_meta`, and `pdf_id`.
        On PATCH validation failure: a 400 response with serializer validation errors.
        If the SensitiveMeta with `pk` does not exist: a 404 response with an error message.
    """
    sensitive_meta = get_object_or_404(SensitiveMeta, pk=pk)
    
    # Get related sensitive metadata
    if not sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for PDF {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    
    if request.method == 'GET':
        serializer = SensitiveMetaDetailSerializer(sensitive_meta)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    elif request.method == 'PATCH':
        serializer = SensitiveMetaUpdateSerializer(
            sensitive_meta,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            updated_instance = serializer.save()
            response_serializer = SensitiveMetaDetailSerializer(updated_instance)
            
            return Response({
                "message": "Sensitive metadata updated successfully",
                "sensitive_meta": response_serializer.data,
                "pdf_id": pk
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([EnvironmentAwarePermission])
@transaction.atomic
def pdf_sensitive_metadata_verify(request, pk):
    """
    Update verification flags for a PDF's sensitive metadata.
    
    Accepts JSON payload with one or both of the fields "dob_verified" and "names_verified" to set the corresponding verification flags on the sensitive metadata's state.
    
    Parameters:
        request: HTTP request with JSON body containing verification fields.
        pk (int): Primary key of the RawPdfFile whose sensitive metadata state will be updated.
    
    Returns:
        dict: On success, a response containing:
            - "message": Confirmation string.
            - "sensitive_meta": Serialized sensitive metadata after the update.
            - "pdf_id": The provided PDF primary key.
            - "state_verified": `true` if the sensitive metadata state's verification criteria are met, `false` otherwise.
        Error responses:
            - 404 if the PDF does not exist or has no associated sensitive metadata.
            - 400 if neither "dob_verified" nor "names_verified" is provided.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    
    if not pdf.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for PDF {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    sensitive_meta = pdf.sensitive_meta
    
    dob_verified = request.data.get('dob_verified')
    names_verified = request.data.get('names_verified')
    
    if dob_verified is None and names_verified is None:
        return Response(
            {"error": "At least one of dob_verified or names_verified must be provided"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    state = sensitive_meta.get_or_create_state()
    
    if dob_verified is not None:
        state.dob_verified = dob_verified
    if names_verified is not None:
        state.names_verified = names_verified
    
    state.save()
    
    response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
    return Response({
        "message": "Verification state updated successfully",
        "sensitive_meta": response_serializer.data,
        "pdf_id": pk,
        "state_verified": state.is_verified
    }, status=status.HTTP_200_OK)


# === LIST ENDPOINTS (Collection-Level) ===

@api_view(['GET'])
@permission_classes([EnvironmentAwarePermission])
def sensitive_metadata_list(request):
    """
    List sensitive metadata entries for videos and PDFs with optional filtering, search, ordering, and pagination.
    
    Query parameters:
        content_type (str, optional): 'pdf' or 'video' to restrict results to that media type.
        verified (str, optional): Filter by verification status; accepted truthy values: 'true', '1', 'yes'.
        search (str, optional): Case-insensitive substring search applied to patient_first_name and patient_last_name.
        ordering (str, optional): Field name to order by (prefix with '-' for descending). Defaults to '-id'.
        page / page_size (int, optional): Standard DRF pagination parameters; page_size is capped by the view's paginator (default 20).
    
    Returns:
        rest_framework.response.Response: Serialized list of SensitiveMeta objects. When pagination is applied, returns a paginated response payload; otherwise returns a flat list.
    """
    from endoreg_db.serializers.meta import SensitiveMetaDetailSerializer
    
    # Get all sensitive metadata
    queryset = SensitiveMeta.objects.select_related('state').all()
    
    # Filter by content type
    content_type = request.query_params.get('content_type')
    if content_type == 'pdf':
        # Only PDFs - filter by existence of related PDFs
        queryset = queryset.filter(raw_pdf_files__isnull=False).distinct()
    elif content_type == 'video':
        # Only Videos - filter by existence of related video
        queryset = queryset.filter(video_file__isnull=False).distinct()
    
    # Filter by verification status
    verified = request.query_params.get('verified')
    if verified is not None:
        verified_bool = verified.lower() in ('true', '1', 'yes')
        queryset = queryset.filter(state__is_verified=verified_bool)
    
    # Search in patient names
    search = request.query_params.get('search')
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search) |
            Q(patient_last_name__icontains=search)
        )
    
    # Ordering
    ordering = request.query_params.get('ordering', '-id')
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


@api_view(['GET'])
@permission_classes([EnvironmentAwarePermission])
def pdf_sensitive_metadata_list(request):
    """
    List sensitive metadata entries associated with PDFs.
    
    Supports optional case-insensitive search on patient_first_name and patient_last_name via the `search` query parameter, optional ordering via the `ordering` query parameter (default `-id`), and paginates results with a page size of 20.
    
    Returns:
        Response: HTTP 200 with either a paginated response or a JSON array of serialized SensitiveMeta objects using SensitiveMetaDetailSerializer.
    """
    from endoreg_db.serializers.meta import SensitiveMetaDetailSerializer
    
    # Get all PDFs with sensitive metadata
    queryset = SensitiveMeta.objects.select_related('state').filter(
        raw_pdf_files__isnull=False
    ).distinct()
    
    # Apply filters
    search = request.query_params.get('search')
    if search:
        queryset = queryset.filter(
            Q(patient_first_name__icontains=search) |
            Q(patient_last_name__icontains=search)
        )
    
    ordering = request.query_params.get('ordering', '-id')
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