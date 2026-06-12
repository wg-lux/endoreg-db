from pathlib import Path
from datetime import date, datetime, time
from typing import Iterable, Protocol, TypedDict, cast
from uuid import UUID

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
    patient_gender: object
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


def resolve_lx_anonymization_state(video: VideoFile) -> LxAnonymizationState:
    state = video.state
    if state is None:
        return LxAnonymizationState.NOT_STARTED

    status = state.anonymization_status
    status_value = getattr(status, "value", str(status))
    try:
        return LxAnonymizationState(status_value)
    except ValueError:
        return LxAnonymizationState.NOT_STARTED


def resolve_segment_labelset_name(segment: LabelVideoSegment) -> str | None:
    return segment.resolve_labelset_name()


def build_lx_sensitive_meta(
    sensitive_meta: SensitiveMeta | None,
) -> LxSensitiveMeta | None:
    if sensitive_meta is None:
        return None

    sensitive_meta_record = cast(_SensitiveMetaLxRecord, sensitive_meta)
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

    return LxSensitiveMeta.model_validate(
        {
            "examination_date": sensitive_meta_record.examination_date,
            "examination_time": sensitive_meta_record.examination_time,
            "casenumber": sensitive_meta_record.casenumber,
            "pseudo_patient": pseudo_patient,
            "pseudo_examination": pseudo_examination,
            "gender": gender_name,
            "pseudo_examiners": pseudo_examiners,
            "first_name": sensitive_meta_record.patient_first_name or "unknown",
            "last_name": sensitive_meta_record.patient_last_name or "unknown",
            "dob": (
                sensitive_meta_record.patient_dob.date()
                if isinstance(sensitive_meta_record.patient_dob, datetime)
                else sensitive_meta_record.patient_dob
                if sensitive_meta_record.patient_dob is not None
                else None
            ),
            "endoscope_type": sensitive_meta_record.endoscope_type,
            "endoscope_sn": sensitive_meta_record.endoscope_sn,
            "text": sensitive_meta_record.text,
            "anonymized_text": sensitive_meta_record.anonymized_text,
            "external_id": external_id,
        }
    )


def build_lx_p_video_segment(
    segment: LabelVideoSegment,
    *,
    default_labelset_name: str | None = None,
) -> PVideoSegment:
    label = segment.label
    if label is None:
        raise ValueError(f"Segment {segment.pk} has no label and cannot be exported.")

    labelset_name = resolve_segment_labelset_name(segment) or default_labelset_name
    if labelset_name is None:
        raise ValueError(
            f"Segment {segment.pk} has no resolvable labelset and cannot be exported."
        )

    return PVideoSegment.model_validate(
        {
            "start_frame_number": segment.start_frame_number,
            "end_frame_number": segment.end_frame_number,
            "patient_video_file": str(segment.video_file.uuid),
            "label": label.name,
            "labelset": labelset_name,
            "export_segment": segment.export_segment,
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
    strict_segments: bool = False,
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
            "prediction_meta__model_meta__labelset",
            "video_file__ai_model_meta__labelset",
        ):
            try:
                lx_segment = build_lx_p_video_segment(
                    segment,
                    default_labelset_name=default_labelset_name,
                )
            except ValueError:
                if strict_segments:
                    raise
                continue
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
