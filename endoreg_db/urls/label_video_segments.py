from django.urls import path
from endoreg_db.views import (
    create_video_segment_annotation, 
    update_lvs_from_annotation,
    UpdateAnonymizedTextView,
    SensitiveMetaDetailView,
    video_segments_view,
    video_segment_detail_view,

)

url_patterns = [
    path(
        'annotations/',
        create_video_segment_annotation,
        name='create_annotation'
    ),
    path(
        'annotations/<int:annotation_id>/',
        update_lvs_from_annotation,
        name='update_annotation'
    ),
    path(
        'save-anonymization-annotation-pdf/<int:annotation_id>/',
        UpdateAnonymizedTextView.as_view(),
        name='save_anonymization_annotation'
    ),
    path(
        'save-anonymization-annotation-video/<int:annotation_id>/',
        SensitiveMetaDetailView.as_view(),
        name='save_anonymization_annotation_video'
    ),
    # Label Video Segment API endpoints
    path(
        'video-segments/',
        video_segments_view,
        name='video_segments'
    ),
    path(
        'video-segments/<int:segment_id>/',
        video_segment_detail_view,
        name='video_segment_detail'
    ),

]