from ..utils.translation import build_multilingual_response
from .anonymization import (
    AnonymizationOverviewView,
    AnonymizationValidateView,
    anonymization_current,
    anonymization_status,
    start_anonymization,
)
from .auth import KeycloakVideoView, keycloak_callback, keycloak_login, public_home
from .examination import (
    ExaminationManifestCache,
    ExaminationViewSet,
    get_classification_choices_for_examination,
    get_classifications_for_examination,
    get_findings_for_examination,
    get_instruments_for_examination,
    get_interventions_for_examination,
    get_location_classification_choices_for_examination,
    get_location_classifications_for_examination,
    get_morphology_classification_choices_for_examination,
    get_morphology_classifications_for_examination,
)
from .finding import (
    FindingViewSet,
    get_classifications_for_finding,
    get_interventions_for_finding,
)
from .finding_classification import (
    FindingClassificationViewSet,
    get_classification_choices,
    get_location_choices,  # DEPRECATED
    get_morphology_choices,  # DEPRECATED
)
from .media import (
    get_sensitive_metadata_pk,
    label_list,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_list,
    pdf_sensitive_metadata_verify,
    sensitive_metadata_list,
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
)
from .meta import SensitiveMetaListView, SensitiveMetaVerificationView
from .misc import (
    CenterViewSet,
    ExaminationStatsView,
    GenderViewSet,
    GeneralStatsView,
    SensitiveMetaStatsView,
    UploadFileView,
    UploadStatusView,
    VideoSegmentStatsView,
    csrf_token_view,
)
from .patient import PatientViewSet
from .patient_examination import (
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
    PatientExaminationViewSet,
)
from .patient_finding import OptimizedPatientFindingViewSet, PatientFindingViewSet
from .patient_finding_classification import create_patient_finding_classification
from .report import ReportReimportView, ReportStreamView
from .requirement import LookupViewSet, evaluate_requirements
from .video import (  # Video Correction (Phase 1.1) - Implemented; Existing views
    VideoApplyMaskView,
    VideoCorrectionView,
    VideoExaminationViewSet,
    VideoReimportView,
    VideoRemoveFramesView,
    VideoStreamView,
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
    "ExaminationViewSet",
    "get_classification_choices_for_examination",
    "get_morphology_classification_choices_for_examination",
    "get_location_classification_choices_for_examination",
    "get_classifications_for_examination",
    "get_location_classifications_for_examination",
    "get_morphology_classifications_for_examination",
    "get_findings_for_examination",
    "get_instruments_for_examination",
    "get_interventions_for_examination",
    # Finding Views
    "FindingViewSet",
    "get_interventions_for_finding",
    "get_classifications_for_finding",
    # Finding Classification Views
    "FindingClassificationViewSet",
    "get_classification_choices",
    "get_morphology_choices",  # DEPRECATED
    "get_location_choices",  # DEPRECATED
    # Meta Views
    "SensitiveMetaListView",
    "SensitiveMetaVerificationView",
    # Misc
    "CenterViewSet",
    "csrf_token_view",
    "GenderViewSet",
    "ExaminationStatsView",
    "VideoSegmentStatsView",
    "SensitiveMetaStatsView",
    "GeneralStatsView",
    "build_multilingual_response",
    "UploadFileView",
    "UploadStatusView",
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
    # report
    "ReportReimportView",
    "ReportStreamView",
    # Requirement Views
    "evaluate_requirements",
    "LookupViewSet",
    # Video Views (Phase 1.1 - Implemented)
    "VideoApplyMaskView",
    "VideoRemoveFramesView",
    "VideoCorrectionView",
    "VideoReimportView",
    "VideoStreamView",
    "VideoExaminationViewSet",
    "ReportReimportView",
    "label_list",
    "get_sensitive_metadata_pk",
    "video_sensitive_metadata",
    "video_sensitive_metadata_verify",
    "pdf_sensitive_metadata",
    "pdf_sensitive_metadata_verify",
    "sensitive_metadata_list",
    "pdf_sensitive_metadata_list",
]
