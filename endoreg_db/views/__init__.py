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

from .finding import get_interventions_for_finding, FindingViewSet
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
    update_lvs_from_annotation
)

from .patient_examination import (
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
    PatientExaminationViewSet,
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
    'update_lvs_from_annotation'
]