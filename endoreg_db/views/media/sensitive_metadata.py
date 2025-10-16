# Modern Media Framework: Sensitive Metadata Management
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

@api_view(['GET', 'PATCH'])
@permission_classes([EnvironmentAwarePermission])
def video_sensitive_metadata(request, pk):
    """
    GET /api/media/videos/<pk>/sensitive-metadata/
    PATCH /api/media/videos/<pk>/sensitive-metadata/
    
    Get or update sensitive metadata for a video.
    Video-scoped: Uses video ID to locate related sensitive metadata.
    """
    video = get_object_or_404(VideoFile, pk=pk)
    
    # Get related sensitive metadata
    if not video.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for video {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    sensitive_meta = video.sensitive_meta
    
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
    GET /api/media/pdfs/<pk>/sensitive-metadata/
    PATCH /api/media/pdfs/<pk>/sensitive-metadata/
    
    Get or update sensitive metadata for a PDF.
    PDF-scoped: Uses PDF ID to locate related sensitive metadata.
    """
    pdf = get_object_or_404(RawPdfFile, pk=pk)
    
    # Get related sensitive metadata
    if not pdf.sensitive_meta:
        return Response(
            {"error": f"No sensitive metadata found for PDF {pk}"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    sensitive_meta = pdf.sensitive_meta
    
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
    POST /api/media/pdfs/<pk>/sensitive-metadata/verify/
    
    Update verification state for PDF sensitive metadata.
    
    Expected payload:
    {
        "dob_verified": true,
        "names_verified": true
    }
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
    GET /api/media/sensitive-metadata/
    
    List all sensitive metadata (combined PDFs and Videos).
    Supports filtering by content_type, status, etc.
    
    Query parameters:
    - content_type: 'pdf' | 'video' (optional)
    - verified: Filter by verification status
    - ordering: Sort field
    - search: Search in patient names
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
    GET /api/media/pdfs/sensitive-metadata/
    
    List sensitive metadata for PDFs only.
    Replaces legacy /api/pdf/sensitivemeta/list/
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
