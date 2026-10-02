"""Business service layer for endoreg_db.

The package root stays intentionally light. Import concrete behavior from the
domain module that owns it, or use the selected compatibility exports below.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import spec_from_loader
from types import ModuleType
from typing import TYPE_CHECKING, Any

_LEGACY_MODULES: dict[str, str] = {
    "aidataset_active_learning": ".datasets.active_learning",
    "aidataset_active_learning_shortlist": ".datasets.active_learning_shortlist",
    "aidataset_exports": ".datasets.exports",
    "aidataset_frame_buckets": ".datasets.frame_buckets",
    "aidataset_training_manifests": ".datasets.training_manifests",
    "aidataset_training_selection": ".datasets.training_selection",
    "annotation_access": ".annotations.annotation_access",
    "anonymization": ".privacy.workflow",
    "anonymization_metrics": ".privacy.metrics",
    "anonymization_quality_evaluation": ".privacy.quality_evaluation",
    "audit_integrity": ".audit.integrity",
    "auto_case_resolution": ".cases.auto_resolution",
    "case_documents": ".cases.documents",
    "case_resolution_state": ".cases.resolution_state",
    "cases": ".cases",
    "center_access": ".centers.access",
    "center_defaults": ".centers.defaults",
    "center_employees": ".centers.employees",
    "clinical_reference_data": ".reference_data.clinical_projection",
    "deployment_setup": ".runtime.deployment_setup",
    "dtypes_records": ".reports.structured_records",
    "environment_readiness": ".runtime.environment_readiness",
    "evaluation_manifest": ".privacy.evaluation_manifest",
    "export_annotated": ".media.export_annotated",
    "export_ready": ".media.export_ready",
    "frame_annotation_buckets": ".annotations.buckets",
    "frame_annotation_sampling": ".annotations.sampling",
    "frame_annotation_segment_identity": ".annotations.segment_identity",
    "frame_annotation_workflow": ".annotations.workflow",
    "frame_retention": ".frames.frame_retention",
    "frame_segment_reconciliation": ".frames.frame_segment_reconciliation",
    "hls_legacy_adoption": ".streaming.hls_legacy_adoption",
    "hls_media": ".streaming.hls_media",
    "import_execution": ".imports.execution",
    "import_lease": ".imports.lease",
    "k_pseudonymity": ".privacy.k_pseudonymity",
    "k_pseudonymity_predicate": ".privacy.k_pseudonymity_predicate",
    "label_video_segment_states": ".annotations.label_video_segment_states",
    "lifecycle_state_machine": ".runtime.lifecycle_state_machine",
    "lx_video_contracts": ".video_files.clinical_contracts",
    "media_integrity": ".media.integrity",
    "media_operation_gate": ".media.operation_gate",
    "medical_ledger": ".interoperability.medical_ledger",
    "model_meta_from_hf": ".ai.model_meta_from_hf",
    "offline_batch_runner": ".runtime.offline_batch_runner",
    "patient_examination_links": ".cases.examination_links",
    "polling_coordinator": ".runtime.polling_coordinator",
    "processed_video_cleanup": ".video_storage.generation_cleanup",
    "pseudonym_service": ".privacy.patient_pseudonyms",
    "reconciliation": ".runtime.reconciliation",
    "reference_catalog": ".reference_data.catalog",
    "reference_catalog_export": ".reference_data.catalog_export",
    "reference_catalog_migration": ".reference_data.catalog_migration",
    "reference_catalog_models": ".reference_data.catalog_models",
    "report_finding_sync": ".reports.finding_sync",
    "report_frame_export": ".reports.frame_export",
    "report_frame_selection": ".reports.frame_selection",
    "report_history": ".reports.history",
    "report_import": ".reports.import_service",
    "report_import_fencing": ".reports.import_fencing",
    "report_import_lifecycle": ".reports.import_lifecycle",
    "report_import_state_machine": ".reports.import_state_machine",
    "report_materialization": ".reports.materialization",
    "report_patient_context": ".reports.patient_context",
    "report_pdf_renderer": ".reports.pdf_renderer",
    "report_persistence": ".reports.persistence",
    "report_runtime_validation": ".reports.runtime_validation",
    "runtime_wheel_staging": ".runtime.runtime_wheel_staging",
    "sap_ish_clinical": ".interoperability.sap_ish_clinical",
    "sap_ish_import": ".interoperability.sap_ish_import",
    "seekable_media_input": ".streaming.seekable_media_input",
    "segment_annotations": ".annotations.segment_annotations",
    "segment_frame_annotations": ".annotations.segment_frame_annotations",
    "segment_sync": ".annotations.segment_sync",
    "sensitive_meta_external_ids": ".patient_identity.external_ids",
    "sensitive_meta_update": ".patient_identity.update",
    "sensitive_meta_verification": ".patient_identity.verification",
    "streamable_media": ".streaming.streamable_media",
    "streamable_media_state": ".streaming.streamable_media_state",
    "streamable_media_transcoding": ".streaming.streamable_media_transcoding",
    "streamable_media_types": ".streaming.streamable_media_types",
    "study_cohort": ".studies.cohort",
    "study_presets": ".studies.presets",
    "tabular_import_formats": ".imports.tabular_import_formats",
    "validated_identity": ".patient_identity.validated_media_identity",
    "video_dimension_backfill": ".video_storage.dimension_backfill",
    "video_format_reconciliation": ".video_storage.format_reconciliation",
    "video_import": ".video_files.direct_import",
    "video_post_validation_blackening": ".video_files.post_validation_blackening",
    "video_processed_transcode": ".video_storage.processed_transcode",
    "video_reimport_orchestrator": ".video_files.reimport_orchestrator",
    "video_segment_blackening": ".video_files.segment_blackening",
    "video_segment_validation_workflow": ".annotations.segment_validation_workflow",
    "video_segments_bulk_mutation": ".annotations.segments_bulk_mutation",
    "video_source_hash": ".video_files.source_hash",
    "video_storage_normalization": ".video_storage.workflow",
    "video_temporal_inference": ".video_files.temporal_inference",
    "video_timeline": ".video_files.timeline",
    "video_transcoding": ".video_storage.transcoding",
}

_EXPORTS = {
    "build_preanonymized_payload": (
        ".imports.tabular_import_formats",
        "build_preanonymized_payload",
    ),
    "convert_sap_ish_zip_to_preanonymized_drop": (
        ".interoperability.sap_ish_import",
        "convert_sap_ish_zip_to_preanonymized_drop",
    ),
    "convert_sap_ish_txt_directory_to_preanonymized_drop": (
        ".interoperability.sap_ish_import",
        "convert_sap_ish_txt_directory_to_preanonymized_drop",
    ),
    "persist_sap_ish_clinical_rows": (
        ".interoperability.sap_ish_clinical",
        "persist_sap_ish_clinical_rows",
    ),
    "load_document_templates": (
        ".imports.tabular_import_formats",
        "load_document_templates",
    ),
    "normalize_document_row": (
        ".imports.tabular_import_formats",
        "normalize_document_row",
    ),
    "resolve_document_template": (
        ".imports.tabular_import_formats",
        "resolve_document_template",
    ),
}

__all__ = [
    "build_preanonymized_payload",
    "convert_sap_ish_txt_directory_to_preanonymized_drop",
    "convert_sap_ish_zip_to_preanonymized_drop",
    "load_document_templates",
    "normalize_document_row",
    "persist_sap_ish_clinical_rows",
    "resolve_document_template",
]


class _LegacyServiceLoader(Loader):
    """Publish the canonical module under its former name without copying it."""

    _canonical_name: str
    _canonical_spec: ModuleSpec | None

    def __init__(self, canonical_name: str) -> None:
        self._canonical_name = canonical_name
        self._canonical_spec = None

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        module = import_module(self._canonical_name)
        self._canonical_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Import machinery assigns the alias spec to the existing module.
        # Keep canonical metadata so reload and introspection still work.
        module.__spec__ = self._canonical_spec


class _LegacyServiceFinder(MetaPathFinder):
    """Resolve only former direct children of this service package."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        parent, _, name = fullname.rpartition(".")
        if parent != __name__ or name == "cases":
            return None
        canonical_path = _LEGACY_MODULES.get(name)
        if canonical_path is None:
            return None
        return spec_from_loader(
            fullname, _LegacyServiceLoader(f"{__name__}{canonical_path}")
        )


# Register once even when this lightweight package is reloaded.
if not any(
    type(finder).__module__ == __name__
    and type(finder).__name__ == "_LegacyServiceFinder"
    for finder in sys.meta_path
):
    sys.meta_path.insert(0, _LegacyServiceFinder())


if TYPE_CHECKING:
    aidataset_active_learning: ModuleType
    aidataset_active_learning_shortlist: ModuleType
    aidataset_exports: ModuleType
    aidataset_frame_buckets: ModuleType
    aidataset_training_manifests: ModuleType
    aidataset_training_selection: ModuleType
    annotation_access: ModuleType
    anonymization: ModuleType
    anonymization_metrics: ModuleType
    anonymization_quality_evaluation: ModuleType
    audit_integrity: ModuleType
    auto_case_resolution: ModuleType
    case_documents: ModuleType
    case_resolution_state: ModuleType
    cases: ModuleType
    center_access: ModuleType
    center_defaults: ModuleType
    center_employees: ModuleType
    clinical_reference_data: ModuleType
    deployment_setup: ModuleType
    dtypes_records: ModuleType
    environment_readiness: ModuleType
    evaluation_manifest: ModuleType
    export_annotated: ModuleType
    export_ready: ModuleType
    frame_annotation_buckets: ModuleType
    frame_annotation_sampling: ModuleType
    frame_annotation_segment_identity: ModuleType
    frame_annotation_workflow: ModuleType
    frame_retention: ModuleType
    frame_segment_reconciliation: ModuleType
    hls_legacy_adoption: ModuleType
    hls_media: ModuleType
    import_execution: ModuleType
    import_lease: ModuleType
    k_pseudonymity: ModuleType
    k_pseudonymity_predicate: ModuleType
    label_video_segment_states: ModuleType
    lifecycle_state_machine: ModuleType
    lx_video_contracts: ModuleType
    media_integrity: ModuleType
    media_operation_gate: ModuleType
    medical_ledger: ModuleType
    model_meta_from_hf: ModuleType
    offline_batch_runner: ModuleType
    patient_examination_links: ModuleType
    polling_coordinator: ModuleType
    processed_video_cleanup: ModuleType
    pseudonym_service: ModuleType
    reconciliation: ModuleType
    reference_catalog: ModuleType
    reference_catalog_export: ModuleType
    reference_catalog_migration: ModuleType
    reference_catalog_models: ModuleType
    report_finding_sync: ModuleType
    report_frame_export: ModuleType
    report_frame_selection: ModuleType
    report_history: ModuleType
    report_import: ModuleType
    report_import_fencing: ModuleType
    report_import_lifecycle: ModuleType
    report_import_state_machine: ModuleType
    report_materialization: ModuleType
    report_patient_context: ModuleType
    report_pdf_renderer: ModuleType
    report_persistence: ModuleType
    report_runtime_validation: ModuleType
    runtime_wheel_staging: ModuleType
    sap_ish_clinical: ModuleType
    sap_ish_import: ModuleType
    seekable_media_input: ModuleType
    segment_annotations: ModuleType
    segment_frame_annotations: ModuleType
    segment_sync: ModuleType
    sensitive_meta_external_ids: ModuleType
    sensitive_meta_update: ModuleType
    sensitive_meta_verification: ModuleType
    streamable_media: ModuleType
    streamable_media_state: ModuleType
    streamable_media_transcoding: ModuleType
    streamable_media_types: ModuleType
    study_cohort: ModuleType
    study_presets: ModuleType
    tabular_import_formats: ModuleType
    validated_identity: ModuleType
    video_dimension_backfill: ModuleType
    video_format_reconciliation: ModuleType
    video_import: ModuleType
    video_post_validation_blackening: ModuleType
    video_processed_transcode: ModuleType
    video_reimport_orchestrator: ModuleType
    video_segment_blackening: ModuleType
    video_segment_validation_workflow: ModuleType
    video_segments_bulk_mutation: ModuleType
    video_source_hash: ModuleType
    video_storage_normalization: ModuleType
    video_temporal_inference: ModuleType
    video_timeline: ModuleType
    video_transcoding: ModuleType

    from endoreg_db.services.interoperability.sap_ish_import import (
        convert_sap_ish_txt_directory_to_preanonymized_drop,
        convert_sap_ish_zip_to_preanonymized_drop,
    )
    from endoreg_db.services.interoperability.sap_ish_clinical import (
        persist_sap_ish_clinical_rows,
    )
    from endoreg_db.services.imports.tabular_import_formats import (
        build_preanonymized_payload,
        load_document_templates,
        normalize_document_row,
        resolve_document_template,
    )


def __getattr__(name: str) -> Any:
    if name in _LEGACY_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module

    export_path = _EXPORTS.get(name)
    if export_path is not None:
        module_name, attribute_name = export_path
        module = import_module(module_name, __name__)
        value = getattr(module, attribute_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LEGACY_MODULES))
