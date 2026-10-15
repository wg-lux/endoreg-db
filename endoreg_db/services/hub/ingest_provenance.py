"""Upload provenance construction and updates; orchestration lives in ingest.py.

Historical imports from ingest.py re-export these same objects. See structure.yml
for module ownership and regression tests. Updates mutate the supplied job;
only record_active_learning_selection_provenance(save=True) persists it.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict, cast

from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob


class UploadProvenance(TypedDict, total=False):
    entrypoint: str
    ingest_mode: str
    source_system: str
    content_hash: str
    source_center_key: str | None
    storage_class: str
    storage_tier: str
    retention_policy: str
    hub_mode: bool
    declared_center_key: str | None
    declared_center_name: str | None
    resolved_center_key: str | None
    watched_path: str
    file_type: str
    ingest_variant: str
    sidecar_path: str
    sidecar_payload: JsonObject
    watcher_processing_path: str
    processor_name: str | None
    processing_handoff: str
    llm_job_id: str
    llm_task_id: str
    llm_queue: str
    prediction_model_name: str | None
    prediction_task_id: str
    prediction_history_id: int | None
    prediction_queue: str
    video_import_task_id: str
    video_import_queue: str
    video_import_fencing_epoch: int
    stored_upload_path: str
    quarantined_path: str
    quarantined_sidecar_path: str
    media_integrity_status: str
    media_integrity_reason: str
    media_integrity_missing_artifacts: list[str]
    previous_upload_job_id: str
    custom_marker: NotRequired[str]


def upload_provenance(
    existing: JsonObject | UploadProvenance | None = None,
) -> UploadProvenance:
    provenance: dict[str, JsonValue] = {}
    if existing:
        provenance.update(cast(JsonObject, existing))
    return cast(UploadProvenance, provenance)


def update_upload_provenance(
    upload_job: UploadJob,
    **updates: JsonValue | None,
) -> UploadProvenance:
    provenance: dict[str, JsonValue] = dict(
        cast(JsonObject, upload_provenance(upload_job.processing_provenance))
    )
    for key, value in updates.items():
        if value is not None:
            provenance[key] = value
    upload_job.processing_provenance = provenance
    return cast(UploadProvenance, provenance)


def record_active_learning_selection_provenance(
    upload_job: UploadJob,
    *,
    selection_strategy: str = "temporal_segment_hybrid",
    candidate_count: int,
    selected_count: int,
    annotation_budget: int,
    ai_dataset_id: int | None = None,
    model_meta_id: int | None = None,
    extra_metadata: JsonObject | None = None,
    save: bool = True,
) -> UploadProvenance:
    active_learning_payload: JsonObject = {
        "selection_strategy": selection_strategy,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "annotation_budget": annotation_budget,
    }
    if ai_dataset_id is not None:
        active_learning_payload["ai_dataset_id"] = ai_dataset_id
    if model_meta_id is not None:
        active_learning_payload["model_meta_id"] = model_meta_id
    if extra_metadata:
        active_learning_payload.update(extra_metadata)

    existing_sidecar_payload: JsonObject = {}
    current_sidecar_payload = upload_job.processing_provenance.get("sidecar_payload")
    if isinstance(current_sidecar_payload, dict):
        existing_sidecar_payload.update(cast(JsonObject, current_sidecar_payload))
    existing_sidecar_payload["active_learning"] = active_learning_payload

    provenance = update_upload_provenance(
        upload_job,
        ingest_variant="active_learning_selection",
        sidecar_payload=existing_sidecar_payload,
        custom_marker="active_learning",
    )
    if save:
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
    return provenance


def normalized_upload_provenance(
    *,
    ingest_mode: str,
    source_system: str,
    content_hash: str,
    source_center: Center | None,
    storage_class: str,
    storage_tier: str,
    retention_policy: str,
    processing_provenance: UploadProvenance | None = None,
) -> UploadProvenance:
    provenance = upload_provenance(processing_provenance)
    provenance.setdefault("entrypoint", ingest_mode)
    provenance["ingest_mode"] = ingest_mode
    provenance["source_system"] = source_system
    provenance["content_hash"] = content_hash
    provenance["source_center_key"] = (
        source_center.center_key if source_center is not None else None
    )
    provenance["storage_class"] = storage_class
    provenance["storage_tier"] = storage_tier
    provenance["retention_policy"] = retention_policy
    return provenance


def get_upload_provenance(upload_job: UploadJob) -> UploadProvenance | None:
    return cast(UploadProvenance | None, upload_job.processing_provenance)
