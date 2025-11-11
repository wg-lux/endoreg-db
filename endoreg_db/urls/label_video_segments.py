from django.urls import path
from endoreg_db.views import (
    create_video_segment_annotation, 
    update_label_video_segment,
    SensitiveMetaDetailView,
    video_segments_view,
    video_segment_detail_view,
    get_lvs_by_name_and_video_id
)



url_patterns = [
    path(
        "lvs/by-label-name/<str:label_name>/by-video-id/<int:video_id>/",
        get_lvs_by_name_and_video_id,
        name='get_lvs_by_name_and_video_id'
    ),
    path(
        'annotations/',
        create_video_segment_annotation,
        name='create_annotation'
    ),
    path(
        'annotations/<int:annotation_id>/',
        update_label_video_segment,
        name='update_annotation'
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