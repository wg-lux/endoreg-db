from .correction import (
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoAnalyzeView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
    VideoReprocessView,
)

# Phase 1.2: Media Management Views ✅ IMPLEMENTED
from ..media.video_media import VideoMediaView

# TODO Phase 1.2+: Future views
# from .media import (
#     VideoCorrectionView,
#     TaskStatusView,
#     VideoDownloadProcessedView,
# )

from .reimport import (
    VideoReimportView
)

from .segmentation import (
    VideoViewSet,
    VideoLabelView,
    UpdateLabelSegmentsView,
    rerun_segmentation,
)

# Phase 3.2: VideoStreamView moved to dedicated module
from .video_stream import VideoStreamView

from .timeline import (
    video_timeline_view
)

from .video_examination_viewset import VideoExaminationViewSet

__all__ = [
    # Video Correction (Phase 1.1) - Implemented
    'VideoMetadataView',
    'VideoProcessingHistoryView',
    'VideoAnalyzeView',
    'VideoApplyMaskView',
    'VideoRemoveFramesView',
    'VideoReprocessView',
    
    # Phase 1.2: Media Management Views ✅ IMPLEMENTED
    'VideoMediaView',
    
    # TODO Phase 1.2+: Future views
    # 'VideoCorrectionView',
    # 'TaskStatusView',
    # 'VideoDownloadProcessedView',

    # Reimport views
    'VideoReimportView',

    # Segmentation views
    'VideoViewSet',
    'VideoStreamView',
    'VideoLabelView',
    'UpdateLabelSegmentsView',
    'rerun_segmentation',

    # Timeline views
    'video_timeline_view',

    # Video Examination ViewSet
    "VideoExaminationViewSet",
]
