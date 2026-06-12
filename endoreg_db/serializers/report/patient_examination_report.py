from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from lx_dtypes.models.contracts.patient_examination_report import (
    PatientExaminationReportMakeReportData,
    PatientExaminationReportMakeReportPayload,
    PatientExaminationReportSubmissionData,
    PatientExaminationReportSubmissionPayload,
    PatientReportIdentityData,
    PatientReportIdentityPayload,
    ReportExportFrameDetailData,
    ReportJsonObject,
    ReportPersistedArtifactsData,
    ReportPersistedArtifactsPayload,
    ReportSegmentFrameSelectionData,
    ReportSegmentFrameSelectionPayload,
    ReportSegmentSelectionMap,
    ReportStatus,
    SegmentAttachedFindingData,
    SegmentFrameControlsData,
    SegmentFramePreviewData,
    SegmentFrameSelectionAction,
    SegmentFrameSelectorItemData,
    SegmentFrameSelectorPatchData,
    SegmentFrameSelectorPatchPayload,
    SegmentFrameSelectorQueryData,
    SegmentFrameSelectorQueryPayload,
    SegmentFrameSelectorResponseData,
    SegmentSelectionMetaData,
    dump_make_report_payload,
    dump_persisted_artifacts_payload,
    dump_report_submission_payload,
    dump_segment_frame_selection_payload,
    dump_selector_patch_payload,
    dump_selector_query_payload,
    validate_segment_selection_map,
)
from ninja import Schema
from pydantic import ConfigDict, Field, model_validator

from endoreg_db.models.report.patient_examination_report import PatientExaminationReport


def _mapping_payload(value: Any) -> dict[str, Any]:
    """
    Convert incoming Ninja/Pydantic payloads into plain dictionaries before
    validating them against the lx_dtypes contract models.
    """
    if isinstance(value, Schema):
        return value.model_dump(mode="python", exclude_none=True)
    if isinstance(value, Mapping):
        mapped = {str(k): v for k, v in cast(Mapping[object, object], value).items()}
        if set(mapped) == {"payload"}:
            payload = mapped["payload"]
            if not isinstance(payload, Mapping):
                raise ValueError("Report payload wrapper must contain an object.")
            return {
                str(k): v for k, v in cast(Mapping[object, object], payload).items()
            }
        return mapped
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, Mapping):
            raise ValueError("Report payload JSON must decode to an object.")
        return _mapping_payload(decoded)
    return cast(dict[str, Any], value)


def _empty_report_json_object() -> ReportJsonObject:
    return {}


def _empty_report_json_list() -> list[ReportJsonObject]:
    return []


class PatientExaminationReportSchema(Schema):
    """
    Response schema for PatientExaminationReport.

    This replaces the old DRF ModelSerializer and exposes the previously sourced
    patient_examination.dtypes_record fields through Ninja resolvers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_examination_id: int

    template_name: str
    template_version: str
    template_hash: str
    title: str
    status: str

    editor_payload: ReportJsonObject
    patient_context_snapshot: ReportJsonObject
    history_context_snapshot: ReportJsonObject
    rendered_text: str

    dtypes_record: ReportJsonObject | None = None
    dtypes_record_updated_at: datetime | None = None

    version: int
    is_active: bool

    created_at: datetime
    updated_at: datetime
    finalized_at: datetime | None = None

    created_by_id: int | None = None
    updated_by_id: int | None = None
    finalized_by_id: int | None = None

    @staticmethod
    def resolve_patient_examination_id(obj: PatientExaminationReport) -> int:
        value = getattr(obj, "patient_examination_id", None)
        return int(value) if isinstance(value, int) else 0

    @staticmethod
    def resolve_dtypes_record(obj: PatientExaminationReport) -> ReportJsonObject | None:
        patient_examination = getattr(obj, "patient_examination", None)
        if patient_examination is None:
            return None
        return cast(
            ReportJsonObject | None,
            getattr(patient_examination, "dtypes_record", None),
        )

    @staticmethod
    def resolve_dtypes_record_updated_at(
        obj: PatientExaminationReport,
    ) -> datetime | None:
        patient_examination = getattr(obj, "patient_examination", None)
        if patient_examination is None:
            return None
        return cast(
            datetime | None,
            getattr(patient_examination, "dtypes_record_updated_at", None),
        )

    @staticmethod
    def resolve_created_by_id(obj: PatientExaminationReport) -> int | None:
        return cast(int | None, getattr(obj, "created_by_id", None))

    @staticmethod
    def resolve_updated_by_id(obj: PatientExaminationReport) -> int | None:
        return cast(int | None, getattr(obj, "updated_by_id", None))

    @staticmethod
    def resolve_finalized_by_id(obj: PatientExaminationReport) -> int | None:
        return cast(int | None, getattr(obj, "finalized_by_id", None))


class PatientReportIdentitySchema(Schema):
    first_name: str = Field(min_length=1, max_length=150)
    last_name: str = Field(min_length=1, max_length=150)
    dob: date

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = PatientReportIdentityPayload.model_validate(_mapping_payload(value))
        return payload.model_dump(mode="python")

    def to_contract_payload(self) -> PatientReportIdentityPayload:
        return PatientReportIdentityPayload.model_validate(
            self.model_dump(mode="python")
        )

    def to_contract_data(self) -> PatientReportIdentityData:
        return cast(
            PatientReportIdentityData,
            self.to_contract_payload().model_dump(mode="python"),
        )


class PatientExaminationReportSubmissionSchema(Schema):
    report_id: int | None = Field(default=None, ge=1)
    patient_examination_id: int = Field(ge=1)
    template_name: str = Field(min_length=1)

    template_version: str = ""
    template_hash: str = ""
    title: str = ""
    status: ReportStatus = "draft"

    rendered_text: str = ""
    editor_payload: ReportJsonObject = Field(default_factory=_empty_report_json_object)
    patient_data: ReportJsonObject = Field(default_factory=_empty_report_json_object)
    indications: list[ReportJsonObject] = Field(default_factory=_empty_report_json_list)
    findings: list[ReportJsonObject] = Field(default_factory=_empty_report_json_list)

    expected_version: int | None = Field(default=None, ge=1)
    history_limit: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = PatientExaminationReportSubmissionPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_report_submission_payload(payload),
        )

    def to_contract_payload(self) -> PatientExaminationReportSubmissionPayload:
        return PatientExaminationReportSubmissionPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> PatientExaminationReportSubmissionData:
        return dump_report_submission_payload(self.to_contract_payload())


class PatientExaminationReportMakeReportSchema(Schema):
    patient_examination_id: int = Field(ge=1)
    report_id: int | None = Field(default=None, ge=1)
    patient: PatientReportIdentitySchema
    max_frames: int = Field(default=12, ge=1, le=24)

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = PatientExaminationReportMakeReportPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_make_report_payload(payload),
        )

    def to_contract_payload(self) -> PatientExaminationReportMakeReportPayload:
        return PatientExaminationReportMakeReportPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> PatientExaminationReportMakeReportData:
        return dump_make_report_payload(self.to_contract_payload())


class ReportPersistedArtifactsSchema(Schema):
    full_report_id: int | None = Field(default=None, ge=1)
    pdf_id: int | None = Field(default=None, ge=1)
    pdf_view_url: str | None = None
    pdf_download_url: str | None = None
    patient_timeline_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = ReportPersistedArtifactsPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_persisted_artifacts_payload(payload),
        )

    def to_contract_payload(self) -> ReportPersistedArtifactsPayload:
        return ReportPersistedArtifactsPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> ReportPersistedArtifactsData:
        return dump_persisted_artifacts_payload(self.to_contract_payload())


class ReportSegmentFrameSelectionSchema(Schema):
    segment_id: int | None = Field(default=None, ge=1)
    video_id: int | None = Field(default=None, ge=1)
    frame_number: int | None = Field(default=None, ge=0)
    frame_id: int | None = Field(default=None, ge=1)
    relative_path: str | None = None
    finding_id: int | None = Field(default=None, ge=1)
    patient_finding_id: int | None = Field(default=None, ge=1)
    updated_at: str | None = None
    selection_source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = ReportSegmentFrameSelectionPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_segment_frame_selection_payload(payload),
        )

    def to_contract_payload(self) -> ReportSegmentFrameSelectionPayload:
        return ReportSegmentFrameSelectionPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> ReportSegmentFrameSelectionData:
        return dump_segment_frame_selection_payload(self.to_contract_payload())


class SegmentFrameSelectorQuerySchema(Schema):
    patient_examination_id: int = Field(ge=1)
    report_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = SegmentFrameSelectorQueryPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_selector_query_payload(payload),
        )

    def to_contract_payload(self) -> SegmentFrameSelectorQueryPayload:
        return SegmentFrameSelectorQueryPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> SegmentFrameSelectorQueryData:
        return dump_selector_query_payload(self.to_contract_payload())


class SegmentFrameSelectorPatchSchema(SegmentFrameSelectorQuerySchema):
    segment_id: int = Field(ge=1)
    action: SegmentFrameSelectionAction = "set"
    frame_number: int | None = Field(default=None, ge=0)
    step: int = 5
    finding_id: int | None = Field(default=None, ge=1)
    template_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_contract(cls, value: Any) -> dict[str, Any]:
        payload = SegmentFrameSelectorPatchPayload.model_validate(
            _mapping_payload(value)
        )
        return cast(
            dict[str, Any],
            dump_selector_patch_payload(payload),
        )

    def to_contract_payload(self) -> SegmentFrameSelectorPatchPayload:
        return SegmentFrameSelectorPatchPayload.model_validate(
            self.model_dump(mode="python", exclude_none=True)
        )

    def to_contract_data(self) -> SegmentFrameSelectorPatchData:
        return dump_selector_patch_payload(self.to_contract_payload())


class SegmentFramePreviewSchema(Schema):
    frame_id: int
    frame_number: int
    timestamp: float | None
    relative_path: str
    file_exists: bool
    stream_url: str


class SegmentFrameControlsSchema(Schema):
    random_frame_number: int
    step_backward_5_frame_number: int
    step_forward_5_frame_number: int


class SegmentAttachedFindingSchema(Schema):
    patient_finding_id: int
    finding_id: int | None
    finding_name: str | None


class SegmentSelectionMetaSchema(Schema):
    updated_at: str | None
    selection_source: str | None


class SegmentFrameSelectorItemSchema(Schema):
    segment_id: int
    video_id: int
    label_id: int | None
    label_name: str | None
    start_frame_number: int
    end_frame_number: int
    segment_duration_seconds: float | None
    selected_frame_number: int | None
    selected_frame: SegmentFramePreviewSchema | None
    controls: SegmentFrameControlsSchema
    attached_finding: SegmentAttachedFindingSchema | None
    selection_meta: SegmentSelectionMetaSchema


class SegmentFrameSelectorResponseSchema(Schema):
    patient_examination_id: int
    report_id: int
    report_status: str
    report_template_name: str
    auto_created_report: bool
    storage_key: str
    count: int
    results: list[SegmentFrameSelectorItemSchema]


class ReportExportFrameDetailSchema(Schema):
    segment_id: int
    video_id: int
    frame_id: int
    frame_number: int
    label_name: str | None
    finding_name: str | None
    stream_url: str
    caption: str


def validate_segment_selection_map_payload(
    payload: object,
) -> ReportSegmentSelectionMap:
    return validate_segment_selection_map(payload)


__all__ = [
    "PatientExaminationReportSchema",
    "PatientExaminationReportSubmissionSchema",
    "PatientReportIdentitySchema",
    "PatientExaminationReportMakeReportSchema",
    "ReportPersistedArtifactsSchema",
    "ReportSegmentFrameSelectionSchema",
    "SegmentFrameSelectorQuerySchema",
    "SegmentFrameSelectorPatchSchema",
    "SegmentFramePreviewSchema",
    "SegmentFrameControlsSchema",
    "SegmentAttachedFindingSchema",
    "SegmentSelectionMetaSchema",
    "SegmentFrameSelectorItemSchema",
    "SegmentFrameSelectorResponseSchema",
    "ReportExportFrameDetailSchema",
    "validate_segment_selection_map_payload",
    "PatientExaminationReportMakeReportSchema",
    "PatientExaminationReportMakeReportData",
    "PatientExaminationReportSubmissionData",
    "PatientReportIdentityData",
    "ReportPersistedArtifactsData",
    "ReportSegmentFrameSelectionData",
    "ReportSegmentSelectionMap",
    "SegmentFrameSelectorQueryData",
    "SegmentFrameSelectorPatchData",
    "SegmentFrameSelectorResponseData",
    "SegmentFrameSelectorItemData",
    "SegmentFramePreviewData",
    "SegmentFrameControlsData",
    "SegmentAttachedFindingData",
    "SegmentSelectionMetaData",
    "ReportExportFrameDetailData",
]
