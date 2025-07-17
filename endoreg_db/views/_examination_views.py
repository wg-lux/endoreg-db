# endoreg_db/views/examination_views.py
from rest_framework.viewsets import ModelViewSet
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from endoreg_db.models import (
    Examination, 
    VideoFile,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
    Finding
)
from ..serializers.optimized_examination_serializers import (
    ExaminationSerializer as OptimizedExaminationSerializer,
    FindingSerializer as OptimizedFindingSerializer,
)

# views/examination_views.py
def build_multilingual_response(obj, include_choices=False, classification_id=None):
    """
    Helper to build a multilingual response dict for an object.
    If include_choices is True, adds a 'choices' key with multilingual dicts for each choice.
    If classification_id is given, adds 'classificationId' to each choice.
    """
    data = {
        'id': obj.id,
        'name': getattr(obj, 'name', ''),
        'name_de': getattr(obj, 'name_de', ''),
        'name_en': getattr(obj, 'name_en', ''),
        'description': getattr(obj, 'description', ''),
        'description_de': getattr(obj, 'description_de', ''),
        'description_en': getattr(obj, 'description_en', ''),
    }
    # Add 'required' if present on the object
    if hasattr(obj, 'required'):
        data['required'] = getattr(obj, 'required', True)
    if include_choices:
        data['choices'] = [
            build_multilingual_response(choice, include_choices=False, classification_id=classification_id or obj.id)
            for choice in obj.get_choices()
        ]
        # Add classificationId to each choice
        for choice_dict in data['choices']:
            choice_dict['classificationId'] = classification_id or obj.id
    if classification_id is not None and not include_choices:
        data['classificationId'] = classification_id
    return data

class ExaminationViewSet(ModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = OptimizedExaminationSerializer

# NEW ENDPOINTS FOR RESTRUCTURED FRONTEND

@api_view(["GET"])
def get_location_classifications_for_exam(request, exam_id):
    """
    Retrieves location classifications linked to a specific examination.
    
    Returns a list of dictionaries containing the ID and name of each location classification associated with the examination identified by `exam_id`.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    location_classifications = exam.location_classifications.all()
    return Response([{"id": lc.id, "name": lc.name} for lc in location_classifications])

@api_view(["GET"])
def get_findings_for_exam(request, exam_id):
    """
    Retrieves findings associated with a specific examination.
    
    Returns a list of dictionaries containing the ID and name of each finding linked to the examination identified by `exam_id`.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    serializer = OptimizedFindingSerializer(findings, many=True)
    return Response(serializer.data)

@api_view(["GET"])
def get_location_choices_for_classification(request, exam_id, location_classification_id):
    """
    Retrieves location choices for a given location classification within an examination.
    
    Returns a list of choices, each with its ID, name, and the associated classification ID. Responds with a 404 error if the location classification does not belong to the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    location_classification = get_object_or_404(FindingClassification, id=location_classification_id, classification_types__name__iexact="location")

    # Validate that the location classification belongs to this examination
    if location_classification not in exam.location_classifications.all():
        return Response(
            {"error": "Location classification not found for this examination"},
            status=404,
        )

    # Get choices for this specific classification
    choices = location_classification.get_choices()
    return Response(
        [
            {"id": c.id, "name": c.name, "classificationId": location_classification_id}
            for c in choices
        ]
    )

@api_view(["GET"])
def get_interventions_for_finding(request, exam_id, finding_id):
    """
    Retrieves interventions linked to a specific finding within an examination.
    
    Returns a JSON list of interventions with their IDs and names. Responds with a 404 error if the finding does not belong to the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    finding = get_object_or_404(Finding, id=finding_id)

    # Validate that the finding belongs to this examination
    exam_findings = exam.get_available_findings()
    if finding not in exam_findings:
        return Response({"error": "Finding not found for this examination"}, status=404)

    # Get interventions for this specific finding
    interventions = finding.finding_interventions.all()
    return Response([{"id": i.id, "name": i.name} for i in interventions])

@api_view(["GET"])
def get_examinations_for_video(request, video_id):
    """
    Placeholder endpoint for retrieving examinations linked to a specific video.
    
    Currently returns an empty list, as the relationship between videos and examinations is not yet implemented.
    """
    from ..models import VideoFile
    _video = get_object_or_404(VideoFile, id=video_id)
    
    # For now, return empty list since video-examination relationship needs to be established
    # TODO: Implement proper video-examination relationship
    return Response([])

@api_view(["GET"])
def get_findings_for_examination(request, examination_id):
    """
    Retrieves findings associated with a specific examination.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/examinations/{examination_id}/findings/
    """
    exam = get_object_or_404(Examination, id=examination_id)
    findings = exam.get_available_findings()
    
    # Return findings with German names and full details
    return Response([
        build_multilingual_response(f)
        for f in findings
    ])

@api_view(["GET"])
def get_location_classifications_for_finding(request, finding_id):
    """
    Retrieves location classifications for a specific finding.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/findings/{finding_id}/location-classifications/
    """
    finding = get_object_or_404(Finding, id=finding_id)
    location_classifications = finding.get_location_classifications()
    
    # Return with choices included and required flag
    result = [
        build_multilingual_response(lc, include_choices=True)
        for lc in location_classifications
    ]
    return Response(result)

@api_view(["GET"])
def get_morphology_classifications_for_finding(request, finding_id):
    """
    Retrieves morphology classifications for a specific finding.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/findings/{finding_id}/morphology-classifications/
    """
    finding = get_object_or_404(Finding, id=finding_id)
    
    # Get required and optional classification types separately
    required_types = finding.required_morphology_classification_types.all()
    optional_types = finding.optional_morphology_classification_types.all()
    
    from endoreg_db.models import FindingClassification
    
    result = []
    # Process required classifications
    for classification_type in required_types:
        classifications = FindingClassification.objects.filter(
            classification_type=classification_type
        )
        for mc in classifications:
            mc_data = build_multilingual_response(mc, include_choices=True)
            mc_data['required'] = True
            result.append(mc_data)
    # Process optional classifications
    for classification_type in optional_types:
        classifications = FindingClassification.objects.filter(
            classification_type=classification_type
        )
        for mc in classifications:
            if any(existing['id'] == mc.id for existing in result):
                continue
            mc_data = build_multilingual_response(mc, include_choices=True)
            mc_data['required'] = False
            result.append(mc_data)
    return Response(result)

@api_view(["GET"])
def get_choices_for_location_classification(request, classification_id):
    """
    Retrieves choices for a specific location classification.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/location-classifications/{classification_id}/choices/
    """
    classification = get_object_or_404(FindingClassification, id=classification_id, classification_types__name__iexact="location")
    choices = classification.get_choices()
    
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)

@api_view(["GET"])
def get_choices_for_morphology_classification(request, classification_id):
    """
    Retrieves choices for a specific morphology classification.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/morphology-classifications/{classification_id}/choices/
    """
    classification = get_object_or_404(FindingClassification, id=classification_id)
    choices = classification.get_choices()
    
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)

# EXISTING ENDPOINTS (KEEPING FOR BACKWARD COMPATIBILITY)


@api_view(["GET"])
def get_location_classification_choices_for_exam(request, exam_id):
    """
    Retrieves distinct location classification choices linked to findings of an examination.
    
    Returns a list of dictionaries with the ID and name of each location classification choice associated with the findings of the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    choices = FindingClassificationChoice.objects.filter(
        location_classifications__in=[
            lc for finding in findings for lc in finding.get_location_classifications()
        ]
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])

@api_view(["GET"])
def get_interventions_for_exam(request, exam_id):
    """
    Retrieves interventions linked to findings of a specific examination.
    
    Returns:
        JSON response with a list of interventions, each containing its ID and name, associated with the findings available for the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    interventions = FindingIntervention.objects.filter(findings__in=findings).distinct()
    return Response([{"id": i.id, "name": i.name} for i in interventions])



# NEW: Video Examination CRUD ViewSet
class VideoExaminationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Video Examination CRUD operations
    Handles POST and PATCH for video examinations at timestamps
    """
    
    def get_queryset(self):
        # Return empty queryset as we handle retrieval manually
        return []
    
    def create(self, request, *args, **kwargs):
        """
        Create a new video examination
        POST /api/examinations/
        
        Expected payload:
        {
            "videoId": 123,
            "timestamp": 45.5,
            "examinationTypeId": 1,
            "findingId": 2,
            "locationClassificationId": 3,
            "locationChoiceId": 4,
            "morphologyClassificationId": 5,
            "morphologyChoiceId": 6,
            "interventionIds": [7, 8],
            "notes": "Sample notes"
        }
        """
        from django.db import transaction
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            data = request.data
            
            # Validate required fields
            required_fields = ['videoId', 'timestamp', 'examinationTypeId', 'findingId']
            for field in required_fields:
                if field not in data or data[field] is None:
                    return Response(
                        {'error': f'Missing or null required field: {field}'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Validate data types
            try:
                video_id = int(data['videoId'])
                timestamp = float(data['timestamp'])
                examination_type_id = int(data['examinationTypeId'])
                finding_id = int(data['findingId'])
            except (ValueError, TypeError) as e:
                return Response(
                    {'error': f'Invalid data type in request: {str(e)}'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate timestamp is not negative
            if timestamp < 0:
                return Response(
                    {'error': 'Timestamp cannot be negative'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            with transaction.atomic():
                # Get video
                try:
                    video = VideoFile.objects.get(id=video_id)
                except VideoFile.DoesNotExist:
                    return Response(
                        {'error': 'Video not found'}, 
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Get examination type
                try:
                    examination = Examination.objects.get(id=examination_type_id)
                except Examination.DoesNotExist:
                    return Response(
                        {'error': 'Examination type not found'}, 
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Get finding
                try:
                    finding = Finding.objects.get(id=finding_id)
                except Finding.DoesNotExist:
                    return Response(
                        {'error': 'Finding not found'}, 
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Validate optional foreign keys if provided
                if data.get('locationClassificationId'):
                    try:
                        FindingClassification.objects.get(id=data['locationClassificationId'], classification_types__name__iexact="location")
                    except FindingClassification.DoesNotExist:
                        return Response(
                            {'error': 'Location classification not found'}, 
                            status=status.HTTP_404_NOT_FOUND
                        )
                
                if data.get('morphologyClassificationId'):
                    try:
                        FindingClassification.objects.get(id=data['morphologyClassificationId'])
                    except FindingClassification.DoesNotExist:
                        return Response(
                            {'error': 'Morphology classification not found'}, 
                            status=status.HTTP_404_NOT_FOUND
                        )
                
                # Create examination record
                examination_data = {
                    'id': f"exam_{video.id}_{timestamp}_{examination.id}",
                    'video_id': video_id,
                    'timestamp': timestamp,
                    'examination_type': examination.name,
                    'finding': finding.name,
                    'location_classification': data.get('locationClassificationId'),
                    'location_choice': data.get('locationChoiceId'),
                    'morphology_classification': data.get('morphologyClassificationId'),
                    'morphology_choice': data.get('morphologyChoiceId'),
                    'interventions': data.get('interventionIds', []),
                    'notes': data.get('notes', ''),
                    'created_at': '2024-01-01T00:00:00Z'  # Placeholder timestamp
                }
                
                logger.info(f"Created video examination for video {video_id} at timestamp {timestamp}")
                return Response(examination_data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Unexpected error creating examination: {str(e)}")
            return Response(
                {'error': 'Internal server error while creating examination'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def update(self, request, *args, **kwargs):
        """
        Update an existing video examination
        PATCH /api/examinations/{id}/
        """
        from django.db import transaction
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            examination_id = kwargs.get('pk')
            if not examination_id:
                return Response(
                    {'error': 'Examination ID is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            data = request.data
            
            # Validate data types for provided fields
            if 'videoId' in data:
                try:
                    data['videoId'] = int(data['videoId'])
                except (ValueError, TypeError):
                    return Response(
                        {'error': 'Invalid videoId format'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            if 'timestamp' in data:
                try:
                    timestamp = float(data['timestamp'])
                    if timestamp < 0:
                        return Response(
                            {'error': 'Timestamp cannot be negative'}, 
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    data['timestamp'] = timestamp
                except (ValueError, TypeError):
                    return Response(
                        {'error': 'Invalid timestamp format'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            with transaction.atomic():
                # Validate foreign keys if provided
                if 'videoId' in data:
                    try:
                        VideoFile.objects.get(id=data['videoId'])
                    except VideoFile.DoesNotExist:
                        return Response(
                            {'error': 'Video not found'}, 
                            status=status.HTTP_404_NOT_FOUND
                        )
                
                if 'examinationTypeId' in data:
                    try:
                        examination_type_id = int(data['examinationTypeId'])
                        Examination.objects.get(id=examination_type_id)
                    except (ValueError, TypeError):
                        return Response(
                            {'error': 'Invalid examination type ID format'}, 
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    except Examination.DoesNotExist:
                        return Response(
                            {'error': 'Examination type not found'}, 
                            status=status.HTTP_404_NOT_FOUND
                        )
                
                if 'findingId' in data:
                    try:
                        finding_id = int(data['findingId'])
                        Finding.objects.get(id=finding_id)
                    except (ValueError, TypeError):
                        return Response(
                            {'error': 'Invalid finding ID format'}, 
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    except Finding.DoesNotExist:
                        return Response(
                            {'error': 'Finding not found'}, 
                            status=status.HTTP_404_NOT_FOUND
                        )
                
                # Return updated examination data
                examination_data = {
                    'id': examination_id,
                    'video_id': data.get('videoId'),
                    'timestamp': data.get('timestamp'),
                    'examination_type': data.get('examinationTypeId'),
                    'finding': data.get('findingId'),
                    'location_classification': data.get('locationClassificationId'),
                    'location_choice': data.get('locationChoiceId'),
                    'morphology_classification': data.get('morphologyClassificationId'),
                    'morphology_choice': data.get('morphologyChoiceId'),
                    'interventions': data.get('interventionIds', []),
                    'notes': data.get('notes', ''),
                    'updated_at': '2024-01-01T00:00:00Z'  # Placeholder timestamp
                }
                
                logger.info(f"Updated video examination {examination_id}")
                return Response(examination_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Unexpected error updating examination {examination_id}: {str(e)}")
            return Response(
                {'error': 'Internal server error while updating examination'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def destroy(self, request, *args, **kwargs):
        """
        Delete a video examination
        DELETE /api/examinations/{id}/
        """
        from django.db import transaction
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            examination_id = kwargs.get('pk')
            if not examination_id:
                return Response(
                    {'error': 'Examination ID is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate examination_id format
            try:
                # For now, we're using string IDs, so just validate it's not empty
                if not str(examination_id).strip():
                    return Response(
                        {'error': 'Invalid examination ID format'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Invalid examination ID format'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            with transaction.atomic():
                # For now, we simulate successful deletion
                # TODO: Implement actual examination record deletion when persistence is added
                
                logger.info(f"Deleted video examination {examination_id}")
                return Response(
                    {'message': f'Examination {examination_id} deleted successfully'}, 
                    status=status.HTTP_204_NO_CONTENT
                )
            
        except Exception as e:
            logger.error(f"Unexpected error deleting examination {examination_id}: {str(e)}")
            return Response(
                {'error': 'Internal server error while deleting examination'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
