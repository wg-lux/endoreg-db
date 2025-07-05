"""
Anonymization overview API views.
Provides endpoints for managing file anonymization status and validation workflow.
"""
from pathlib import Path
from django.http import Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q, F, Case, When, CharField, Value
from django.utils import timezone
from endoreg_db.models import VideoFile, RawPdfFile, SensitiveMeta
from endoreg_db.serializers.video_meta import VideoFileForMetaSerializer
from endoreg_db.serializers.file_overview_serializer import PatientDataSerializer

# DEBUG: Remove in production
DEBUG_PERMISSIONS = True


@api_view(['GET'])
@permission_classes([AllowAny] if DEBUG_PERMISSIONS else [])
def anonymization_items_overview(request):
    """
    Get overview of all uploaded files with their anonymization and annotation statuses.
    
    Returns:
        List of FileItem objects with:
        - id: primary key (VideoFile.id or RawPdfFile.id)
        - filename: original_file_name or file.name
        - mediaType: "video" | "pdf"
        - anonymizationStatus: "not_started" | "processing" | "done" | "failed"
        - annotationStatus: "not_started" | "done"
        - createdAt: ISO string (uploaded_at for videos, created_at for PDFs)
    """
    
    # Build video queryset with values projection
    video_values = VideoFile.objects.select_related('state', 'sensitive_meta').values(
        'id',
        'original_file_name',
        'uploaded_at',
        'state__anonymized',
        'state__processing_error', 
        'state__frames_extracted',
        'sensitive_meta__is_verified'
    ).order_by('-uploaded_at')
    
    # Build PDF queryset with values projection
    pdf_values = RawPdfFile.objects.select_related('sensitive_meta').values(
        'id',
        'file',
        'created_at',
        'anonymized_text',
        'sensitive_meta__is_verified'
    ).order_by('-created_at')
    
    items = []
    
    # Process videos
    for video_data in video_values:
        # Determine anonymization status
        if video_data['state__anonymized']:
            anonymization_status = 'done'
        elif video_data['state__processing_error']:
            anonymization_status = 'failed'
        elif video_data['state__frames_extracted']:
            anonymization_status = 'processing'
        else:
            anonymization_status = 'not_started'
        
        # Determine annotation status
        annotation_status = 'done' if video_data['sensitive_meta__is_verified'] else 'not_started'
        
        items.append({
            'id': video_data['id'],
            'filename': video_data['original_file_name'] or f"video_{video_data['id']}",
            'mediaType': 'video',
            'anonymizationStatus': anonymization_status,
            'annotationStatus': annotation_status,
            'createdAt': video_data['uploaded_at'].isoformat() if video_data['uploaded_at'] else None
        })
    
    # Process PDFs
    for pdf_data in pdf_values:
        # Extract filename from file path
        filename = Path(pdf_data['file']).name if pdf_data['file'] else f"pdf_{pdf_data['id']}"
        
        # Determine anonymization status
        anonymization_status = 'done' if pdf_data['anonymized_text'] and pdf_data['anonymized_text'].strip() else 'not_started'
        
        # Determine annotation status
        annotation_status = 'done' if pdf_data['sensitive_meta__is_verified'] else 'not_started'
        
        items.append({
            'id': pdf_data['id'],
            'filename': filename,
            'mediaType': 'pdf',
            'anonymizationStatus': anonymization_status,
            'annotationStatus': annotation_status,
            'createdAt': pdf_data['created_at'].isoformat() if pdf_data['created_at'] else None
        })
    
    # Sort by createdAt descending
    items.sort(key=lambda x: x['createdAt'] or '', reverse=True)
    
    return Response(items)


@api_view(['PUT', 'POST'])
@permission_classes([AllowAny] if DEBUG_PERMISSIONS else [])
def set_current_for_validation(request, file_id):
    """
    Set current file for validation and return patient data.
    Accepts both PUT and POST methods for frontend compatibility.
    """
    try:
        # Try to find VideoFile first
        try:
            obj = VideoFile.objects.select_related('sensitive_meta').get(id=file_id)
        except VideoFile.DoesNotExist:
            # Try RawPdfFile
            obj = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
        
        # Serialize with PatientDataSerializer
        serializer = PatientDataSerializer(obj, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)
        
    except RawPdfFile.DoesNotExist:
        # Neither video nor PDF found
        return Response(
            {'error': f'File with ID {file_id} not found'},
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['POST'])
@permission_classes([AllowAny] if DEBUG_PERMISSIONS else [])
def start_anonymization(request, file_id):
    """
    Start anonymization process for a specific file.
    
    This endpoint triggers the anonymization workflow for videos or PDFs.
    For videos, this would typically start face detection and blurring.
    For PDFs, this would start text anonymization.
    
    Args:
        file_id: Primary key of VideoFile or RawPdfFile
        
    Returns:
        Success response or error if file not found
    """
    
    # Try to find video first
    try:
        video = VideoFile.objects.get(id=file_id)
        
        # For videos, trigger anonymization process
        # This is a placeholder - implement actual anonymization logic
        if hasattr(video, 'state'):
            video.state.anonymized = False
            video.state.processing_error = False
            video.state.frames_extracted = True  # Set to processing state
            video.state.save()
        
        return Response({
            'message': f'Anonymization started for video {file_id}',
            'status': 'processing'
        })
        
    except VideoFile.DoesNotExist:
        pass
    
    # Try to find PDF
    try:
        pdf = RawPdfFile.objects.get(id=file_id)
        
        # For PDFs, trigger anonymization process
        # This is a placeholder - implement actual anonymization logic
        # You could call the import_report command logic here
        
        return Response({
            'message': f'Anonymization started for PDF {file_id}',
            'status': 'processing'
        })
        
    except RawPdfFile.DoesNotExist:
        pass
    
    # Neither video nor PDF found
    return Response(
        {'error': f'File with ID {file_id} not found'},
        status=status.HTTP_404_NOT_FOUND
    )


@api_view(['GET'])
@permission_classes([AllowAny] if DEBUG_PERMISSIONS else [])
def get_anonymization_status(request, file_id):
    """
    Get anonymization status for a specific file.
    
    This endpoint is used by the frontend to poll the status of anonymization
    processes for both videos and PDFs.
    
    Args:
        file_id: Primary key of VideoFile or RawPdfFile
        
    Returns:
        Status information including anonymization and annotation status
    """
    
    # Try to find video first
    try:
        video = VideoFile.objects.select_related('state', 'sensitive_meta').get(id=file_id)
        
        # Determine anonymization status
        if video.state.anonymized:
            anonymization_status = 'done'
        elif video.state.processing_error:
            anonymization_status = 'failed'
        elif video.state.frames_extracted:
            anonymization_status = 'processing'
        else:
            anonymization_status = 'not_started'
        
        # Determine annotation status
        annotation_status = 'done' if video.sensitive_meta and video.sensitive_meta.is_verified else 'not_started'
        
        return Response({
            'id': video.id,
            'mediaType': 'video',
            'anonymizationStatus': anonymization_status,
            'annotationStatus': annotation_status,
            'filename': video.original_file_name or f"video_{video.id}",
            'createdAt': video.uploaded_at.isoformat() if video.uploaded_at else None
        })
        
    except VideoFile.DoesNotExist:
        pass
    
    # Try to find PDF
    try:
        pdf = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
        
        # Extract filename from file path
        filename = Path(pdf.file.name).name if pdf.file else f"pdf_{pdf.id}"
        
        # Determine anonymization status
        anonymization_status = 'done' if pdf.anonymized_text and pdf.anonymized_text.strip() else 'not_started'
        
        # Determine annotation status
        annotation_status = 'done' if pdf.sensitive_meta and pdf.sensitive_meta.is_verified else 'not_started'
        
        return Response({
            'id': pdf.id,
            'mediaType': 'pdf',
            'anonymizationStatus': anonymization_status,
            'annotationStatus': annotation_status,
            'filename': filename,
            'createdAt': pdf.created_at.isoformat() if pdf.created_at else None
        })
        
    except RawPdfFile.DoesNotExist:
        pass
    
    # Neither video nor PDF found
    return Response(
        {'error': f'File with ID {file_id} not found'},
        status=status.HTTP_404_NOT_FOUND
    )