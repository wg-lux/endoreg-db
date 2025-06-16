from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.shortcuts import get_object_or_404
import logging

from ..models import SensitiveMeta, SensitiveMetaState
from ..serializers.sensitive_meta_serializer import (
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
    
    permission_classes = [IsAuthenticated]

    def get(self, request, sensitive_meta_id=None):
        """
        Retrieve SensitiveMeta details for display and annotation.
        
        Query Parameters:
        - sensitive_meta_id: ID of the SensitiveMeta to retrieve
        
        Returns detailed information suitable for user verification.
        """
        if not sensitive_meta_id:
            return Response(
                {"error": "sensitive_meta_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
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
            
            response_data = {
                "sensitive_meta": serializer.data,
                "message": "SensitiveMeta details retrieved successfully"
            }
            
            logger.info(f"Retrieved SensitiveMeta {sensitive_meta_id} for user {request.user}")
            return Response(response_data, status=status.HTTP_200_OK)
            
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
    
    permission_classes = [IsAuthenticated]

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
    
    permission_classes = [IsAuthenticated]

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