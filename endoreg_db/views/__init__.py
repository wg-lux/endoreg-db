from __future__ import annotations

import logging
from importlib import import_module

logger = logging.getLogger(__name__)

_IMPORT_TARGETS: dict[str, tuple[str, str]] = {
    "build_multilingual_response": (
        "endoreg_db.utils.translation",
        "build_multilingual_response",
    ),
    "AnonymizationOverviewView": (
        "endoreg_db.views.anonymization",
        "AnonymizationOverviewView",
    ),
    "AnonymizationValidateView": (
        "endoreg_db.views.anonymization",
        "AnonymizationValidateView",
    ),
    "anonymization_current": (
        "endoreg_db.views.anonymization",
        "anonymization_current",
    ),
    "anonymization_status": ("endoreg_db.views.anonymization", "anonymization_status"),
    "start_anonymization": ("endoreg_db.views.anonymization", "start_anonymization"),
    "KeycloakVideoView": ("endoreg_db.views.auth", "KeycloakVideoView"),
    "keycloak_callback": ("endoreg_db.views.auth", "keycloak_callback"),
    "keycloak_login": ("endoreg_db.views.auth", "keycloak_login"),
    "public_home": ("endoreg_db.views.auth", "public_home"),
    "ExaminationManifestCache": (
        "endoreg_db.views.examination",
        "ExaminationManifestCache",
    ),
    "ExaminationViewSet": ("endoreg_db.views.examination", "ExaminationViewSet"),
    "get_classification_choices_for_examination": (
        "endoreg_db.views.examination",
        "get_classification_choices_for_examination",
    ),
    "get_classifications_for_examination": (
        "endoreg_db.views.examination",
        "get_classifications_for_examination",
    ),
    "get_findings_for_examination": (
        "endoreg_db.views.examination",
        "get_findings_for_examination",
    ),
    "get_instruments_for_examination": (
        "endoreg_db.views.examination",
        "get_instruments_for_examination",
    ),
    "get_interventions_for_examination": (
        "endoreg_db.views.examination",
        "get_interventions_for_examination",
    ),
    "get_location_classification_choices_for_examination": (
        "endoreg_db.views.examination",
        "get_location_classification_choices_for_examination",
    ),
    "get_location_classifications_for_examination": (
        "endoreg_db.views.examination",
        "get_location_classifications_for_examination",
    ),
    "get_morphology_classification_choices_for_examination": (
        "endoreg_db.views.examination",
        "get_morphology_classification_choices_for_examination",
    ),
    "get_morphology_classifications_for_examination": (
        "endoreg_db.views.examination",
        "get_morphology_classifications_for_examination",
    ),
    "FindingViewSet": ("endoreg_db.views.finding", "FindingViewSet"),
    "get_classifications_for_finding": (
        "endoreg_db.views.finding",
        "get_classifications_for_finding",
    ),
    "get_interventions_for_finding": (
        "endoreg_db.views.finding",
        "get_interventions_for_finding",
    ),
    "FindingClassificationViewSet": (
        "endoreg_db.views.finding_classification",
        "FindingClassificationViewSet",
    ),
    "get_classification_choices": (
        "endoreg_db.views.finding_classification",
        "get_classification_choices",
    ),
    "get_location_choices": (
        "endoreg_db.views.finding_classification",
        "get_location_choices",
    ),
    "get_morphology_choices": (
        "endoreg_db.views.finding_classification",
        "get_morphology_choices",
    ),
    "get_sensitive_metadata_pk": (
        "endoreg_db.views.media",
        "get_sensitive_metadata_pk",
    ),
    "label_list": ("endoreg_db.views.media", "label_list"),
    "pdf_sensitive_metadata": ("endoreg_db.views.media", "pdf_sensitive_metadata"),
    "pdf_sensitive_metadata_list": (
        "endoreg_db.views.media",
        "pdf_sensitive_metadata_list",
    ),
    "pdf_sensitive_metadata_verify": (
        "endoreg_db.views.media",
        "pdf_sensitive_metadata_verify",
    ),
    "sensitive_metadata_list": ("endoreg_db.views.media", "sensitive_metadata_list"),
    "video_sensitive_metadata": ("endoreg_db.views.media", "video_sensitive_metadata"),
    "video_sensitive_metadata_verify": (
        "endoreg_db.views.media",
        "video_sensitive_metadata_verify",
    ),
    "SensitiveMetaListView": ("endoreg_db.views.meta", "SensitiveMetaListView"),
    "SensitiveMetaVerificationView": (
        "endoreg_db.views.meta",
        "SensitiveMetaVerificationView",
    ),
    "CenterViewSet": ("endoreg_db.views.misc", "CenterViewSet"),
    "application_settings_detail": (
        "endoreg_db.views.misc",
        "application_settings_detail",
    ),
    "application_settings_centers_dropdown": (
        "endoreg_db.views.misc",
        "application_settings_centers_dropdown",
    ),
    "application_settings_processors_dropdown": (
        "endoreg_db.views.misc",
        "application_settings_processors_dropdown",
    ),
    "application_settings_annotators_dropdown": (
        "endoreg_db.views.misc",
        "application_settings_annotators_dropdown",
    ),
    "application_settings_report_templates_dropdown": (
        "endoreg_db.views.misc",
        "application_settings_report_templates_dropdown",
    ),
    "ExaminationStatsView": ("endoreg_db.views.misc", "ExaminationStatsView"),
    "GenderViewSet": ("endoreg_db.views.misc", "GenderViewSet"),
    "GeneralStatsView": ("endoreg_db.views.misc", "GeneralStatsView"),
    "SensitiveMetaStatsView": ("endoreg_db.views.misc", "SensitiveMetaStatsView"),
    "UploadFileView": ("endoreg_db.views.misc", "UploadFileView"),
    "UploadStatusView": ("endoreg_db.views.misc", "UploadStatusView"),
    "VideoSegmentStatsView": ("endoreg_db.views.misc", "VideoSegmentStatsView"),
    "csrf_token_view": ("endoreg_db.views.misc", "csrf_token_view"),
    "PatientViewSet": ("endoreg_db.views.patient", "PatientViewSet"),
    "ExaminationCreateView": (
        "endoreg_db.views.patient_examination",
        "ExaminationCreateView",
    ),
    "PatientExaminationDetailView": (
        "endoreg_db.views.patient_examination",
        "PatientExaminationDetailView",
    ),
    "PatientExaminationListView": (
        "endoreg_db.views.patient_examination",
        "PatientExaminationListView",
    ),
    "PatientExaminationViewSet": (
        "endoreg_db.views.patient_examination",
        "PatientExaminationViewSet",
    ),
    "OptimizedPatientFindingViewSet": (
        "endoreg_db.views.patient_finding",
        "OptimizedPatientFindingViewSet",
    ),
    "PatientFindingViewSet": (
        "endoreg_db.views.patient_finding",
        "PatientFindingViewSet",
    ),
    "create_patient_finding_classification": (
        "endoreg_db.views.patient_finding_classification",
        "create_patient_finding_classification",
    ),
    "PatientExaminationReportViewSet": (
        "endoreg_db.views.report",
        "PatientExaminationReportViewSet",
    ),
    "ReportReimportView": ("endoreg_db.views.report", "ReportReimportView"),
    "ReportStreamView": ("endoreg_db.views.report", "ReportStreamView"),
    "evaluate_requirements": ("endoreg_db.views.requirement", "evaluate_requirements"),
    "LookupViewSet": ("endoreg_db.views.requirement", "LookupViewSet"),
    "VideoApplyMaskView": ("endoreg_db.views.video", "VideoApplyMaskView"),
    "VideoCorrectionView": ("endoreg_db.views.video", "VideoCorrectionView"),
    "VideoExaminationViewSet": ("endoreg_db.views.video", "VideoExaminationViewSet"),
    "VideoReimportView": ("endoreg_db.views.video", "VideoReimportView"),
    "VideoRemoveFramesView": ("endoreg_db.views.video", "VideoRemoveFramesView"),
    "VideoStreamView": ("endoreg_db.views.video", "VideoStreamView"),
}

__all__ = [
    "anonymization_status",
    "anonymization_current",
    "start_anonymization",
    "AnonymizationOverviewView",
    "AnonymizationValidateView",
    "KeycloakVideoView",
    "keycloak_login",
    "keycloak_callback",
    "public_home",
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
    "FindingViewSet",
    "get_interventions_for_finding",
    "get_classifications_for_finding",
    "FindingClassificationViewSet",
    "get_classification_choices",
    "get_morphology_choices",
    "get_location_choices",
    "SensitiveMetaListView",
    "SensitiveMetaVerificationView",
    "CenterViewSet",
    "application_settings_detail",
    "application_settings_centers_dropdown",
    "application_settings_processors_dropdown",
    "application_settings_annotators_dropdown",
    "application_settings_report_templates_dropdown",
    "csrf_token_view",
    "GenderViewSet",
    "ExaminationStatsView",
    "VideoSegmentStatsView",
    "SensitiveMetaStatsView",
    "GeneralStatsView",
    "build_multilingual_response",
    "UploadFileView",
    "UploadStatusView",
    "PatientViewSet",
    "ExaminationCreateView",
    "PatientExaminationDetailView",
    "PatientExaminationListView",
    "PatientExaminationViewSet",
    "PatientFindingViewSet",
    "OptimizedPatientFindingViewSet",
    "create_patient_finding_classification",
    "ReportReimportView",
    "ReportStreamView",
    "PatientExaminationReportViewSet",
    "evaluate_requirements",
    "LookupViewSet",
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

_OPTIONAL_IMPORT_NAMES = {"evaluate_requirements", "LookupViewSet"}


def __getattr__(name: str):
    target = _IMPORT_TARGETS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    try:
        module = import_module(module_name)
        value = getattr(module, attr_name)
    except Exception as exc:
        if name in _OPTIONAL_IMPORT_NAMES:
            logger.warning(
                "Optional view import failed for %s from %s: %s",
                name,
                module_name,
                exc,
                exc_info=True,
            )
        raise

    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
