from django.urls import URLPattern, path

from endoreg_db.views.media.frame_media import DecodedFrameStreamView, FrameStreamView
from endoreg_db.views.media.export_ready import VideoMarkReadyForExportView
from endoreg_db.views.media.hub import (
    HubTransferCreateView,
    HubTransferMediaUploadView,
    HubTransferStatusView,
    QuarantineApproveDeletionView,
    QuarantineItemListView,
    QuarantineReapApprovedView,
    QuarantineRetainView,
    QuarantineSyncView,
)
from endoreg_db.views.media.patient_media_timeline import PatientMediaTimelineView
from endoreg_db.views.media.anonymization_metrics import AnonymizationMetricsView
from endoreg_db.views.media.pdf_media import (
    PdfMediaView,  # Alias to avoid conflict with legacy pdf.reportMediaView
)
from endoreg_db.views.media.sensitive_metadata import (
    get_sensitive_metadata_pk,
    pdf_case_resolution,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_list,
    pdf_sensitive_metadata_verify,
    sensitive_metadata_list,
    video_case_resolution,
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
)
from endoreg_db.views.media.video_media import VideoMediaView
from endoreg_db.views.report.reimport import ReportLlmJobStatusView, ReportReimportView
from endoreg_db.views.report.pdf_redaction import (
    PdfApplyRedactionsView,
    PdfProcessingHistoryView,
)
from endoreg_db.views.report.report_stream import ReportStreamView
from endoreg_db.views.video.hls_stream import (
    HLSKeyView,
    HLSPlaylistView,
    HLSSegmentView,
)
from endoreg_db.views.video.video_stream import VideoStreamView
from endoreg_db.views.video import (
    VideoReimportView,
    VideoFpsView,
    export_annotated,
    video_segment_detail,
    video_segment_validate,
    video_segments_blacken_outside,
    video_segments_bulk_mutation,
    video_segments_by_video,
    video_segments_stats,
    video_segments_validate_bulk,
    video_segments_validation_status,
    import_prediction_segments_to_manual,
    ensure_segment_annotations_bulk,
    ensure_segment_annotations_for_video,
    ensure_prediction_segment_annotations_bulk,
    ensure_prediction_segment_annotations_for_video,
)
from endoreg_db.views.video.ai import (
    FrameAnnotationBulkUpsertView,
    FrameAnnotationRandomTaskView,
    FrameAnnotationSkipView,
    FrameBoxAnnotationView,
    label_list,
    label_set_list,
    prediction_model_list,
    rerun_prediction_segments,
)
from endoreg_db.views.video.correction import (
    VideoApplyMaskView,
    VideoCorrectionView,
    VideoRemoveFramesView,
)
from endoreg_db.views.video.video_metadata import VideoMetadataStatsView

HUB_TRANSFER_URLPATTERNS: list[URLPattern] = [
    path(
        "media/hub/transfers/",
        HubTransferCreateView.as_view(),
        name="hub-transfer-create",
    ),
    path(
        "media/hub/transfers/<str:transfer_key>/status/",
        HubTransferStatusView.as_view(),
        name="hub-transfer-status",
    ),
    path(
        "media/hub/transfers/<str:transfer_key>/media/",
        HubTransferMediaUploadView.as_view(),
        name="hub-transfer-media-upload",
    ),
]

QUARANTINE_URLPATTERNS: list[URLPattern] = [
    path(
        "media/quarantine/",
        QuarantineItemListView.as_view(),
        name="quarantine-item-list",
    ),
    path(
        "media/quarantine/sync/",
        QuarantineSyncView.as_view(),
        name="quarantine-sync",
    ),
    path(
        "media/quarantine/reap-approved/",
        QuarantineReapApprovedView.as_view(),
        name="quarantine-reap-approved",
    ),
    path(
        "media/quarantine/<uuid:item_id>/approve-deletion/",
        QuarantineApproveDeletionView.as_view(),
        name="quarantine-approve-deletion",
    ),
    path(
        "media/quarantine/<uuid:item_id>/retain/",
        QuarantineRetainView.as_view(),
        name="quarantine-retain",
    ),
]

MEDIA_OVERVIEW_URLPATTERNS: list[URLPattern] = [
    path(
        "media/patients/<int:patient_id>/timeline/",
        PatientMediaTimelineView.as_view(),
        name="patient-media-timeline",
    ),
    path(
        "media/anonymization/metrics/",
        AnonymizationMetricsView.as_view(),
        name="media-anonymization-metrics",
    ),
]

VIDEO_MEDIA_URLPATTERNS: list[URLPattern] = [
    path("media/videos/", VideoMediaView.as_view(), name="video-list"),
    path(
        "media/videos/<int:pk>/", VideoStreamView.as_view(), name="video-detail-stream"
    ),  # Support ?type= params
    path(
        "media/videos/<int:pk>/details/", VideoMediaView.as_view(), name="video-detail"
    ),  # JSON metadata
    path(
        "media/videos/<int:pk>/stream/", VideoStreamView.as_view(), name="video-stream"
    ),  # Legacy support
    path(
        "media/videos/<int:pk>/hls/playlist/",
        HLSPlaylistView.as_view(),
        name="video-hls-playlist",
    ),
    path(
        "media/videos/<int:pk>/hls/key/<uuid:key_id>/",
        HLSKeyView.as_view(),
        name="video-hls-key",
    ),
    path(
        "media/videos/<int:pk>/hls/segments/<uuid:key_id>/<str:segment_name>",
        HLSSegmentView.as_view(),
        name="video-hls-segment",
    ),
    path(
        "media/videos/<int:video_id>/frames/<int:frame_number>/stream/",
        FrameStreamView.as_view(),
        name="video-frame-stream",
    ),
    path(
        "media/videos/<int:video_id>/frames/<int:frame_number>/decoded-stream/",
        DecodedFrameStreamView.as_view(),
        name="video-frame-decoded-stream",
    ),
    path(
        "media/videos/<int:pk>/reimport/",
        VideoReimportView.as_view(),
        name="video-reimport",
    ),
    path(
        "media/videos/<int:pk>/mark-ready-for-export/",
        VideoMarkReadyForExportView.as_view(),
        name="video-mark-ready-for-export",
    ),
    path(
        "media/videos/video-correction/<int:pk>",
        VideoCorrectionView.as_view(),
        name="video-correction",
    ),
    path(
        "media/videos/<int:pk>/metadata/",
        VideoMetadataStatsView.as_view(),
        name="video-metadata",
    ),
    path(
        "media/videos/<int:pk>/fps/",
        VideoFpsView.as_view(),
        name="video-fps",
    ),
    path(
        "media/videos/<int:pk>/apply-mask/",
        VideoApplyMaskView.as_view(),
        name="video-apply-mask",
    ),
    path(
        "media/videos/<int:pk>/remove-frames/",
        VideoRemoveFramesView.as_view(),
        name="video-remove-frames",
    ),
]

VIDEO_ANNOTATION_URLPATTERNS: list[URLPattern] = [
    path(
        "media/videos/export-annotated/",
        export_annotated,
        name="video-annotated-export",
    ),
    path("media/videos/labels/list/", label_list, name="get_lvs_list"),
    path(
        "media/videos/label-sets/list/",
        label_set_list,
        name="video-label-set-list",
    ),
    path(
        "media/videos/prediction-models/list/",
        prediction_model_list,
        name="video-prediction-model-list",
    ),
    path(
        "media/videos/<int:pk>/segments/rerun-predictions/",
        rerun_prediction_segments,
        name="video-segments-rerun-predictions",
    ),
    path(
        "media/videos/<int:pk>/segments/import-predictions/",
        import_prediction_segments_to_manual,
        name="video-segments-import-predictions",
    ),
    path(
        "media/videos/<int:pk>/ensure-segment-annotations/",
        ensure_segment_annotations_for_video,
        name="video-segment-ensure-annotations",
    ),
    path(
        "media/videos/ensure-segment-annotations/",
        ensure_segment_annotations_bulk,
        name="video-segments-ensure-annotations",
    ),
    path(
        "media/videos/<int:pk>/ensure-prediction-segment-annotations/",
        ensure_prediction_segment_annotations_for_video,
        name="video-segment-ensure-prediction-annotations",
    ),
    path(
        "media/videos/ensure-prediction-segment-annotations/",
        ensure_prediction_segment_annotations_bulk,
        name="video-segments-ensure-prediction-annotations",
    ),
    path(
        "media/annotations/frames/bulk-upsert/",
        FrameAnnotationBulkUpsertView.as_view(),
        name="frame-annotations-bulk-upsert",
    ),
    path(
        "media/annotations/frames/random-task/",
        FrameAnnotationRandomTaskView.as_view(),
        name="frame-annotations-random-task",
    ),
    path(
        "media/annotations/frames/skip/",
        FrameAnnotationSkipView.as_view(),
        name="frame-annotations-skip",
    ),
    path(
        "media/annotations/frames/boxes/",
        FrameBoxAnnotationView.as_view(),
        name="frame-box-annotations",
    ),
    path(
        "media/videos/segments/stats/",
        video_segments_stats,
        name="video-segments-stats",
    ),
    path(
        "media/videos/<int:pk>/segments/",
        video_segments_by_video,
        name="video-segments-by-video",
    ),
    path(
        "media/videos/<int:pk>/segments/blacken-outside/",
        video_segments_blacken_outside,
        name="video-segments-blacken-outside",
    ),
    path(
        "media/videos/<int:pk>/segments/bulk/",
        video_segments_bulk_mutation,
        name="video-segments-bulk-mutation",
    ),
    path(
        "media/videos/<int:pk>/segments/<int:segment_id>/",
        video_segment_detail,
        name="video-segment-detail",
    ),
]

VIDEO_SEGMENT_VALIDATION_URLPATTERNS: list[URLPattern] = [
    path(
        "media/videos/<int:pk>/segments/<int:segment_id>/validate/",
        video_segment_validate,
        name="video-segment-validate",
    ),
    path(
        "media/videos/<int:pk>/segments/validate-bulk/",
        video_segments_validate_bulk,
        name="video-segments-validate-bulk",
    ),
    path(
        "media/videos/<int:pk>/segments/validation-status/",
        video_segments_validation_status,
        name="video-segments-validation-status",
    ),
]

SENSITIVE_METADATA_URLPATTERNS: list[URLPattern] = [
    path(
        "media/sensitive-media-id/<int:pk>/<str:media_type>/",
        get_sensitive_metadata_pk,
        name="sm-pk",
    ),
    path(
        "media/videos/<int:pk>/sensitive-metadata/",
        video_sensitive_metadata,
        name="video-sensitive-metadata",
    ),
    path(
        "media/videos/<int:pk>/case-resolution/",
        video_case_resolution,
        name="video-case-resolution",
    ),
    path(
        "media/videos/<int:pk>/sensitive-metadata/verify/",
        video_sensitive_metadata_verify,
        name="video-sensitive-metadata-verify",
    ),
    path(
        "media/pdfs/<int:pk>/sensitive-metadata/",
        pdf_sensitive_metadata,
        name="pdf-sensitive-metadata",
    ),
    path(
        "media/pdfs/<int:pk>/case-resolution/",
        pdf_case_resolution,
        name="pdf-case-resolution",
    ),
    path(
        "media/pdfs/<int:pk>/sensitive-metadata/verify/",
        pdf_sensitive_metadata_verify,
        name="pdf-sensitive-metadata-verify",
    ),
    path(
        "media/sensitive-metadata/",
        sensitive_metadata_list,
        name="sensitive-metadata-list",
    ),
    path(
        "media/pdfs/sensitive-metadata/",
        pdf_sensitive_metadata_list,
        name="pdf-sensitive-metadata-list",
    ),
]

PDF_REPORT_MEDIA_URLPATTERNS: list[URLPattern] = [
    path("media/pdfs/", PdfMediaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PdfMediaView.as_view(), name="pdf-detail"),
    path(
        "media/pdfs/<int:pk>/stream/", ReportStreamView.as_view(), name="pdf-stream"
    ),  # Support ?type=raw|anonymized params
    path(
        "media/pdfs/<int:pk>/reimport/",
        ReportReimportView.as_view(),
        name="report-reimport",
    ),
    path(
        "media/pdfs/<int:pk>/llm-jobs/<str:job_id>/",
        ReportLlmJobStatusView.as_view(),
        name="report-llm-job-status",
    ),
    path(
        "media/pdfs/<int:pk>/apply-redactions/",
        PdfApplyRedactionsView.as_view(),
        name="pdf-apply-redactions",
    ),
    path(
        "media/pdfs/<int:pk>/processing-history/",
        PdfProcessingHistoryView.as_view(),
        name="pdf-processing-history",
    ),
]

urlpatterns: list[URLPattern] = [
    *HUB_TRANSFER_URLPATTERNS,
    *QUARANTINE_URLPATTERNS,
    *MEDIA_OVERVIEW_URLPATTERNS,
    *VIDEO_MEDIA_URLPATTERNS,
    *VIDEO_ANNOTATION_URLPATTERNS,
    *VIDEO_SEGMENT_VALIDATION_URLPATTERNS,
    *SENSITIVE_METADATA_URLPATTERNS,
    *PDF_REPORT_MEDIA_URLPATTERNS,
]
