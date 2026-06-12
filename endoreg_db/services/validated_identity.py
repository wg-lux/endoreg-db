from __future__ import annotations

from typing import Literal, Protocol, cast
import hashlib
import json

from django.db import models, transaction

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.services.auto_case_resolution import (
    AutoCaseResolutionResult,
    auto_resolve_media_case,
)
from lx_dtypes.models.contracts.validated_identity import (
    ValidatedIdentityPayload,
    dump_validated_identity_payload,
)
from lx_dtypes.models.contracts.json_types import JsonObject


MediaType = Literal["video", "pdf"]


class _SensitiveMetaIdentityContract(Protocol):
    pk: int
    patient_hash: str | None
    examination_hash: str | None
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


class _PatientExaminationIdentityContract(Protocol):
    pk: int
    patient_id: int | None


def _canonical_payload_hash(payload: JsonObject) -> str:
    return hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


@transaction.atomic
def commit_validated_media_identity(
    *,
    media_type: MediaType,
    media_obj: RawPdfFile | VideoFile,
    user: models.Model | None = None,
    source: str,
) -> AutoCaseResolutionResult:
    """
    Resolve and append-only commit the validated non-PII identity for a media item.

    This must run after SensitiveMeta was updated from validated OCR/manual input
    and before cleartext fields are anonymized. The AuditLedger entry contains
    only hashes and ids; it deliberately does not store personal data.
    """
    sensitive_meta = cast(
        _SensitiveMetaIdentityContract | None, media_obj.sensitive_meta
    )
    if sensitive_meta is None:
        return auto_resolve_media_case(media_type=media_type, media_obj=media_obj)

    resolution = auto_resolve_media_case(media_type=media_type, media_obj=media_obj)
    patient_examination = resolution.patient_examination

    commit_payload = ValidatedIdentityPayload(
        source=source,
        media_type=media_type,
        media_pk=str(media_obj.pk),
        sensitive_meta_id=sensitive_meta.pk,
        patient_hash=sensitive_meta.patient_hash,
        examination_hash=sensitive_meta.examination_hash,
        pseudo_patient_id=sensitive_meta.pseudo_patient_id,
        pseudo_examination_id=sensitive_meta.pseudo_examination_id,
        linked_patient_id=(
            cast(_PatientExaminationIdentityContract, patient_examination).patient_id
            if patient_examination is not None
            else None
        ),
        linked_patient_examination_id=(
            patient_examination.pk if patient_examination is not None else None
        ),
        case_resolution_status=resolution.status,
        case_resolution_reason=resolution.reason,
        case_resolution_created=resolution.created,
    )
    commit_payload_data = cast(JsonObject, dump_validated_identity_payload(commit_payload))
    commit_payload_data["payload_hash"] = _canonical_payload_hash(commit_payload_data)

    AuditLedger.append_identity_commit(
        user=user,
        object_type="SensitiveMeta",
        object_pk=str(sensitive_meta.pk),
        data=commit_payload_data,
    )
    return resolution
