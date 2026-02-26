from .center import CenterViewSet
from .application_settings import (
    application_settings_detail,
    application_settings_centers_dropdown,
    application_settings_processors_dropdown,
    application_settings_annotators_dropdown,
    application_settings_report_templates_dropdown,
)
from .csrf import csrf_token_view
from .gender import GenderViewSet
from .stats import (
    ExaminationStatsView,
    VideoSegmentStatsView,
    SensitiveMetaStatsView,
    GeneralStatsView,
)

from .upload_views import (
    UploadFileView,
    UploadStatusView,
)

__all__ = [
    "CenterViewSet",
    "application_settings_detail",
    "application_settings_centers_dropdown",
    "application_settings_processors_dropdown",
    "application_settings_annotators_dropdown",
    "application_settings_report_templates_dropdown",
    "csrf_token_view",
    # Gender view
    "GenderViewSet",
    # Stats views
    "ExaminationStatsView",
    "VideoSegmentStatsView",
    "SensitiveMetaStatsView",
    "GeneralStatsView",
    # Upload views
    "UploadFileView",
    "UploadStatusView",
]
