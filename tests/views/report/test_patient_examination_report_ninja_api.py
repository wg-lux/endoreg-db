# tests/views/report/test_patient_examination_report_ninja_api.py

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, TypedDict, cast

import pytest
from django.contrib.auth.models import User
from django.test import Client
from lx_dtypes.models.contracts.patient_examination_report import (
    ReportExportFrameDetailData,
    ReportPersistedArtifactsData,
    SegmentFrameSelectorResponseData,
)

from endoreg_db.models import (
    Center,
    Examination,
    Frame,
    InformationSource,
    LabelVideoSegment,
    Patient,
    PatientExamination,
    PatientExaminationReport,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.utils.file_operations import atomic_write_file, safe_rmtree
from endoreg_db.utils.paths import protected_media_root
from endoreg_db.services.report_runtime_validation import ReportRuntimeValidationError

REPORT_API_MODULE = "endoreg_db.views.report.patient_examination_report"
API_PREFIX = "/api/patient-examination-reports"


def _successful_runtime_validation(
    *_args: object,
    **_kwargs: object,
) -> dict[str, object]:
    return {"ok": True, "issues": []}


class _ReportHeaderPayload(TypedDict):
    patient_label: str
    patient_birth_date: str


class _ReportBlockPayload(TypedDict, total=False):
    type: str
    text: str
    image_paths: list[str]
    captions: list[str]


class _RenderPdfPayload(TypedDict):
    header: _ReportHeaderPayload
    blocks: list[_ReportBlockPayload]


class _MakeReportResponse(TypedDict):
    report: dict[str, Any]
    warnings: list[str]
    included_frame_count: int
    included_frames: list[ReportExportFrameDetailData]
    persisted_report_artifact_id: int | None
    persisted_pdf_artifact_id: int | None
    persisted_artifacts: ReportPersistedArtifactsData | None


@pytest.fixture
def staff_user(db: Any) -> User:
    return User.objects.create_user(
        username=f"report-staff-{uuid.uuid4().hex}",
        password="pw",
        is_staff=True,
    )


@pytest.fixture
def logged_in_client(client: Client, staff_user: User) -> Client:
    client.force_login(staff_user)
    return client


@pytest.fixture
def report_center(db: Any) -> Center:
    return Center.objects.create(name=f"Report Center {uuid.uuid4().hex}")


@pytest.fixture
def report_patient(report_center: Center) -> Patient:
    return Patient.objects.create(
        first_name="Pseudo",
        last_name="Patient",
        center=report_center,
        is_real_person=False,
        patient_hash=f"report-patient-{uuid.uuid4().hex}",
    )


@pytest.fixture
def report_examination(db: Any) -> Examination:
    return Examination.objects.create(name=f"report_exam_{uuid.uuid4().hex}")


@pytest.fixture
def patient_examination(
    report_patient: Patient,
    report_examination: Examination,
) -> PatientExamination:
    return PatientExamination.objects.create(
        patient=report_patient,
        examination=report_examination,
        knowledge_base_module="star_upper_gi",
        knowledge_base_version="0.1.2",
        date_start="2026-02-24",
        hash=f"report-pe-{uuid.uuid4().hex}",
    )


@pytest.fixture
def selector_context(
    staff_user: User,
    report_center: Center,
    report_patient: Patient,
    patient_examination: PatientExamination,
) -> SimpleNamespace:
    video = VideoFile.objects.create(
        center=report_center,
        video_hash=f"selector-video-{uuid.uuid4().hex}",
        examination=patient_examination,
        patient=report_patient,
        fps=25.0,
        frame_count=100,
        original_file_name="selector.mp4",
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        start_frame_number=10,
        end_frame_number=20,
    )
    for frame_number in [10, 12, 15, 17, 20]:
        Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=f"frame_{frame_number:04d}.jpg",
            timestamp=frame_number / 25.0,
            is_extracted=True,
        )

    return SimpleNamespace(
        user=staff_user,
        center=report_center,
        patient=report_patient,
        patient_examination=patient_examination,
        video=video,
        segment=segment,
    )


@pytest.fixture
def export_context(
    staff_user: User,
    report_center: Center,
    report_patient: Patient,
    patient_examination: PatientExamination,
) -> Iterator[SimpleNamespace]:
    video = VideoFile.objects.create(
        center=report_center,
        video_hash=f"export-report-video-{uuid.uuid4().hex}",
        examination=patient_examination,
        patient=report_patient,
        fps=25.0,
        frame_count=100,
        original_file_name="export-report.mp4",
    )

    frame_dir = protected_media_root() / f"pytest_report_export_{uuid.uuid4().hex}"
    video.frame_dir = str(frame_dir)
    video.save(update_fields=["frame_dir"])

    prediction_source = InformationSource.objects.create(
        name=f"prediction-{uuid.uuid4().hex}"
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        source=prediction_source,
        start_frame_number=10,
        end_frame_number=20,
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=12,
        relative_path="frame_0000012.jpg",
        timestamp=0.48,
        is_extracted=True,
    )
    atomic_write_file(
        destination=frame.file_path,
        content=[b"fake frame bytes"],
    )

    report = PatientExaminationReport.objects.create(
        patient_examination=patient_examination,
        template_name="star_upper_gi_main",
        knowledge_base_module="star_upper_gi",
        knowledge_base_version="0.1.2",
        title="Exportable report",
        status=PatientExaminationReport.Status.DRAFT,
        rendered_text="AI prediction based report text.",
        editor_payload={
            "report_segment_frame_selections": {
                str(segment.pk): {
                    "segment_id": segment.pk,
                    "video_id": video.pk,
                    "frame_id": frame.pk,
                    "frame_number": frame.frame_number,
                }
            }
        },
        created_by=staff_user,
        updated_by=staff_user,
    )

    ctx = SimpleNamespace(
        user=staff_user,
        center=report_center,
        patient=report_patient,
        patient_examination=patient_examination,
        video=video,
        frame_dir=frame_dir,
        prediction_source=prediction_source,
        segment=segment,
        frame=frame,
        report=report,
    )

    try:
        yield ctx
    finally:
        safe_rmtree(frame_dir, missing_ok=True)


def _selector_url(
    patient_examination_id: int,
    *,
    report_id: int | None = None,
) -> str:
    url = (
        f"{API_PREFIX}/segment-frame-selector"
        f"?patient_examination_id={patient_examination_id}"
    )
    if report_id is not None:
        url += f"&report_id={report_id}"
    return url


def _get_segment_item(
    payload: SegmentFrameSelectorResponseData,
    *,
    segment_id: int,
) -> dict[str, Any]:
    items = payload.get("results", [])
    assert items, "Expected at least one segment in selector response"
    return cast(
        dict[str, Any],
        next(item for item in items if item["segment_id"] == segment_id),
    )


def _json_body(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


@pytest.mark.django_db
def test_history_context_requires_patient_examination_id(
    logged_in_client: Client,
) -> None:
    resp = logged_in_client.get(f"{API_PREFIX}/history-context")

    # Ninja validation errors are usually 422, unless you installed a custom handler.
    assert resp.status_code == 422
    assert "patient_examination_id" in str(resp.json())


@pytest.mark.django_db
def test_history_context_rejects_non_integer_patient_examination_id(
    logged_in_client: Client,
) -> None:
    resp = logged_in_client.get(
        f"{API_PREFIX}/history-context?patient_examination_id=abc"
    )

    assert resp.status_code == 422
    assert "patient_examination_id" in str(resp.json())


@pytest.mark.django_db
def test_history_context_returns_payload(
    logged_in_client: Client,
    patient_examination: PatientExamination,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    api_module = importlib.import_module(REPORT_API_MODULE)

    def fake_history_context(
        patient_examination_arg: PatientExamination,
        *,
        limit: int,
    ) -> dict[str, Any]:
        return {
            "previous_examinations": [],
            "limit": limit,
            "patient_examination_id": int(cast(Any, patient_examination_arg).pk),
        }

    monkeypatch.setattr(
        api_module,
        "get_patient_examination_history_context",
        fake_history_context,
    )

    resp = logged_in_client.get(
        f"{API_PREFIX}/history-context"
        f"?patient_examination_id={patient_examination.pk}&limit=7"
    )

    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert data["previous_examinations"] == []
    assert data["limit"] == 7
    assert data["patient_examination_id"] == patient_examination.pk


@pytest.mark.django_db
def test_save_submission_returns_history_and_warnings(
    logged_in_client: Client,
    patient_examination: PatientExamination,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    api_module = importlib.import_module(REPORT_API_MODULE)

    fake_report = PatientExaminationReport.objects.create(
        patient_examination=patient_examination,
        template_name="t",
        status=PatientExaminationReport.Status.DRAFT,
        editor_payload={},
        patient_context_snapshot={},
        history_context_snapshot={},
        rendered_text="",
    )

    def fake_save_report_submission(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            report=fake_report,
            created=True,
            warnings=["nag"],
            history_context={"previous_examinations": []},
            persisted_dtypes_record=None,
            persisted_dtypes_record_updated_at=None,
            persisted_report_artifact_id=None,
            persisted_pdf_artifact_id=None,
        )

    monkeypatch.setattr(
        api_module,
        "save_report_submission",
        fake_save_report_submission,
    )

    resp = logged_in_client.post(
        f"{API_PREFIX}/save-submission",
        data=_json_body(
            {
                "patient_examination_id": patient_examination.pk,
                "template_name": "t",
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "status": "draft",
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 201, resp.content
    data = resp.json()

    assert data["created"] is True
    assert data["history_context"] == {"previous_examinations": []}
    assert data["warnings"] == ["nag"]
    assert data["persisted_dtypes_record"] is None
    assert data["persisted_dtypes_record_updated_at"] is None
    assert data["persisted_report_artifact_id"] is None
    assert data["persisted_pdf_artifact_id"] is None
    assert data["persisted_artifacts"] is None
    assert data["report"]["id"] == fake_report.pk


@pytest.mark.django_db
def test_save_submission_rejects_invalid_payload(
    logged_in_client: Client,
) -> None:
    resp = logged_in_client.post(
        f"{API_PREFIX}/save-submission",
        data=_json_body(
            {
                "patient_examination_id": 0,
                "template_name": "",
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "patient_examination_id" in str(body)
    assert "template_name" in str(body)


@pytest.mark.django_db
def test_create_report_minimal_payload(
    logged_in_client: Client,
    patient_examination: PatientExamination,
) -> None:
    resp = logged_in_client.post(
        f"{API_PREFIX}/save-submission",
        data=_json_body(
            {
                "patient_examination_id": patient_examination.pk,
                "template_name": "star_upper_gi_main",
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "status": "draft",
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 201, resp.content
    data = resp.json()

    report = PatientExaminationReport.objects.get(pk=data["report"]["id"])
    assert int(cast(Any, report).patient_examination_id) == patient_examination.pk
    assert report.template_name == "star_upper_gi_main"
    assert report.status == PatientExaminationReport.Status.DRAFT
    assert report.version == 1
    assert report.is_active is True


@pytest.mark.django_db
def test_explicit_frontend_identity_migrates_examination_and_report(
    logged_in_client: Client,
    patient_examination: PatientExamination,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient_examination.knowledge_base_module = "report_template_examples"
    patient_examination.knowledge_base_version = "0.1.0"
    patient_examination.save(
        update_fields=["knowledge_base_module", "knowledge_base_version"]
    )
    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        _successful_runtime_validation,
    )
    resp = logged_in_client.post(
        f"{API_PREFIX}/save-submission",
        data=_json_body(
            {
                "patient_examination_id": patient_examination.pk,
                "template_name": "star_upper_gi_main",
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "title": "Initial Finalized Draft",
                "status": "final",
                "editor_payload": {"sections": [{"id": "findings"}]},
                "rendered_text": "Rendered report text",
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 201, resp.content
    data = resp.json()

    report = PatientExaminationReport.objects.get(pk=data["report"]["id"])
    assert report.status == PatientExaminationReport.Status.FINAL
    assert report.title == "Initial Finalized Draft"
    assert report.language == "de"
    assert report.knowledge_base_module == "star_upper_gi"
    assert report.knowledge_base_version == "0.1.2"
    assert report.editor_payload == {
        "sections": [{"id": "findings"}],
        "report_language": "de",
    }
    assert data["report"]["language"] == "de"
    assert data["report"]["knowledge_base_module"] == "star_upper_gi"
    assert data["report"]["knowledge_base_version"] == "0.1.2"
    patient_examination.refresh_from_db()
    assert patient_examination.knowledge_base_module == "star_upper_gi"
    assert patient_examination.knowledge_base_version == "0.1.2"
    assert report.rendered_text == "Rendered report text"
    assert data["history_context"]["previous_examinations"] == []


@pytest.mark.django_db
def test_final_submission_returns_422_and_rolls_back_failed_template_validation(
    logged_in_client: Client,
    patient_examination: PatientExamination,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_validation(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {"code": "required_finding_missing", "message": "Befund fehlt."}
                ],
            }
        )

    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        fail_validation,
    )

    response = logged_in_client.post(
        f"{API_PREFIX}/save-submission",
        data=_json_body(
            {
                "patient_examination_id": patient_examination.pk,
                "template_name": "star_upper_gi_main",
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "status": "final",
                "rendered_text": "Unvollständiger Befund",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 422
    assert "required_finding_missing" in str(response.json())
    assert PatientExaminationReport.objects.count() == 0


@pytest.mark.django_db
def test_segment_frame_selector_get_auto_creates_draft_report(
    logged_in_client: Client,
    selector_context: SimpleNamespace,
) -> None:
    resp = logged_in_client.get(_selector_url(selector_context.patient_examination.pk))

    assert resp.status_code == 200, resp.content
    data = cast(SegmentFrameSelectorResponseData, resp.json())

    assert data["patient_examination_id"] == selector_context.patient_examination.pk
    assert data["auto_created_report"] is True
    assert data["storage_key"] == "report_segment_frame_selections"
    assert data["count"] >= 1

    report_id = data["report_id"]
    report = PatientExaminationReport.objects.get(pk=report_id)
    assert report.status == PatientExaminationReport.Status.DRAFT
    assert report.template_name == "segment_frame_selection"
    assert "report_segment_frame_selections" in (report.editor_payload or {})

    item = _get_segment_item(data, segment_id=selector_context.segment.pk)
    assert item["segment_id"] == selector_context.segment.pk
    assert (
        item["controls"]["step_backward_5_frame_number"]
        >= selector_context.segment.start_frame_number
    )
    assert (
        item["controls"]["step_forward_5_frame_number"]
        <= selector_context.segment.end_frame_number
    )


@pytest.mark.django_db
def test_segment_frame_selector_includes_video_segments_by_shared_sensitive_meta(
    logged_in_client: Client,
    selector_context: SimpleNamespace,
) -> None:
    sensitive_meta = SensitiveMeta.objects.create(center=selector_context.center)

    RawPdfFile.objects.create(
        pdf_hash=f"selector-pdf-{uuid.uuid4().hex}",
        center=selector_context.center,
        patient=selector_context.patient,
        examination=selector_context.patient_examination,
        sensitive_meta=sensitive_meta,
    )

    linked_by_meta_video = VideoFile.objects.create(
        center=selector_context.center,
        video_hash=f"selector-shared-meta-video-{uuid.uuid4().hex}",
        patient=selector_context.patient,
        sensitive_meta=sensitive_meta,
        fps=25.0,
        frame_count=100,
        original_file_name="selector-shared-meta.mp4",
    )

    linked_by_meta_segment = LabelVideoSegment.objects.create(
        video_file=linked_by_meta_video,
        start_frame_number=30,
        end_frame_number=40,
    )

    Frame.objects.create(
        video=linked_by_meta_video,
        frame_number=35,
        relative_path="frame_0035.jpg",
        timestamp=1.4,
        is_extracted=True,
    )

    resp = logged_in_client.get(_selector_url(selector_context.patient_examination.pk))

    assert resp.status_code == 200, resp.content
    data = cast(SegmentFrameSelectorResponseData, resp.json())

    segment_ids = {item["segment_id"] for item in data["results"]}
    assert selector_context.segment.pk in segment_ids
    assert linked_by_meta_segment.pk in segment_ids

    patch_resp = logged_in_client.patch(
        _selector_url(
            selector_context.patient_examination.pk,
            report_id=data["report_id"],
        ),
        data=_json_body(
            {
                "patient_examination_id": selector_context.patient_examination.pk,
                "report_id": data["report_id"],
                "segment_id": linked_by_meta_segment.pk,
                "frame_number": 35,
            }
        ),
        content_type="application/json",
    )

    assert patch_resp.status_code == 200, patch_resp.content
    patch_data = cast(SegmentFrameSelectorResponseData, patch_resp.json())

    shared_item = _get_segment_item(
        patch_data,
        segment_id=linked_by_meta_segment.pk,
    )
    assert shared_item["selected_frame_number"] == 35


@pytest.mark.django_db
def test_segment_frame_selector_patch_random_step_set_and_clear(
    logged_in_client: Client,
    selector_context: SimpleNamespace,
) -> None:
    first = logged_in_client.get(_selector_url(selector_context.patient_examination.pk))
    assert first.status_code == 200, first.content

    first_data = cast(SegmentFrameSelectorResponseData, first.json())
    report_id = first_data["report_id"]
    segment_id = selector_context.segment.pk
    patient_examination_id = selector_context.patient_examination.pk

    resp_random = logged_in_client.patch(
        _selector_url(patient_examination_id, report_id=report_id),
        data=_json_body(
            {
                "patient_examination_id": patient_examination_id,
                "report_id": report_id,
                "segment_id": segment_id,
                "action": "random",
            }
        ),
        content_type="application/json",
    )

    assert resp_random.status_code == 200, resp_random.content
    random_data = cast(SegmentFrameSelectorResponseData, resp_random.json())
    random_item = _get_segment_item(random_data, segment_id=segment_id)
    random_selected = cast(int, random_item["selected_frame_number"])

    assert selector_context.segment.start_frame_number <= random_selected
    assert random_selected <= selector_context.segment.end_frame_number

    resp_step = logged_in_client.patch(
        _selector_url(patient_examination_id, report_id=report_id),
        data=_json_body(
            {
                "patient_examination_id": patient_examination_id,
                "report_id": report_id,
                "segment_id": segment_id,
                "action": "step",
                "step": 5,
            }
        ),
        content_type="application/json",
    )

    assert resp_step.status_code == 200, resp_step.content
    step_data = cast(SegmentFrameSelectorResponseData, resp_step.json())
    step_item = _get_segment_item(step_data, segment_id=segment_id)
    step_selected = step_item["selected_frame_number"]

    assert step_selected == min(
        selector_context.segment.end_frame_number,
        random_selected + 5,
    )

    resp_set = logged_in_client.patch(
        _selector_url(patient_examination_id, report_id=report_id),
        data=_json_body(
            {
                "patient_examination_id": patient_examination_id,
                "report_id": report_id,
                "segment_id": segment_id,
                "action": "set",
                "frame_number": 17,
            }
        ),
        content_type="application/json",
    )

    assert resp_set.status_code == 200, resp_set.content
    set_data = cast(SegmentFrameSelectorResponseData, resp_set.json())
    set_item = _get_segment_item(set_data, segment_id=segment_id)

    assert set_item["selected_frame_number"] == 17
    assert set_item["selected_frame"] is not None
    assert set_item["selected_frame"]["frame_number"] == 17

    report = PatientExaminationReport.objects.get(pk=report_id)
    payload = cast(dict[str, Any], report.editor_payload or {})
    stored = cast(
        dict[str, dict[str, Any]],
        payload.get("report_segment_frame_selections", {}),
    )

    assert str(segment_id) in stored
    assert stored[str(segment_id)]["frame_number"] == 17

    resp_clear = logged_in_client.patch(
        _selector_url(patient_examination_id, report_id=report_id),
        data=_json_body(
            {
                "patient_examination_id": patient_examination_id,
                "report_id": report_id,
                "segment_id": segment_id,
                "action": "clear",
            }
        ),
        content_type="application/json",
    )

    assert resp_clear.status_code == 200, resp_clear.content

    report.refresh_from_db()
    payload_after_clear = cast(dict[str, Any], report.editor_payload or {})
    stored_after_clear = cast(
        dict[str, dict[str, Any]],
        payload_after_clear.get("report_segment_frame_selections", {}),
    )

    assert str(segment_id) not in stored_after_clear


@pytest.mark.django_db
def test_segment_frame_selector_patch_rejects_unknown_segment(
    logged_in_client: Client,
    selector_context: SimpleNamespace,
) -> None:
    first = logged_in_client.get(_selector_url(selector_context.patient_examination.pk))
    assert first.status_code == 200, first.content
    report_id = first.json()["report_id"]

    resp = logged_in_client.patch(
        _selector_url(selector_context.patient_examination.pk, report_id=report_id),
        data=_json_body(
            {
                "patient_examination_id": selector_context.patient_examination.pk,
                "report_id": report_id,
                "segment_id": 999999999,
                "frame_number": 17,
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 404
    assert "Segment not found" in str(resp.json())


@pytest.mark.django_db
def test_make_report_returns_404_when_no_report_exists(
    logged_in_client: Client,
    patient_examination: PatientExamination,
) -> None:
    resp = logged_in_client.post(
        f"{API_PREFIX}/make-report",
        data=_json_body(
            {
                "patient_examination_id": patient_examination.pk,
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "patient": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "dob": "1815-12-10",
                },
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 404
    assert "No report found" in str(resp.json())


@pytest.mark.django_db
def test_make_report_renders_selected_prediction_frame_with_patient_identity(
    logged_in_client: Client,
    export_context: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.services import report_pdf_renderer as renderer_module

    captured_payload: _RenderPdfPayload = {
        "header": {"patient_label": "", "patient_birth_date": ""},
        "blocks": [],
    }

    def fake_render_pdf(
        payload: _RenderPdfPayload,
        *,
        output_path: Path,
        timeout_seconds: int = 20,
    ) -> Path:
        captured_payload.update(payload)
        atomic_write_file(
            destination=output_path,
            content=[b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"],
        )
        return output_path

    monkeypatch.setattr(
        renderer_module,
        "render_pdf_with_rust_renderer",
        fake_render_pdf,
    )
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report.validate_final_report_submission",
        _successful_runtime_validation,
    )

    resp = logged_in_client.post(
        f"{API_PREFIX}/make-report",
        data=_json_body(
            {
                "patient_examination_id": export_context.patient_examination.pk,
                "report_id": export_context.report.pk,
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "patient": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "dob": "1815-12-10",
                },
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200, resp.content
    data = cast(_MakeReportResponse, resp.json())

    export_context.report.refresh_from_db()

    assert export_context.report.status == PatientExaminationReport.Status.FINAL
    assert data["included_frame_count"] == 1
    assert data["persisted_pdf_artifact_id"]
    assert data["persisted_artifacts"] is not None
    assert data["persisted_artifacts"]["pdf_view_url"]
    assert RawPdfFile.objects.filter(pk=data["persisted_pdf_artifact_id"]).exists()

    assert captured_payload["header"]["patient_label"] == "Ada Lovelace"
    assert captured_payload["header"]["patient_birth_date"] == "1815-12-10"

    image_grid = next(
        block
        for block in captured_payload["blocks"]
        if block.get("type") == "image_grid"
    )
    image_paths = image_grid.get("image_paths")
    captions = image_grid.get("captions")
    first_block_text = captured_payload["blocks"][0].get("text", "")

    assert image_paths == [str(export_context.frame.file_path)]
    assert captions is not None
    assert "frame 12" in captions[0]
    assert "AI prediction based report text." in first_block_text


@pytest.mark.django_db
def test_make_report_rejects_invalid_patient_identity(
    logged_in_client: Client,
    export_context: SimpleNamespace,
) -> None:
    resp = logged_in_client.post(
        f"{API_PREFIX}/make-report",
        data=_json_body(
            {
                "patient_examination_id": export_context.patient_examination.pk,
                "report_id": export_context.report.pk,
                "knowledge_base_module": "star_upper_gi",
                "knowledge_base_version": "0.1.2",
                "patient": {
                    "first_name": "",
                    "last_name": "Lovelace",
                    "dob": "1815-12-10",
                },
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 422
    assert "first_name" in str(resp.json())


@pytest.mark.django_db
def test_report_list_requires_scope_for_non_privileged_user(
    client: Client,
    patient_examination: PatientExamination,
) -> None:
    user = User.objects.create_user(
        username=f"nonpriv-report-user-{uuid.uuid4().hex}",
        password="pw",
        is_staff=False,
        is_superuser=False,
    )
    client.force_login(user)

    PatientExaminationReport.objects.create(
        patient_examination=patient_examination,
        template_name="star_upper_gi_main",
        status=PatientExaminationReport.Status.DRAFT,
    )

    resp = client.get(f"{API_PREFIX}/")

    assert resp.status_code == 200, resp.content
    assert resp.json() == []
