"""
Statistics views for the endoreg_db API.

Provides dashboard statistics for the frontend including examination stats,
video segment stats, sensitive meta stats, and general overview stats.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta

from ...models import (
    VideoFile, 
    LabelVideoSegment, 
    SensitiveMeta, 
    Examination,
    PatientExamination,
    Label
)
from ...utils.permissions import EnvironmentAwarePermission


class ExaminationStatsView(APIView):
    """
    Statistics about examinations and patient examinations.
    
    GET /api/examinations/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        """
        Returns statistics about examinations including total counts,
        recent activity, and status distribution.
        """
        try:
            # Total examination counts
            total_examinations = Examination.objects.count()
            total_patient_examinations = PatientExamination.objects.count()
            
            # Recent activity (last 30 days)
            thirty_days_ago = timezone.now() - timedelta(days=30)
            recent_examinations = Examination.objects.filter(
                created_at__gte=thirty_days_ago
            ).count() if hasattr(Examination, 'created_at') else 0
            
            # Status distribution for patient examinations
            status_distribution = PatientExamination.objects.values('status').annotate(
                count=Count('id')
            ).order_by('status') if hasattr(PatientExamination, 'status') else []
            
            return Response({
                'total_examinations': total_examinations,
                'total_patient_examinations': total_patient_examinations,
                'recent_examinations': recent_examinations,
                'status_distribution': list(status_distribution)
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                'error': f'Failed to retrieve examination stats: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VideoSegmentStatsView(APIView):
    """
    Statistics about video segments and label distribution.
    
    GET /api/video-segment/stats/
    GET /api/video-segments/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        """
        Returns statistics about video segments including total counts,
        label distribution, and processing status.
        """
        try:
            # Total segment counts
            total_segments = LabelVideoSegment.objects.count()
            total_videos_with_segments = VideoFile.objects.filter(
                labelvideosegment__isnull=False
            ).distinct().count()
            
            # Label distribution
            label_distribution = LabelVideoSegment.objects.values(
                'label__name'
            ).annotate(
                count=Count('id')
            ).order_by('-count')
            
            # Videos without segments
            videos_without_segments = VideoFile.objects.filter(
                labelvideosegment__isnull=True
            ).count()
            
            # Average segments per video
            avg_segments_per_video = 0
            if total_videos_with_segments > 0:
                avg_segments_per_video = round(total_segments / total_videos_with_segments, 2)
            
            return Response({
                'total_segments': total_segments,
                'total_videos_with_segments': total_videos_with_segments,
                'videos_without_segments': videos_without_segments,
                'avg_segments_per_video': avg_segments_per_video,
                'label_distribution': list(label_distribution)
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                'error': f'Failed to retrieve video segment stats: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SensitiveMetaStatsView(APIView):
    """
    Statistics about sensitive metadata entries and verification status.
    
    GET /api/video/sensitivemeta/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        """
        Returns statistics about sensitive metadata including verification status,
        types of sensitive data found, and processing statistics.
        """
        try:
            # Total sensitive meta entries
            total_sensitive_meta = SensitiveMeta.objects.count()
            
            # Verification status distribution
            verified_count = SensitiveMeta.objects.filter(
                verified=True
            ).count() if hasattr(SensitiveMeta, 'verified') else 0
            
            unverified_count = total_sensitive_meta - verified_count
            
            # Videos with sensitive data
            videos_with_sensitive_data = VideoFile.objects.filter(
                sensitivemeta__isnull=False
            ).distinct().count()
            
            # Type distribution (if available)
            type_distribution = []
            if hasattr(SensitiveMeta, 'meta_type'):
                type_distribution = SensitiveMeta.objects.values(
                    'meta_type'
                ).annotate(
                    count=Count('id')
                ).order_by('-count')
            
            return Response({
                'total_sensitive_meta': total_sensitive_meta,
                'verified_count': verified_count,
                'unverified_count': unverified_count,
                'videos_with_sensitive_data': videos_with_sensitive_data,
                'type_distribution': list(type_distribution)
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                'error': f'Failed to retrieve sensitive meta stats: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GeneralStatsView(APIView):
    """
    General dashboard statistics providing an overview of the system.
    
    GET /api/stats/
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request):
        """
        Returns general overview statistics for the dashboard including
        total counts across all major entities.
        """
        try:
            # General counts
            total_videos = VideoFile.objects.count()
            total_labels = Label.objects.count()
            total_segments = LabelVideoSegment.objects.count()
            total_examinations = Examination.objects.count()
            total_sensitive_meta = SensitiveMeta.objects.count()
            
            # Processing status
            videos_with_segments = VideoFile.objects.filter(
                labelvideosegment__isnull=False
            ).distinct().count()
            
            videos_with_sensitive_data = VideoFile.objects.filter(
                sensitivemeta__isnull=False
            ).distinct().count()
            
            # Calculate percentages
            segment_coverage = 0
            sensitive_data_coverage = 0
            if total_videos > 0:
                segment_coverage = round((videos_with_segments / total_videos) * 100, 1)
                sensitive_data_coverage = round((videos_with_sensitive_data / total_videos) * 100, 1)
            
            return Response({
                'totals': {
                    'videos': total_videos,
                    'labels': total_labels,
                    'segments': total_segments,
                    'examinations': total_examinations,
                    'sensitive_meta': total_sensitive_meta
                },
                'coverage': {
                    'segment_coverage_percent': segment_coverage,
                    'sensitive_data_coverage_percent': sensitive_data_coverage
                },
                'processing_status': {
                    'videos_with_segments': videos_with_segments,
                    'videos_with_sensitive_data': videos_with_sensitive_data
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({
                'error': f'Failed to retrieve general stats: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
