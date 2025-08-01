# from rest_framework import viewsets, status
# from rest_framework.response import Response
# from django.shortcuts import get_object_or_404
# from endoreg_db.models import Examination, VideoFile, Finding, FindingClassification
# from django.db import transaction
# from django.utils import timezone
# import logging


# class VideoExaminationViewSet(viewsets.ModelViewSet):
#     """
#     ViewSet for Video Examination CRUD operations
#     Handles POST and PATCH for video examinations at timestamps
#     """
#     def get_queryset(self):
#         return []

#     def create(self, request, *args, **kwargs):

#         logger = logging.getLogger(__name__)
#         try:
#             data = request.data
#             required_fields = ['videoId', 'timestamp', 'examinationTypeId', 'findingId']
#             for field in required_fields:
#                 if field not in data or data[field] is None:
#                     return Response({'error': f'Missing or null required field: {field}'}, status=status.HTTP_400_BAD_REQUEST)
#             try:
#                 video_id = int(data['videoId'])
#                 timestamp = float(data['timestamp'])
#                 examination_type_id = int(data['examinationTypeId'])
#                 finding_id = int(data['findingId'])
#             except (ValueError, TypeError) as e:
#                 return Response({'error': f'Invalid data type in request: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)
#             if timestamp < 0:
#                 return Response({'error': 'Timestamp cannot be negative'}, status=status.HTTP_400_BAD_REQUEST)
#             with transaction.atomic():
#                 try:
#                     video = VideoFile.objects.get(id=video_id)
#                 except VideoFile.DoesNotExist:
#                     return Response({'error': 'Video not found'}, status=status.HTTP_404_NOT_FOUND)
#                 try:
#                     examination = Examination.objects.get(id=examination_type_id)
#                 except Examination.DoesNotExist:
#                     return Response({'error': 'Examination type not found'}, status=status.HTTP_404_NOT_FOUND)
#                 try:
#                     finding = Finding.objects.get(id=finding_id)
#                 except Finding.DoesNotExist:
#                     return Response({'error': 'Finding not found'}, status=status.HTTP_404_NOT_FOUND)
#                 if data.get('locationClassificationId'):
#                     try:
#                         FindingClassification.objects.get(id=data['locationClassificationId'], classification_types__name__iexact="location")
#                     except FindingClassification.DoesNotExist:
#                         return Response({'error': 'Location classification not found'}, status=status.HTTP_404_NOT_FOUND)
#                 if data.get('morphologyClassificationId'):
#                     try:
#                         FindingClassification.objects.get(id=data['morphologyClassificationId'], classification_types__name__iexact="morphology")
#                     except FindingClassification.DoesNotExist:
#                         return Response({'error': 'Morphology classification not found'}, status=status.HTTP_404_NOT_FOUND)
#                 examination_data = {
#                     'id': f"exam_{video.id}_{timestamp}_{examination.id}",
#                     'video_id': video_id,
#                     'timestamp': timestamp,
#                     'examination_type': examination.name,
#                     'finding': finding.name,
#                     'location_classification': data.get('locationClassificationId'),
#                     'location_choice': data.get('locationChoiceId'),
#                     'morphology_classification': data.get('morphologyClassificationId'),
#                     'morphology_choice': data.get('morphologyChoiceId'),
#                     'interventions': data.get('interventionIds', []),
#                     'notes': data.get('notes', ''),
#                     'created_at': timezone.now().isoformat()
#                 }
#                 logger.info(f"Created video examination for video {video_id} at timestamp {timestamp}")
#                 return Response(examination_data, status=status.HTTP_201_CREATED)
#         except Exception as e:
#             logger.error(f"Unexpected error creating examination: {str(e)}")
#             return Response({'error': 'Internal server error while creating examination'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#     def update(self, request, *args, **kwargs):
#         logger = logging.getLogger(__name__)
#         try:
#             examination_id = kwargs.get('pk')
#             if not examination_id:
#                 return Response({'error': 'Examination ID is required'}, status=status.HTTP_400_BAD_REQUEST)
#             data = request.data
#             if 'videoId' in data:
#                 try:
#                     data['videoId'] = int(data['videoId'])
#                 except (ValueError, TypeError):
#                     return Response({'error': 'Invalid videoId format'}, status=status.HTTP_400_BAD_REQUEST)
#             if 'timestamp' in data:
#                 try:
#                     timestamp = float(data['timestamp'])
#                     if timestamp < 0:
#                         return Response({'error': 'Timestamp cannot be negative'}, status=status.HTTP_400_BAD_REQUEST)
#                     data['timestamp'] = timestamp
#                 except (ValueError, TypeError):
#                     return Response({'error': 'Invalid timestamp format'}, status=status.HTTP_400_BAD_REQUEST)
#             with transaction.atomic():
#                 if 'videoId' in data:
#                     try:
#                         VideoFile.objects.get(id=data['videoId'])
#                     except VideoFile.DoesNotExist:
#                         return Response({'error': 'Video not found'}, status=status.HTTP_404_NOT_FOUND)
#                 if 'examinationTypeId' in data:
#                     try:
#                         examination_type_id = int(data['examinationTypeId'])
#                         Examination.objects.get(id=examination_type_id)
#                     except (ValueError, TypeError):
#                         return Response({'error': 'Invalid examination type ID format'}, status=status.HTTP_400_BAD_REQUEST)
#                     except Examination.DoesNotExist:
#                         return Response({'error': 'Examination type not found'}, status=status.HTTP_404_NOT_FOUND)
#                 if 'findingId' in data:
#                     try:
#                         finding_id = int(data['findingId'])
#                         Finding.objects.get(id=finding_id)
#                     except (ValueError, TypeError):
#                         return Response({'error': 'Invalid finding ID format'}, status=status.HTTP_400_BAD_REQUEST)
#                     except Finding.DoesNotExist:
#                         return Response({'error': 'Finding not found'}, status=status.HTTP_404_NOT_FOUND)
#                 examination_data = {
#                     'id': examination_id,
#                     'video_id': data.get('videoId'),
#                     'timestamp': data.get('timestamp'),
#                     'examination_type': data.get('examinationTypeId'),
#                     'finding': data.get('findingId'),
#                     'location_classification': data.get('locationClassificationId'),
#                     'location_choice': data.get('locationChoiceId'),
#                     'morphology_classification': data.get('morphologyClassificationId'),
#                     'morphology_choice': data.get('morphologyChoiceId'),
#                     'interventions': data.get('interventionIds', []),
#                     'notes': data.get('notes', ''),
#                     'updated_at': '2024-01-01T00:00:00Z'
#                 }
#                 logger.info(f"Updated video examination {examination_id}")
#                 return Response(examination_data, status=status.HTTP_200_OK)
#         except Exception as e:
#             logger.error(f"Unexpected error updating examination {examination_id}: {str(e)}")
#             return Response({'error': 'Internal server error while updating examination'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#     def destroy(self, request, *args, **kwargs):
#         logger = logging.getLogger(__name__)
#         try:
#             examination_id = kwargs.get('pk')
#             if not examination_id:
#                 return Response({'error': 'Examination ID is required'}, status=status.HTTP_400_BAD_REQUEST)
#             try:
#                 if not str(examination_id).strip():
#                     return Response({'error': 'Invalid examination ID format'}, status=status.HTTP_400_BAD_REQUEST)
#             except (ValueError, TypeError):
#                 return Response({'error': 'Invalid examination ID format'}, status=status.HTTP_400_BAD_REQUEST)
#             with transaction.atomic():
#                 logger.info(f"Deleted video examination {examination_id}")
#                 return Response({'message': f'Examination {examination_id} deleted successfully'}, status=status.HTTP_204_NO_CONTENT)
#         except Exception as e:
#             logger.error(f"Unexpected error deleting examination {examination_id}: {str(e)}")
#             return Response({'error': 'Internal server error while deleting examination'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# from rest_framework.decorators import api_view
# @api_view(["GET"])
# def get_examinations_for_video(request, video_id):
#     _video = get_object_or_404(VideoFile, id=video_id)
#     #TODO no functionality implemented yet
#     return Response([])
