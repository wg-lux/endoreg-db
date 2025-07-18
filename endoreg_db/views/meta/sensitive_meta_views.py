from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
# from rest_framework.permissions import AllowAny  # Changed from IsAuthenticated for development # not used in this file
from django.db import transaction
from django.shortcuts import get_object_or_404
import logging
from ...utils.permissions import DEBUG_PERMISSIONS
from ...models import SensitiveMeta, RawPdfFile, VideoFile
from ...serializers.meta.sensitive_meta_serializer import (
    SensitiveMetaDetailSerializer,
    SensitiveMetaUpdateSerializer,
    SensitiveMetaVerificationSerializer
)

logger = logging.getLogger(__name__)


class SensitiveMetaDetailView(APIView):
    """
    API endpoint to retrieve and update SensitiveMeta details with verification state.
    
    GET: Returns detailed SensitiveMeta information for annotation/verification
    PATCH: Updates SensitiveMeta fields including verification state
    """
    
    permission_classes = DEBUG_PERMISSIONS  # Changed from IsAuthenticated for development

    def get(self, request, sensitive_meta_id=None):
        """
        Retrieve SensitiveMeta details for display and annotation.
        
        Supports both URL parameter and query parameter access patterns:
        - /api/pdf/sensitivemeta/123/ (URL parameter)
        - /api/pdf/sensitivemeta/?id=123 (query parameter - for backward compatibility)
        - /api/pdf/sensitivemeta/ (list all - returns empty list instead of 400)
        
        Returns detailed information suitable for user verification.
        """
        # Handle both URL parameter and query parameter patterns
        if not sensitive_meta_id:
            sensitive_meta_id = request.query_params.get('id')
        
        # If no ID provided, return empty list instead of error
        if not sensitive_meta_id:
            return Response([], status=status.HTTP_200_OK)

        try:
            # Convert to int if it's a string
            sensitive_meta_id = int(sensitive_meta_id)
            
            # Get the SensitiveMeta instance with related data
            sensitive_meta = SensitiveMeta.objects.select_related(
                'center', 
                'patient_gender',
                'pseudo_patient',
                'pseudo_examination'
            ).prefetch_related(
                'examiners',
                'state'
            ).get(id=sensitive_meta_id)

            # Serialize for display
            serializer = SensitiveMetaDetailSerializer(sensitive_meta)
            
            # Return direct data to match anonymization store expectations
            # Instead of wrapping in "sensitive_meta" key, return data directly
            response_data = serializer.data
            
            logger.info(f"Retrieved SensitiveMeta {sensitive_meta_id} for user {request.user}")
            return Response(response_data, status=status.HTTP_200_OK)
            
        except ValueError:
            return Response(
                {"error": "Invalid sensitive_meta_id format. Must be an integer."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except SensitiveMeta.DoesNotExist:
            return Response(
                {"error": f"SensitiveMeta with ID {sensitive_meta_id} not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error retrieving SensitiveMeta {sensitive_meta_id}: {e}")
            return Response(
                {"error": "Internal server error occurred"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @transaction.atomic
    def patch(self, request, sensitive_meta_id=None):
        """
        Update SensitiveMeta fields including verification state.
        
        Accepts partial updates for any field in SensitiveMetaUpdateSerializer.
        Special handling for verification state changes.
        """
        if not sensitive_meta_id:
            return Response(
                {"error": "sensitive_meta_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get the SensitiveMeta instance
            sensitive_meta = get_object_or_404(SensitiveMeta, id=sensitive_meta_id)
            
            # Validate and update using serializer
            serializer = SensitiveMetaUpdateSerializer(
                sensitive_meta, 
                data=request.data, 
                partial=True
            )
            
            if serializer.is_valid():
                updated_instance = serializer.save()
                
                # Return updated data using detail serializer
                response_serializer = SensitiveMetaDetailSerializer(updated_instance)
                
                response_data = {
                    "message": "SensitiveMeta updated successfully",
                    "sensitive_meta": response_serializer.data,
                    "updated_fields": list(request.data.keys())
                }
                
                logger.info(
                    f"Updated SensitiveMeta {sensitive_meta_id} by user {request.user}. "
                    f"Fields: {list(request.data.keys())}"
                )
                
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                return Response(
                    {
                        "error": "Validation failed", 
                        "details": serializer.errors
                    }, 
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        except SensitiveMeta.DoesNotExist:
            return Response(
                {"error": f"SensitiveMeta with ID {sensitive_meta_id} not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error updating SensitiveMeta {sensitive_meta_id}: {e}")
            return Response(
                {"error": "Internal server error occurred"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SensitiveMetaVerificationView(APIView):
    """
    API endpoint specifically for updating verification state.
    
    POST: Update verification flags (dob_verified, names_verified) for a SensitiveMeta
    """
    
    permission_classes = DEBUG_PERMISSIONS  # Changed from IsAuthenticated for development

    @transaction.atomic
    def post(self, request):
        """
        Update verification state for a SensitiveMeta instance.
        
        Expected payload:
        {
            "sensitive_meta_id": 123,
            "dob_verified": true,
            "names_verified": true
        }
        """
        serializer = SensitiveMetaVerificationSerializer(data=request.data)
        
        if serializer.is_valid():
            try:
                state = serializer.save()
                sensitive_meta_id = serializer.validated_data['sensitive_meta_id']
                
                # Get updated SensitiveMeta for response
                sensitive_meta = SensitiveMeta.objects.get(id=sensitive_meta_id)
                response_serializer = SensitiveMetaDetailSerializer(sensitive_meta)
                
                response_data = {
                    "message": "Verification state updated successfully",
                    "sensitive_meta": response_serializer.data,
                    "state_verified": state.is_verified
                }
                
                logger.info(
                    f"Updated verification state for SensitiveMeta {sensitive_meta_id} "
                    f"by user {request.user}. Overall verified: {state.is_verified}"
                )
                
                return Response(response_data, status=status.HTTP_200_OK)
                
            except Exception as e:
                logger.error(f"Error in verification update: {e}")
                return Response(
                    {"error": f"Failed to update verification state: {e}"}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            return Response(
                {
                    "error": "Validation failed", 
                    "details": serializer.errors
                }, 
                status=status.HTTP_400_BAD_REQUEST
            )


class SensitiveMetaListView(APIView):
    """
    API endpoint to list SensitiveMeta entries with filtering options.
    
    GET: Returns paginated list of SensitiveMeta entries
    """
    
    permission_classes = DEBUG_PERMISSIONS  # Changed from IsAuthenticated for development

    def get(self, request):
        """
        List SensitiveMeta entries with optional filtering.
        
        Query Parameters:
        - verified: Filter by verification status (true/false)
        - center: Filter by center name
        - limit: Number of results to return (default 20)
        - offset: Offset for pagination (default 0)
        """
        try:
            # Base queryset with related data
            queryset = SensitiveMeta.objects.select_related(
                'center', 
                'patient_gender',
                'state'
            ).prefetch_related('examiners')
            
            # Apply filters
            verified_filter = request.query_params.get('verified')
            if verified_filter is not None:
                if verified_filter.lower() == 'true':
                    # Filter for entries where both DOB and names are verified
                    queryset = queryset.filter(
                        state__dob_verified=True,
                        state__names_verified=True
                    )
                elif verified_filter.lower() == 'false':
                    # Filter for entries that are not fully verified
                    queryset = queryset.exclude(
                        state__dob_verified=True,
                        state__names_verified=True
                    )
            
            center_filter = request.query_params.get('center')
            if center_filter:
                queryset = queryset.filter(center__name__icontains=center_filter)
            
            # Pagination
            limit = int(request.query_params.get('limit', 20))
            offset = int(request.query_params.get('offset', 0))
            
            total_count = queryset.count()
            paginated_queryset = queryset[offset:offset + limit]
            
            # Serialize results
            serializer = SensitiveMetaDetailSerializer(paginated_queryset, many=True)
            
            response_data = {
                "results": serializer.data,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total_count
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response(
                {"error": f"Invalid query parameters: {e}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error listing SensitiveMeta entries: {e}")
            return Response(
                {"error": "Internal server error occurred"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AvailableFilesListView(APIView):
    """
    API endpoint to list available PDFs and videos for anonymization selection.
    
    GET: Returns lists of available PDF and video files with their metadata
    """
    
    permission_classes = DEBUG_PERMISSIONS

    def get(self, request):
        """
        List available PDF and video files for anonymization selection.
        
        Query Parameters:
        - type: Filter by file type ('pdf' or 'video')
        - status: Filter by anonymization status
        - limit: Number of results to return (default 50)
        - offset: Offset for pagination (default 0)
        
        Returns:
        {
            "pdfs": [...],
            "videos": [...],
            "total_pdfs": N,
            "total_videos": N
        }
        """
        try:
            file_type = request.query_params.get('type', 'all').lower()
            limit = int(request.query_params.get('limit', 50))
            offset = int(request.query_params.get('offset', 0))
            
            response_data = {}
            
            # Get PDFs if requested
            if file_type in ['all', 'pdf']:
                pdf_queryset = RawPdfFile.objects.select_related('sensitive_meta').all()
                total_pdfs = pdf_queryset.count()
                # Validate limit and offset
                limit_param = request.query_params.get('limit', 50)
                offset_param = request.query_params.get('offset', 0)
                try:
                    limit = int(limit_param)
                    offset = int(offset_param)
                    if limit < 0 or offset < 0:
                        raise ValueError("limit and offset must be non-negative integers")
                except ValueError:
                    return Response(
                        {"error": "Invalid 'limit' or 'offset' parameter. Must be non-negative integers."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                paginated_pdfs = pdf_queryset[offset:offset + limit]
                
                pdf_list = []
                for pdf in paginated_pdfs:
                    # Safely handle missing file attribute
                    file_name = 'Unknown'
                    file_path = None
                    if hasattr(pdf, 'file') and pdf.file:
                        file_name = pdf.file.name.split('/')[-1]
                        file_path = pdf.file.name
                    pdf_data = {
                        'id': pdf.id,
                        'filename': file_name,
                        'file_path': file_path,
                        'sensitive_meta_id': pdf.sensitive_meta_id,
                        'anonymized_text': getattr(pdf, 'anonymized_text', None),
                        'created_at': pdf.created_at if hasattr(pdf, 'created_at') else None,
                        'patient_info': None
                    }
                    
                    # Add patient info if available
                    if pdf.sensitive_meta:
                        pdf_data['patient_info'] = {
                            'patient_first_name': pdf.sensitive_meta.patient_first_name,
                            'patient_last_name': pdf.sensitive_meta.patient_last_name,
                            'patient_dob': pdf.sensitive_meta.patient_dob,
                            'examination_date': pdf.sensitive_meta.examination_date,
                            'center_name': pdf.sensitive_meta.center.name if pdf.sensitive_meta.center else None
                        }
                    
                    pdf_list.append(pdf_data)
                
                response_data['pdfs'] = pdf_list
                response_data['total_pdfs'] = total_pdfs
            # Get Videos if requested
            if file_type in ['all', 'video']:
                video_queryset = VideoFile.objects.select_related('sensitive_meta').all()
                total_videos = video_queryset.count()
                # Validate limit and offset (reuse above logic)
                limit_param = request.query_params.get('limit', 50)
                offset_param = request.query_params.get('offset', 0)
                try:
                    limit = int(limit_param)
                    offset = int(offset_param)
                    if limit < 0 or offset < 0:
                        raise ValueError("limit and offset must be non-negative integers")
                except ValueError:
                    return Response(
                        {"error": "Invalid 'limit' or 'offset' parameter. Must be non-negative integers."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                paginated_videos = video_queryset[offset:offset + limit]
                
                video_list = []
                for video in paginated_videos:
                    # Safely handle missing raw_file attribute
                    file_name = 'Unknown'
                    file_path = None
                    if hasattr(video, 'raw_file') and video.raw_file:
                        file_name = video.raw_file.name.split('/')[-1]
                        file_path = video.raw_file.name
                    video_data = {
                        'id': video.id,
                        'filename': file_name,
                        'file_path': file_path,
                        'sensitive_meta_id': video.sensitive_meta_id,
                        'created_at': video.created_at if hasattr(video, 'created_at') else None,
                        'patient_info': None
                    }
                    
                    # Add patient info if available
                    if video.sensitive_meta:
                        video_data['patient_info'] = {
                            'patient_first_name': video.sensitive_meta.patient_first_name,
                            'patient_last_name': video.sensitive_meta.patient_last_name,
                            'patient_dob': video.sensitive_meta.patient_dob,
                            'examination_date': video.sensitive_meta.examination_date,
                            'center_name': video.sensitive_meta.center.name if video.sensitive_meta.center else None
                        }
                    
                    video_list.append(video_data)
                
                response_data['videos'] = video_list
                response_data['total_videos'] = total_videos
            
            response_data['limit'] = limit
            response_data['offset'] = offset
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response(
                {"error": f"Invalid query parameters: {e}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error listing available files: {e}")
            return Response(
                {"error": "Internal server error occurred"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )