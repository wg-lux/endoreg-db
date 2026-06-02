"""Business service layer for endoreg_db.

The package root stays intentionally light. Import concrete behavior from the
domain module that owns it, or use the selected compatibility exports below.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any

_DATASET_MODULES = (
    "aidataset_exports",
    "aidataset_frame_buckets",
)
_ANONYMIZATION_MODULES = (
    "anonymization",
    "anonymization_metrics",
    "anonymization_quality_evaluation",
    "pseudonym_service",
    "validated_identity",
)
_MEDIA_MODULES = (
    "export_ready",
    "frame_retention",
    "frame_segment_reconciliation",
    "lx_video_contracts",
    "media_integrity",
    "media_operation_gate",
    "pdf_import",
    "raw_pdf_files",
    "segment_annotations",
    "segment_contracts",
    "segment_sync",
    "streamable_media",
    "video_dimension_backfill",
    "video_format_reconciliation",
    "video_files",
    "video_import",
    "video_post_validation_blackening",
    "video_segments_bulk_mutation",
    "video_temporal_inference",
    "video_transcoding",
)
_REPORT_MODULES = (
    "report_history",
    "report_import",
    "report_materialization",
    "report_pdf_renderer",
    "report_persistence",
)
_SYSTEM_MODULES = (
    "application_settings",
    "audit_integrity",
    "auto_case_resolution",
    "case_resolution_state",
    "environment_readiness",
    "finding_description_service",
    "frames",
    "hub",
    "jobs",
    "knowledge_base_identity",
    "model_meta_from_hf",
    "polling_coordinator",
    "reconciliation",
    "sap_ish_import",
    "tabular_import_formats",
)
_SERVICE_MODULES = {
    module_name: f".{module_name}"
    for module_name in (
        *_DATASET_MODULES,
        *_ANONYMIZATION_MODULES,
        *_MEDIA_MODULES,
        *_REPORT_MODULES,
        *_SYSTEM_MODULES,
    )
}

_EXPORTS = {
    "build_preanonymized_payload": (
        ".tabular_import_formats",
        "build_preanonymized_payload",
    ),
    "convert_sap_ish_zip_to_preanonymized_drop": (
        ".sap_ish_import",
        "convert_sap_ish_zip_to_preanonymized_drop",
    ),
    "load_document_templates": (
        ".tabular_import_formats",
        "load_document_templates",
    ),
    "normalize_document_row": (
        ".tabular_import_formats",
        "normalize_document_row",
    ),
    "resolve_document_template": (
        ".tabular_import_formats",
        "resolve_document_template",
    ),
}

__all__ = [
    "build_preanonymized_payload",
    "convert_sap_ish_zip_to_preanonymized_drop",
    "load_document_templates",
    "normalize_document_row",
    "resolve_document_template",
]

if TYPE_CHECKING:
    aidataset_exports: ModuleType
    aidataset_frame_buckets: ModuleType
    anonymization: ModuleType
    anonymization_metrics: ModuleType
    anonymization_quality_evaluation: ModuleType
    application_settings: ModuleType
    audit_integrity: ModuleType
    auto_case_resolution: ModuleType
    case_resolution_state: ModuleType
    environment_readiness: ModuleType
    export_ready: ModuleType
    finding_description_service: ModuleType
    frames: ModuleType
    frame_retention: ModuleType
    frame_segment_reconciliation: ModuleType
    hub: ModuleType
    jobs: ModuleType
    knowledge_base_identity: ModuleType
    lx_video_contracts: ModuleType
    media_integrity: ModuleType
    media_operation_gate: ModuleType
    model_meta_from_hf: ModuleType
    pdf_import: ModuleType
    polling_coordinator: ModuleType
    pseudonym_service: ModuleType
    raw_pdf_files: ModuleType
    reconciliation: ModuleType
    report_history: ModuleType
    report_import: ModuleType
    report_materialization: ModuleType
    report_pdf_renderer: ModuleType
    report_persistence: ModuleType
    sap_ish_import: ModuleType
    segment_annotations: ModuleType
    segment_contracts: ModuleType
    segment_sync: ModuleType
    streamable_media: ModuleType
    tabular_import_formats: ModuleType
    validated_identity: ModuleType
    video_dimension_backfill: ModuleType
    video_format_reconciliation: ModuleType
    video_files: ModuleType
    video_import: ModuleType
    video_post_validation_blackening: ModuleType
    video_segments_bulk_mutation: ModuleType
    video_temporal_inference: ModuleType
    video_transcoding: ModuleType

    from .sap_ish_import import convert_sap_ish_zip_to_preanonymized_drop
    from .tabular_import_formats import (
        build_preanonymized_payload,
        load_document_templates,
        normalize_document_row,
        resolve_document_template,
    )


def __getattr__(name: str) -> Any:
    module_path = _SERVICE_MODULES.get(name)
    if module_path is not None:
        module = import_module(module_path, __name__)
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
    return sorted(set(globals()) | set(__all__) | set(_SERVICE_MODULES))
