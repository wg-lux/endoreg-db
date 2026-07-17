from pathlib import Path
from datetime import date, datetime, time
from typing import Iterable, Protocol, TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from django.core.exceptions import ObjectDoesNotExist
from lx_dtypes.models.base.file.ddict.FilesAndDirsDataDict import FilesAndDirsDataDict
from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile
from lx_dtypes.models.ledger.p_video.state import (
    AnonymizationState as LxAnonymizationState,
)
from lx_dtypes.models.ledger.p_video_segment.Pydantic import PVideoSegment
from lx_dtypes.models.meta.SensitiveMeta import SensitiveMeta as LxSensitiveMeta

from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

_KNOWN_GENDER_NAMES = {"female", "male", "other", "unknown"}


class _ModelPk(Protocol):
    pk: int | str | UUID


class _ExaminerRelationManager(Protocol):
    def all(self) -> Iterable[_ModelPk]: ...


class _SensitiveMetaLxRecord(Protocol):
    pk: int | None
    patient_gender: object
    center: object | None
    external_id: object | None
    pseudo_patient: _ModelPk | None
    pseudo_examination: _ModelPk | None
    examination_date: date | datetime | None
    examination_time: time | None
    casenumber: str | None
    patient_first_name: str | None
    patient_last_name: str | None
    patient_dob: date | datetime | None
    endoscope_type: str | None
    endoscope_sn: str | None
    text: str | None
    anonymized_text: str | None
    file_path: str | None
    examiner_first_name: str | None
    examiner_last_name: str | None


class _SensitiveMetaStateRecord(Protocol):
    dob_verified: bool
    names_verified: bool


class _SegmentStateRecord(Protocol):
    prediction: bool
    annotation: bool
    frames_extracted: bool
    is_validated: bool


class _PatientVideoFileInput(TypedDict, total=False):
    uuid: str
    patient: str | None
    patient_examination: str | None
    fnd: FilesAndDirsDataDict
    anonymization_state: LxAnonymizationState
    sensitive_meta: LxSensitiveMeta | None
    patient_video_segments: dict[str, PVideoSegment]
    external_ids: dict[str, str]


def _pk_as_str(instance: _ModelPk | None) -> str | None:
    if instance is None:
        return None
    return str(instance.pk)


def _persistent_contract_uuid(*, kind: str, primary_key: int | None) -> str:
    if primary_key is None:
        raise ValueError(
            f"Unsaved {kind} cannot be converted to an lx_dtypes contract."
        )
    return str(uuid5(NAMESPACE_URL, f"urn:endoreg-db:{kind}:{primary_key}"))


def _as_date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def resolve_lx_anonymization_state(video: VideoFile) -> LxAnonymizationState:
    state = video.state
    if state is None:
        return LxAnonymizationState.NOT_STARTED

    return LxAnonymizationState(state.anonymization_status.value)


def resolve_segment_labelset_name(segment: LabelVideoSegment) -> str | None:
    return segment.resolve_labelset_name()


def build_lx_sensitive_meta(
    sensitive_meta: SensitiveMeta | None,
) -> LxSensitiveMeta | None:
    if sensitive_meta is None:
        return None

    sensitive_meta_record = cast(_SensitiveMetaLxRecord, sensitive_meta)
    contract_uuid = _persistent_contract_uuid(
        kind="sensitive-meta",
        primary_key=sensitive_meta_record.pk,
    )
    try:
        state = cast(_SensitiveMetaStateRecord, sensitive_meta.state_safe)
    except ObjectDoesNotExist as exc:
        raise ValueError(
            f"SensitiveMeta {sensitive_meta_record.pk} has no persisted state."
        ) from exc

    dob = _as_date(sensitive_meta_record.patient_dob)
    examination_date = _as_date(sensitive_meta_record.examination_date)
    if dob is not None and examination_date is not None and examination_date < dob:
        raise ValueError(
            f"SensitiveMeta {sensitive_meta_record.pk} has examination_date before dob."
        )

    gender_name = (
        getattr(sensitive_meta_record.patient_gender, "name", None) or "unknown"
    )
    if gender_name not in _KNOWN_GENDER_NAMES:
        gender_name = "unknown"

    external_id = None
    if sensitive_meta_record.external_id is not None:
        external_id = getattr(sensitive_meta_record.external_id, "external_id", None)

    examiners = cast(_ExaminerRelationManager, getattr(sensitive_meta, "examiners"))
    pseudo_examiners = [str(examiner.pk) for examiner in examiners.all()]
    pseudo_patient = _pk_as_str(sensitive_meta_record.pseudo_patient)
    pseudo_examination = _pk_as_str(sensitive_meta_record.pseudo_examination)
    center_name = getattr(sensitive_meta_record.center, "name", None)

    return LxSensitiveMeta.model_validate(
        {
            "uuid": contract_uuid,
            "file_path": sensitive_meta_record.file_path,
            "examination_date": examination_date,
            "examination_time": sensitive_meta_record.examination_time,
            "casenumber": sensitive_meta_record.casenumber,
            "pseudo_patient": pseudo_patient,
            "pseudo_examination": pseudo_examination,
            "gender": gender_name,
            "pseudo_examiners": pseudo_examiners,
            "first_name": sensitive_meta_record.patient_first_name or "unknown",
            "last_name": sensitive_meta_record.patient_last_name or "unknown",
            "dob": dob,
            "endoscope_type": sensitive_meta_record.endoscope_type,
            "endoscope_sn": sensitive_meta_record.endoscope_sn,
            "examiner_first_name": sensitive_meta_record.examiner_first_name,
            "examiner_last_name": sensitive_meta_record.examiner_last_name,
            "center": center_name,
            "text": sensitive_meta_record.text,
            "anonymized_text": sensitive_meta_record.anonymized_text,
            "external_id": external_id,
            "sensitive_meta_state": {
                "sensitive_meta": contract_uuid,
                "dob_verified": state.dob_verified,
                "name_verified": state.names_verified,
                "examination_date_verified": False,
            },
        }
    )


def build_lx_p_video_segment(
    segment: LabelVideoSegment,
    *,
    default_labelset_name: str | None = None,
) -> PVideoSegment:
    contract_uuid = _persistent_contract_uuid(
        kind="label-video-segment",
        primary_key=segment.pk,
    )
    label = segment.label
    if label is None:
        raise ValueError(f"Segment {segment.pk} has no label and cannot be exported.")

    labelset_name = resolve_segment_labelset_name(segment) or default_labelset_name
    if labelset_name is None:
        raise ValueError(
            f"Segment {segment.pk} has no resolvable labelset and cannot be exported."
        )

    try:
        state = cast(_SegmentStateRecord, segment.state)
    except ObjectDoesNotExist as exc:
        raise ValueError(f"Segment {segment.pk} has no persisted state.") from exc

    has_prediction_meta = segment.prediction_meta is not None
    prediction = bool(state.prediction or has_prediction_meta)
    annotation = bool(state.annotation or not has_prediction_meta)

    return PVideoSegment.model_validate(
        {
            "uuid": contract_uuid,
            "start_frame_number": segment.start_frame_number,
            "end_frame_number": segment.end_frame_number,
            "patient_video_file": str(segment.video_file.uuid),
            "label": label.name,
            "labelset": labelset_name,
            "export_segment": segment.export_segment,
            "patient_video_segment_state": {
                "prediction": prediction,
                "annotation": annotation,
                "frames_extracted": state.frames_extracted,
                "is_validated": state.is_validated,
                "patient_video_segment": contract_uuid,
            },
            "external_ids": (
                {"endoreg_db_segment_id": str(segment.pk)}
                if segment.pk is not None
                else {}
            ),
        }
    )


def build_lx_patient_video_file(
    video: VideoFile,
    *,
    include_segments: bool = True,
    default_labelset_name: str | None = None,
) -> PatientVideoFile:
    try:
        processed_file = video.processed_file
    except Exception as exc:
        raise ValueError(f"Video {video.pk} has no processed file") from exc
    processed_name = getattr(processed_file, "name", None)
    if not processed_name:
        raise ValueError(f"Video {video.pk} has no processed file")
    processed_path = Path(processed_name)
    lx_sensitive_meta = build_lx_sensitive_meta(video.sensitive_meta)

    patient_video_segments: dict[str, PVideoSegment] = {}
    if include_segments:
        for segment in video.label_video_segments.select_related(
            "label",
            "state",
            "prediction_meta__model_meta__labelset",
            "video_file__ai_model_meta__labelset",
        ):
            lx_segment = build_lx_p_video_segment(
                segment,
                default_labelset_name=default_labelset_name,
            )
            patient_video_segments[str(lx_segment.uuid)] = lx_segment

    external_ids = {"video_hash": video.video_hash}
    if video.processed_video_hash:
        external_ids["processed_video_hash"] = video.processed_video_hash

    fnd: FilesAndDirsDataDict = {
        "file": str(processed_path),
        "dir": str(Path(processed_path).parent),
        "files": [],
        "dirs": [],
    }
    data: _PatientVideoFileInput = {
        "uuid": str(video.uuid),
        "patient": str(video.patient_id) if video.patient_id is not None else None,
        "patient_examination": (
            str(video.examination_id) if video.examination_id is not None else None
        ),
        "fnd": fnd,
        "anonymization_state": resolve_lx_anonymization_state(video),
        "sensitive_meta": lx_sensitive_meta,
        "patient_video_segments": patient_video_segments,
        "external_ids": external_ids,
    }

    return PatientVideoFile.model_validate(data)
