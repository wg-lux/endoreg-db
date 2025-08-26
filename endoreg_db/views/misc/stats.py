from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Q
import logging

from ...models import (
    VideoFile, LabelVideoSegment, Examination, 
    SensitiveMeta, PatientExamination
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


class ExaminationStatsView(APIView):
    """
    API-Endpoint für Examination-Statistiken
    GET /api/examinations/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        try:
            # Examination-basierte Statistiken
            total_examinations = Examination.objects.count()
            patient_examinations_count = PatientExamination.objects.count()
            
            # Examination-Typ Verteilung
            examination_types = Examination.objects.values('name').annotate(
                count=Count('id')
            ).order_by('-count')[:10]
            
            # Recent examinations
            recent_examinations = PatientExamination.objects.select_related(
                'patient', 'examination'
            ).order_by('-date_start')[:5]
            
            recent_data = []
            for exam in recent_examinations:
                patient = exam.patient
                if not patient:
                    logger.warning(f"Patient not found for examination ID {exam.id}")
                    continue
                recent_data.append({
                    'id': exam.id,
                    'patient_name': f"{patient.first_name} {patient.last_name}".strip() if patient.first_name or patient.last_name else 'Unknown',
                    'examination_type': exam.examination.name if exam.examination else 'Unknown',
                    'date': exam.date_start.isoformat() if exam.date_start else None,
                })
            
            stats_data = {
                'total_examinations': total_examinations,
                'total_patient_examinations': patient_examinations_count,
                'examination_types': examination_types,
                'recent_examinations': recent_data,
                'status': 'success'
            }
            
            return Response(stats_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error fetching examination stats: {str(e)}")
            return Response(
                {'error': 'Failed to fetch examination statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoSegmentStatsView(APIView):
    """
    API-Endpoint für Video-Segment-Statistiken
    GET /api/video-segments/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        try:
            # Video-Segment Statistiken
            total_segments = LabelVideoSegment.objects.count()
            total_videos = VideoFile.objects.count()
            videos_with_segments = VideoFile.objects.filter(
                label_video_segments__isnull=False
            ).distinct().count()
            
            # Label-Verteilung
            label_distribution = LabelVideoSegment.objects.select_related('label').values(
                'label__name'
            ).annotate(
                count=Count('id')
            ).order_by('-count')
            
            # Video-Segment-Status
            videos_without_segments = total_videos - videos_with_segments
            
            stats_data = {
                'total_segments': total_segments,
                'total_videos': total_videos,
                'videos_with_segments': videos_with_segments,
                'videos_without_segments': videos_without_segments,
                'label_distribution': label_distribution,
                'status': 'success'
            }
            
            return Response(stats_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error fetching video segment stats: {str(e)}")
            return Response(
                {'error': 'Failed to fetch video segment statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SensitiveMetaStatsView(APIView):
    """
    API-Endpoint für SensitiveMeta-Statistiken
    GET /api/video/sensitivemeta/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        try:
            # SensitiveMeta Statistiken
            total_sensitive_meta = SensitiveMeta.objects.count()
            
            # Verifikationsstatus
            verified_meta = SensitiveMeta.objects.filter(
                state__dob_verified=True,
                state__names_verified=True
            ).count()
            
            pending_verification = total_sensitive_meta - verified_meta
            
            # Patientendaten-Status
            complete_patient_data = SensitiveMeta.objects.filter(
                patient_first_name__isnull=False,
                patient_last_name__isnull=False,
                patient_dob__isnull=False
            ).exclude(
                Q(patient_first_name='') | Q(patient_last_name='')
            ).count()
            
            incomplete_patient_data = total_sensitive_meta - complete_patient_data
            
            # Recent meta entries
            recent_meta = SensitiveMeta.objects.select_related('center').order_by('-id')[:5]
            recent_data = []
            for meta in recent_meta:
                recent_data.append({
                    'id': meta.id,
                    'patient_name': f"{meta.patient_first_name or 'Unknown'} {meta.patient_last_name or 'Unknown'}",
                    'center': meta.center.name if meta.center else 'Unknown',
                    'examination_date': meta.examination_date.isoformat() if meta.examination_date else None,
                })
            
            stats_data = {
                'total_sensitive_meta': total_sensitive_meta,
                'verified_meta': verified_meta,
                'pending_verification': pending_verification,
                'complete_patient_data': complete_patient_data,
                'incomplete_patient_data': incomplete_patient_data,
                'recent_meta': recent_data,
                'status': 'success'
            }
            
            return Response(stats_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error fetching sensitive meta stats: {str(e)}")
            return Response(
                {'error': 'Failed to fetch sensitive meta statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GeneralStatsView(APIView):
    """
    API-Endpoint für allgemeine Dashboard-Statistiken
    GET /api/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        try:
            # Allgemeine Übersicht
            total_videos = VideoFile.objects.count()
            total_segments = LabelVideoSegment.objects.count()
            total_examinations = PatientExamination.objects.count()
            total_patients = SensitiveMeta.objects.values('patient_first_name', 'patient_last_name').distinct().count()
            
            # System-Status
            videos_with_segments = VideoFile.objects.filter(
                label_video_segments__isnull=False
            ).distinct().count()
            
            processing_completion = (videos_with_segments / total_videos * 100) if total_videos > 0 else 0
            
            stats_data = {
                'overview': {
                    'total_videos': total_videos,
                    'total_segments': total_segments,
                    'total_examinations': total_examinations,
                    'total_patients': total_patients,
                },
                'system_status': {
                    'videos_processed': videos_with_segments,
                    'processing_completion_percent': round(processing_completion, 1),
                },
                'status': 'success'
            }
            
            return Response(stats_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error fetching general stats: {str(e)}")
            return Response(
                {'error': 'Failed to fetch general statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )