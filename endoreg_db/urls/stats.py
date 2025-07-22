from django.urls import path

from endoreg_db.views import (
    ExaminationStatsView,
    VideoSegmentStatsView,
    SensitiveMetaStatsView,
    GeneralStatsView,
)

url_patterns = [
    # ---------------------------------------------------------------------------------------
    # STATISTICS API ENDPOINTS
    #
    # Diese Endpunkte stellen Dashboard-Statistiken bereit für das Frontend
    # ---------------------------------------------------------------------------------------

    # Examination Statistics API
    # GET /api/examinations/stats/
    # Liefert Statistiken über Examinations und PatientExaminations
    path('examinations/stats/', ExaminationStatsView.as_view(), name='examination_stats'),

    # Video Segment Statistics API  
    # GET /api/video-segment/stats/  (Note: singular 'segment' to match frontend)
    # Liefert Statistiken über Video-Segmente und Label-Verteilung
    path('video-segment/stats/', VideoSegmentStatsView.as_view(), name='video_segment_stats'),

    # Alternative Video Segments Statistics API (plural version for compatibility)
    # GET /api/video-segments/stats/
    path('video-segments/stats/', VideoSegmentStatsView.as_view(), name='video_segments_stats'),

    # Sensitive Meta Statistics API
    # GET /api/video/sensitivemeta/stats/
    # Liefert Statistiken über SensitiveMeta-Einträge und Verifikationsstatus
    path('video/sensitivemeta/stats/', SensitiveMetaStatsView.as_view(), name='sensitive_meta_stats'),

    # General Dashboard Statistics API
    # GET /api/stats/
    # Liefert allgemeine Übersichtsstatistiken für das Dashboard
    path('stats/', GeneralStatsView.as_view(), name='general_stats'),

    path(
    'video-segments/stats/', 
    VideoSegmentStatsView.as_view(), 
    name='video_segments_stats'
    ),
]