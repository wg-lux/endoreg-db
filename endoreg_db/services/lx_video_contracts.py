from pathlib import Path

from lx_dtypes.models.ledger.p_video.Pydantic import PatientVideoFile
from lx_dtypes.models.ledger.p_video.state import (
    AnonymizationState as LxAnonymizationState,
)
from lx_dtypes.models.ledger.p_video_segment.Pydantic import PVideoSegment
from lx_dtypes.models.meta.SensitiveMeta import SensitiveMeta as LxSensitiveMeta

from endoreg_db.models import LabelVideoSegment, SensitiveMeta, VideoFile

_KNOWN_GENDER_NAMES = {"female", "male", "other", "unknown"}


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

    gender_name = getattr(sensitive_meta.patient_gender, "name", None) or "unknown"
    if gender_name not in _KNOWN_GENDER_NAMES:
        gender_name = "unknown"

    external_id = None
    if sensitive_meta.external_id is not None:
        external_id = sensitive_meta.external_id.external_id

    pseudo_examiners = [str(examiner.pk) for examiner in sensitive_meta.examiners.all()]

    return LxSensitiveMeta.model_validate(
        {
            "examination_date": sensitive_meta.examination_date,
            "examination_time": sensitive_meta.examination_time,
            "casenumber": sensitive_meta.casenumber,
            "pseudo_patient": (
                str(sensitive_meta.pseudo_patient_id)
                if sensitive_meta.pseudo_patient_id is not None
                else None
            ),
            "pseudo_examination": (
                str(sensitive_meta.pseudo_examination_id)
                if sensitive_meta.pseudo_examination_id is not None
                else None
            ),
            "gender": gender_name,
            "pseudo_examiners": pseudo_examiners,
            "first_name": sensitive_meta.patient_first_name or "unknown",
            "last_name": sensitive_meta.patient_last_name or "unknown",
            "dob": (
                sensitive_meta.patient_dob.date()
                if sensitive_meta.patient_dob is not None
                else None
            ),
            "endoscope_type": sensitive_meta.endoscope_type,
            "endoscope_sn": sensitive_meta.endoscope_sn,
            "text": sensitive_meta.text,
            "anonymized_text": sensitive_meta.anonymized_text,
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
        active_file = video.active_file
    except Exception as exc:
        raise ValueError(f"Video {video.pk} has no active file") from exc
    active_name = getattr(active_file, "name", None)
    if not active_name:
        raise ValueError(f"Video {video.pk} has no active file")
    active_path = Path(active_name)
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

    data = {
        "uuid": str(video.uuid),
        "patient": str(video.patient_id) if video.patient_id is not None else None,
        "patient_examination": (
            str(video.examination_id) if video.examination_id is not None else None
        ),
        "fnd": {
            "file": str(active_path),
            "dir": str(Path(active_path).parent),
            "files": [],
            "dirs": [],
        },
        "anonymization_state": resolve_lx_anonymization_state(video),
        "sensitive_meta": lx_sensitive_meta,
        "patient_video_segments": patient_video_segments,
        "external_ids": external_ids,
    }

    return PatientVideoFile.model_validate(data)
