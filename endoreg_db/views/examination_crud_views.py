from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
import logging

from ..models import PatientExamination, Patient, Examination
from ..serializers.patient_examination import PatientExaminationSerializer
from ..utils.permissions import DEBUG_PERMISSIONS

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
        "date_end": "2024-01-15"  # optional
    }
    """
    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

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


class ExaminationDetailView(generics.RetrieveUpdateAPIView):
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


class ExaminationListView(generics.ListAPIView):
    """
    List PatientExamination instances with filtering.
    GET /api/examinations/list/
    
    Query parameters:
    - patient_id: Filter by patient ID
    - examination_name: Filter by examination name
    - limit: Number of results (default 20)
    - offset: Pagination offset (default 0)
    """
    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

    def get_queryset(self):
        queryset = PatientExamination.objects.select_related(
            'patient', 'examination'
        ).order_by('-date_start', '-id')
        
        # Apply filters
        patient_id = self.request.query_params.get('patient_id')
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)
        
        examination_name = self.request.query_params.get('examination_name')
        if examination_name:
            queryset = queryset.filter(examination__name__icontains=examination_name)
        
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            
            # Pagination
            limit = int(request.query_params.get('limit', 20))
            offset = int(request.query_params.get('offset', 0))
            
            total_count = queryset.count()
            paginated_queryset = queryset[offset:offset + limit]
            
            serializer = self.get_serializer(paginated_queryset, many=True)
            
            return Response({
                'results': serializer.data,
                'total_count': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + limit) < total_count
            })
            
        except Exception as e:
            logger.error(f"Error listing examinations: {str(e)}")
            return Response(
                {'error': 'Failed to list examinations'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )