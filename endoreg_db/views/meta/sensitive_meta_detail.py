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