from endoreg_db.models import PatientExamination
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer

from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

import logging
logger = logging.getLogger(__name__)

class PatientExaminationDetailView(generics.RetrieveUpdateAPIView):
    """
    Retrieve and update PatientExamination instances.
    GET /api/examinations/{id}/
    PATCH /api/examinations/{id}/
    """
    queryset = PatientExamination.objects.select_related('patient', 'examination')
    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

    def get(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error retrieving examination: {str(e)}")
            return Response(
                {'error': 'Failed to retrieve examination'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)

            if serializer.is_valid():
                updated_instance = serializer.save()

                response_data = serializer.data
                response_data['message'] = 'Examination updated successfully'

                logger.info(f"Examination {instance.id} updated successfully")
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                return Response(
                    {
                        'error': 'Validation failed',
                        'details': serializer.errors
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

        except Exception as e:
            logger.error(f"Error updating examination: {str(e)}")
            return Response(
                {
                    'error': 'Failed to update examination',
                    'message': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )