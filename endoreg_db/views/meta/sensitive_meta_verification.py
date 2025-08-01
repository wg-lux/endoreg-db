from endoreg_db.models import SensitiveMeta
from endoreg_db.serializers.meta import SensitiveMetaDetailSerializer, SensitiveMetaVerificationSerializer

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
import logging

logger = logging.getLogger(__name__)

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