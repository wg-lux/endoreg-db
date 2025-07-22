from endoreg_db.models import SensitiveMeta
from endoreg_db.serializers import SensitiveMetaDetailSerializer, SensitiveMetaUpdateSerializer

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
import logging

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