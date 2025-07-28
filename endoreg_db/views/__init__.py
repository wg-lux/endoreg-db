from .anonymization import (
    anonymization_overview,
    anonymization_status,
    anonymization_current,
    start_anonymization,
    validate_anonymization
)


from ..utils.translation import build_multilingual_response


from .auth import (
    KeycloakVideoView,
    keycloak_login,
    keycloak_callback,
    public_home,
)

from .examination import (
    ExaminationManifestCache,
    ExaminationViewSet,

    get_classification_choices_for_examination,
    get_morphology_classification_choices_for_examination,
    get_location_classification_choices_for_examination,

    get_classifications_for_examination,
    get_location_classifications_for_examination,
    get_morphology_classifications_for_examination,

    get_findings_for_examination,
    get_instruments_for_examination,
    get_interventions_for_examination,
)

from .finding import (
    get_interventions_for_finding,
    get_classifications_for_finding,
    FindingViewSet
)

from .finding_classification import (
    FindingClassificationViewSet,
    get_classification_choices,
    get_morphology_choices, # DEPRECATED
    get_location_choices, # DEPRECATED
)

from .label_video_segment import (
    create_video_segment_annotation,
    video_segments_by_label_id_view,
    video_segments_by_label_name_view,
    video_segment_detail_view,
    video_segments_view,
    update_label_video_segment,
    get_lvs_by_name_and_video_id
)

from .meta import (
    AvailableFilesListView,
    SensitiveMetaDetailView,
    SensitiveMetaListView,
    SensitiveMetaVerificationView,
    ReportFileMetadataView,
)

from .misc import (
    CenterViewSet,
    csrf_token_view,
    GenderViewSet,
    ExaminationStatsView,
    VideoSegmentStatsView,
    SensitiveMetaStatsView,
    GeneralStatsView,
    SecureFileUrlView,
    SecureFileServingView,
    validate_secure_url,
    ExaminationTranslationOptions,
    FindingTranslationOptions,
    FindingClassificationTranslationOptions,
    FindingClassificationChoiceTranslationOptions,
    InterventionTranslationOptions,
    TranslatedFieldMixin,
    TranslationMigrationHelper,
    TranslatedFixtureLoader,
    MODELTRANSLATION_SETTINGS,
    UploadFileView,
    UploadStatusView,
)

from .patient import PatientViewSet

from .patient_examination import (
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
    PatientExaminationViewSet,
)

from .patient_finding import (
    PatientFindingViewSet,
    OptimizedPatientFindingViewSet
)

from .patient_finding_classification import (
    create_patient_finding_classification,
)

from .pdf import (
    ClosingFileWrapper,
    PDFStreamView,
    UpdateAnonymizedTextView,
    RawPdfAnonyTextView,
    PDFFileForMetaView,
    UpdateSensitiveMetaView,
)

from .report import (
    ReportListView,
    ReportWithSecureUrlView,
    start_examination,
)

from .video import (
    VideoMediaView,
    VideoCorrectionView,
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoAnalyzeView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
    VideoReprocessView,
    TaskStatusView,
    VideoDownloadProcessedView,
    VideoReimportView,
    VideoViewSet,
    VideoStreamView,
    VideoLabelView,
    UpdateLabelSegmentsView,
    rerun_segmentation,
    video_timeline_view,
    VideoExaminationViewSet,
)

__all__ = [
    # Anonymization views
    "anonymization_overview",
    "anonymization_status",
    "anonymization_current",
    "start_anonymization",
    "validate_anonymization",

    # Auth views
    "KeycloakVideoView",
    "keycloak_login",
    "keycloak_callback",
    "public_home",

    # Examination views
    "ExaminationManifestCache",
    'ExaminationViewSet',

    'get_classification_choices_for_examination',
    'get_morphology_classification_choices_for_examination',
    'get_location_classification_choices_for_examination',

    'get_classifications_for_examination',
    'get_location_classifications_for_examination',
    'get_morphology_classifications_for_examination',

    'get_findings_for_examination',
    'get_instruments_for_examination',
    'get_interventions_for_examination',

    # Finding Views
    "FindingViewSet",
    "get_interventions_for_finding",
    "get_classifications_for_finding",

    # Finding Classification Views
    "FindingClassificationViewSet",
    "get_classification_choices",
    "get_morphology_choices", # DEPRECATED
    "get_location_choices", # DEPRECATED

    # Label Video Segment Views
    'create_video_segment_annotation',
    'video_segments_by_label_id_view',
    'video_segments_by_label_name_view',
    'video_segment_detail_view',
    'video_segments_view',
    'update_label_video_segment',
    "get_lvs_by_name_and_video_id",

    # Meta Views
    "AvailableFilesListView",
    "SensitiveMetaDetailView",
    "SensitiveMetaListView",
    "SensitiveMetaVerificationView",
    "ReportFileMetadataView",

    # Misc
    "CenterViewSet",
    'csrf_token_view',
    "GenderViewSet",
    'ExaminationStatsView',
    'VideoSegmentStatsView',
    'SensitiveMetaStatsView',
    "GeneralStatsView",
    "SecureFileUrlView",
    "SecureFileServingView",
    "validate_secure_url",
    'ExaminationTranslationOptions',
    'FindingTranslationOptions',
    'FindingClassificationTranslationOptions',
    'FindingClassificationChoiceTranslationOptions',
    'InterventionTranslationOptions',
    'TranslatedFieldMixin',
    'TranslationMigrationHelper',
    'TranslatedFixtureLoader',
    "build_multilingual_response",
    'MODELTRANSLATION_SETTINGS',
    'UploadFileView',
    'UploadStatusView',

    # Patient Views
    "PatientViewSet",

    # Patient Examination Views
    "ExaminationCreateView",
    "PatientExaminationDetailView",
    "PatientExaminationListView",
    "PatientExaminationViewSet",

    # Patient Finding Views
    "PatientFindingViewSet",
    "OptimizedPatientFindingViewSet",

    # Patient Finding Classification Views
    "create_patient_finding_classification",

    # PDF
    "ClosingFileWrapper",
    "PDFStreamView",
    "UpdateAnonymizedTextView",
    "RawPdfAnonyTextView",
    "PDFFileForMetaView",
    "UpdateSensitiveMetaView",

    # Report
    "ReportListView",
    "ReportWithSecureUrlView",
    "start_examination",

    # Video Views
    'VideoMediaView',
    'VideoCorrectionView',
    'VideoMetadataView',
    'VideoProcessingHistoryView',
    'VideoAnalyzeView',
    'VideoApplyMaskView',
    'VideoRemoveFramesView',
    'VideoReprocessView',
    'TaskStatusView',
    'VideoDownloadProcessedView',
    'VideoReimportView',
    'VideoViewSet',
    'VideoStreamView',
    'VideoLabelView',
    'UpdateLabelSegmentsView',
    'rerun_segmentation',
    'video_timeline_view',
    "VideoExaminationViewSet",
]