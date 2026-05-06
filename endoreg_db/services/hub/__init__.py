from __future__ import annotations

from importlib import import_module

__all__ = [
    "_default_processor_name",
    "apply_transfer_metadata",
    "assert_environment_readiness",
    "attach_transfer_media",
    "authenticate_network_node",
    "check_environment_readiness",
    "create_or_reuse_transfer_job",
    "create_or_reuse_upload_job",
    "create_or_reuse_watcher_upload_job",
    "deployment_profile_payload",
    "get_deployment_role",
    "hub_mode_enabled",
    "local_study_server_mode_enabled",
    "MediaIntegrityError",
    "MediaIntegrityExpectation",
    "MediaIntegrityResult",
    "MediaIntegrityStatus",
    "PreanonymizedIngestPayload",
    "check_upload_job_media_integrity",
    "process_preanonymized_watcher_file",
    "process_upload_job",
    "process_watcher_file",
    "reap_upload_job_sources",
    "resolve_allowed_center_id",
    "resolve_api_upload_context",
    "resolve_declared_upload_center",
    "resolve_default_center",
    "resolve_upload_center",
    "start_upload_job_processing",
    "transfer_api_enabled",
]

_EXPORTS = {
    "PreanonymizedIngestPayload": (".payloads", "PreanonymizedIngestPayload"),
    "deployment_profile_payload": (".deployment", "deployment_profile_payload"),
    "get_deployment_role": (".deployment", "get_deployment_role"),
    "transfer_api_enabled": (".deployment", "transfer_api_enabled"),
    "local_study_server_mode_enabled": (
        ".deployment",
        "local_study_server_mode_enabled",
    ),
    "reap_upload_job_sources": (".cleanup", "reap_upload_job_sources"),
    "assert_environment_readiness": (
        "..environment_readiness",
        "assert_environment_readiness",
    ),
    "check_environment_readiness": (
        "..environment_readiness",
        "check_environment_readiness",
    ),
    "_default_processor_name": (".ingest", "_default_processor_name"),
    "create_or_reuse_upload_job": (".ingest", "create_or_reuse_upload_job"),
    "create_or_reuse_watcher_upload_job": (
        ".ingest",
        "create_or_reuse_watcher_upload_job",
    ),
    "process_preanonymized_watcher_file": (
        ".ingest",
        "process_preanonymized_watcher_file",
    ),
    "process_watcher_file": (".ingest", "process_watcher_file"),
    "process_upload_job": (".ingest", "process_upload_job"),
    "resolve_api_upload_context": (".ingest", "resolve_api_upload_context"),
    "start_upload_job_processing": (".ingest", "start_upload_job_processing"),
    "hub_mode_enabled": (".ingest", "hub_mode_enabled"),
    "resolve_allowed_center_id": (".ingest", "resolve_allowed_center_id"),
    "resolve_declared_upload_center": (
        ".ingest",
        "resolve_declared_upload_center",
    ),
    "resolve_default_center": (".ingest", "resolve_default_center"),
    "resolve_upload_center": (".ingest", "resolve_upload_center"),
    "apply_transfer_metadata": (".transfers", "apply_transfer_metadata"),
    "attach_transfer_media": (".transfers", "attach_transfer_media"),
    "authenticate_network_node": (".transfers", "authenticate_network_node"),
    "create_or_reuse_transfer_job": (".transfers", "create_or_reuse_transfer_job"),
    "MediaIntegrityError": (".media_integrity", "MediaIntegrityError"),
    "MediaIntegrityExpectation": (".media_integrity", "MediaIntegrityExpectation"),
    "MediaIntegrityResult": (".media_integrity", "MediaIntegrityResult"),
    "MediaIntegrityStatus": (".media_integrity", "MediaIntegrityStatus"),
    "check_upload_job_media_integrity": (
        ".media_integrity",
        "check_upload_job_media_integrity",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value
