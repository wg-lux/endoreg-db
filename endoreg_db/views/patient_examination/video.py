from rest_framework import viewsets, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.models import (
    Examination,
    VideoFile,
    Finding,
    FindingClassification,
    PatientExamination, PatientFinding, PatientFindingClassification,
    PatientFindingIntervention,
)
from django.db import DatabaseError, transaction
from django.utils import timezone
import logging
from rest_framework.decorators import api_view

@api_view(["GET"])
def get_examinations_for_video(request, video_id):
    _video = get_object_or_404(VideoFile, id=video_id)
    #TODO no functionality implemented yet
    return Response([])

class VideoPatientExaminationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PatientExamination CRUD operations in the context of video files
    Handles POST and PATCH for video examinations at timestamps
    """


    def get_queryset(self):
        video_id = self.request.query_params.get('videoId')
        if video_id:
            return PatientExamination.objects.filter(video_id=video_id)
        return PatientExamination.objects.all()

    def _validate_create_data(self, request):
        """
        Validate incoming data for create.
        Returns (clean_data, errors) where errors is a dict of field issues.
        """
        data = request.data.copy()
        errors = {}

        # patient_id validation
        if 'patient_id' not in data:
            errors['patient_id'] = 'This field is required.'
        else:
            try:
                data['patient_id'] = int(data['patient_id'])
            except (TypeError, ValueError):
                errors['patient_id'] = 'Must be an integer.'

        # exam_date validation
        if 'exam_date' not in data:
            errors['exam_date'] = 'This field is required.'
        else:
            from django.utils.dateparse import parse_date
            parsed = parse_date(data['exam_date'])
            if not parsed:
                errors['exam_date'] = 'Invalid date format.'
            else:
                data['exam_date'] = parsed

        # video_file presence
        if 'video_file' not in data:
            errors['video_file'] = 'This field is required.'

        return data, errors


    def _create_examination_data(self, validated_data):
        """
        Create examination and video record, then return payload.
        """
        # assume Examination, Video imported above
        exam = Examination.objects.create(
            patient_id=validated_data['patient_id'],
            date=validated_data['exam_date']
        )
        vid = VideoFile.objects.create(
            examination=exam,
            file=validated_data['video_file']
        )
        return {
            'examination_id': exam.id,
            'video_id': vid.id
        }


    def create(self, request, *args, **kwargs):
        """
        Create a new video examination.
        """
        logger = logging.getLogger(__name__)
        clean_data, errors = self._validate_create_data(request)
        if errors:
            return Response({'errors': errors}, status=400)

        try:
            payload = self._create_examination_data(clean_data)
            return Response(payload, status=201)
        except DatabaseError as db_err:
            return Response(
                {'error': f'Database error: {db_err}'},
                status=500
            )
        except Exception:
            logger.exception("Unexpected error in create()")
            return Response(
                {'error': 'Internal server error'},
                status=500
            )

    def update(self, request, *args, **kwargs):
        logger = logging.getLogger(__name__)
        try:
            examination_id = kwargs.get('pk')
            if not examination_id:
                return Response({'error': 'Examination ID is required'}, status=status.HTTP_400_BAD_REQUEST)
            data = request.data
            if 'videoId' in data:
                try:
                    data['videoId'] = int(data['videoId'])
                except (ValueError, TypeError):
                    return Response({'error': 'Invalid videoId format'}, status=status.HTTP_400_BAD_REQUEST)
            if 'timestamp' in data:
                try:
                    timestamp = float(data['timestamp'])
                    if timestamp < 0:
                        return Response({'error': 'Timestamp cannot be negative'}, status=status.HTTP_400_BAD_REQUEST)
                    data['timestamp'] = timestamp
                except (ValueError, TypeError):
                    return Response({'error': 'Invalid timestamp format'}, status=status.HTTP_400_BAD_REQUEST)
            with transaction.atomic():
                if 'videoId' in data:
                    try:
                        VideoFile.objects.get(id=data['videoId'])
                    except VideoFile.DoesNotExist:
                        return Response({'error': 'Video not found'}, status=status.HTTP_404_NOT_FOUND)
                if 'examinationTypeId' in data:
                    try:
                        examination_type_id = int(data['examinationTypeId'])
                        Examination.objects.get(id=examination_type_id)
                    except (ValueError, TypeError):
                        return Response({'error': 'Invalid examination type ID format'}, status=status.HTTP_400_BAD_REQUEST)
                    except Examination.DoesNotExist:
                        return Response({'error': 'Examination type not found'}, status=status.HTTP_404_NOT_FOUND)
                if 'findingId' in data:
                    try:
                        finding_id = int(data['findingId'])
                        Finding.objects.get(id=finding_id)
                    except (ValueError, TypeError):
                        return Response({'error': 'Invalid finding ID format'}, status=status.HTTP_400_BAD_REQUEST)
                    except Finding.DoesNotExist:
                        return Response({'error': 'Finding not found'}, status=status.HTTP_404_NOT_FOUND)
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
                    'updated_at': timezone.now().isoformat()
                }
                logger.info(f"Updated video examination {examination_id}")
                return Response(examination_data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Unexpected error updating examination {examination_id}: {str(e)}")
            return Response({'error': 'Internal server error while updating examination'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        logger = logging.getLogger(__name__)
        try:
            examination_id = kwargs.get('pk')
            if not examination_id:
                return Response({'error': 'Examination ID is required'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                if not str(examination_id).strip():
                    return Response({'error': 'Invalid examination ID format'}, status=status.HTTP_400_BAD_REQUEST)
            except (ValueError, TypeError):
                return Response({'error': 'Invalid examination ID format'}, status=status.HTTP_400_BAD_REQUEST)
            with transaction.atomic():
                logger.info(f"Deleted video examination {examination_id}")
                return Response({'message': f'Examination {examination_id} deleted successfully'}, status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.error(f"Unexpected error deleting examination {examination_id}: {str(e)}")
            return Response({'error': 'Internal server error while deleting examination'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


