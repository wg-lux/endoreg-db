from .anonymization import (
    AnonymizationOverviewView,
    AnonymizationValidateView,
    anonymization_status,
    anonymization_current,
    start_anonymization,
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


from .meta import (
    SensitiveMetaListView,
    SensitiveMetaVerificationView,
)

from .misc import (
    CenterViewSet,
    csrf_token_view,
    GenderViewSet,
    ExaminationStatsView,
    VideoSegmentStatsView,
    SensitiveMetaStatsView,
    GeneralStatsView,
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
    PdfReimportView,
    PdfStreamView,
)
from .requirement import (
    evaluate_requirements,
    LookupViewSet,
)

from .video import (
    # Video Correction (Phase 1.1) - Implemented
    VideoProcessingHistoryView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
    
    # Existing views
    VideoReimportView,
    VideoStreamView,
    VideoExaminationViewSet,
    VideoCorrectionView,
)

from .media import (
    PdfReimportView,
    label_list,
    get_sensitive_metadata_pk,
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_verify,
    sensitive_metadata_list,
    pdf_sensitive_metadata_list,
)

__all__ = [
    # Anonymization views
    "anonymization_status",
    "anonymization_current",
    "start_anonymization",
    "AnonymizationOverviewView",
    "AnonymizationValidateView",

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


    # Meta Views
    "SensitiveMetaListView",
    "SensitiveMetaVerificationView",

    # Misc
    "CenterViewSet",
    'csrf_token_view',
    "GenderViewSet",
    'ExaminationStatsView',
    'VideoSegmentStatsView',
    'SensitiveMetaStatsView',
    "GeneralStatsView",
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
    "PdfReimportView",
    "PdfStreamView",
    
    # Requirement Views
    "evaluate_requirements",
    "LookupViewSet",

    # Video Views (Phase 1.1 - Implemented)
    'VideoProcessingHistoryView',
    'VideoApplyMaskView',
    'VideoRemoveFramesView',
    'VideoCorrectionView',
    'VideoReimportView',
    'VideoStreamView',
    "VideoExaminationViewSet",
    
    "PdfReimportView",
    "label_list",
    "get_sensitive_metadata_pk",
    "video_sensitive_metadata",
    "video_sensitive_metadata_verify",
    "pdf_sensitive_metadata",
    "pdf_sensitive_metadata_verify",
    "sensitive_metadata_list",
    "pdf_sensitive_metadata_list",
    
    
]
