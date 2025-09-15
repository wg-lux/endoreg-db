from endoreg_db.serializers.patient_examination import PatientExaminationSerializer

from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response
from endoreg_db.utils.permissions import EnvironmentAwarePermission

import logging
logger = logging.getLogger(__name__)

class ExaminationCreateView(generics.CreateAPIView):
    """
    Create new PatientExamination instances.
    POST /api/examinations/create/

    Expected payload:
    {
        "patient": "patient_hash_string",  # or patient_id integer
        "examination": "examination_name", # examination name string 
        "date_start": "2024-01-15",
    }
    """
    serializer_class = PatientExaminationSerializer
    permission_classes = [EnvironmentAwarePermission]


    @transaction.atomic
    def create(self, request, *args, **kwargs):
        
        try:
            logger.info(f"Creating examination with data: {request.data}")

            # Use the serializer for validation and creation
            serializer = self.get_serializer(data=request.data)

            if serializer.is_valid():
                # The serializer handles patient lookup/creation in validate_patient
                instance = serializer.save()

                response_data = serializer.data
                response_data['message'] = 'Examination created successfully'

                logger.info(f"Examination created successfully with ID: {instance.id}")
                return Response(response_data, status=status.HTTP_201_CREATED)
            else:
                logger.warning(f"Validation errors: {serializer.errors}")
                return Response(
                    {
                        'error': 'Validation failed',
                        'details': serializer.errors
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

        except Exception as e:
            logger.error(f"Error creating examination: {str(e)}")
            return Response(
                {
                    'error': 'Failed to create examination',
                    'message': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )