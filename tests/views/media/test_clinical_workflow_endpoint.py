from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.utils import timezone

from endoreg_db.models import Center, PortalUserInfo, RawPdfFile, VideoFile, VideoState
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.services.clinical_lifecycle.persistence import apply_workflow_event
from endoreg_db.services.clinical_lifecycle.state_machine import (
    ClinicalLifecycleEvent,
    ClinicalRecordKind,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def center() -> Center:
    return Center.objects.create(name=f"workflow-api-{uuid4().hex[:8]}")


def _meta(center: Center) -> SensitiveMeta:
    return SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
        examination_date=date(2025, 11, 27),
        patient_gender=Gender.objects.create(name=f"workflow-api-{uuid4().hex[:8]}"),
    )


def _video(center: Center, state: VideoState | None = None) -> VideoFile:
    return VideoFile.objects.create(
        center=center,
        raw_video_hash=f"workflow-api-{uuid4().hex[:8]}",
        sensitive_meta=_meta(center),
        state=state if state is not None else VideoState.objects.create(),
    )


@pytest.fixture
def client(center: Center) -> Client:
    user = User.objects.create_user(username=f"workflow-{uuid4().hex[:8]}")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    api = Client()
    api.force_login(user)
    return api


VIDEO_ITEM = "/api/media/videos/{}/workflow/"
REPORT_ITEM = "/api/media/pdfs/{}/workflow/"
STAGES = "/api/media/workflow/stages/"


def test_video_item_matches_frontend_contract(client: Client, center: Center) -> None:
    video = _video(center)

    response = client.get(VIDEO_ITEM.format(video.pk))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "kind",
        "record_id",
        "privacy",
        "annotation",
        "release",
        "version",
        "source",
        "allowed_events",
    }
    assert (body["kind"], body["record_id"], body["version"], body["source"]) == (
        "video",
        video.pk,
        0,
        "projected",
    )
    assert (body["privacy"], body["annotation"], body["release"]) == (
        "import_pending",
        "not_available",
        "not_released",
    )
    assert "import_started" in body["allowed_events"]
    assert "raw_purged" not in body["allowed_events"]


def test_video_item_reports_stored_state_after_event(
    client: Client, center: Center
) -> None:
    video = _video(center)
    apply_workflow_event(
        kind=ClinicalRecordKind.VIDEO,
        record_id=video.pk,
        event=ClinicalLifecycleEvent.IMPORT_STARTED,
        idempotency_key="import-1",
    )

    body = client.get(VIDEO_ITEM.format(video.pk)).json()

    assert (body["privacy"], body["version"], body["source"]) == (
        "importing",
        1,
        "stored",
    )


def test_report_item_uses_report_route(client: Client, center: Center) -> None:
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash=f"workflow-report-{uuid4().hex[:8]}",
        sensitive_meta=_meta(center),
    )

    response = client.get(REPORT_ITEM.format(report.pk))

    assert response.status_code == 200
    assert (response.json()["kind"], response.json()["record_id"]) == (
        "report",
        report.pk,
    )


def test_inconsistent_facts_are_surfaced_not_guessed(
    client: Client, center: Center
) -> None:
    # Validated without a processed artifact cannot be mapped to any state.
    video = _video(center, VideoState.objects.create(anonymized=True))
    VideoState.objects.filter(pk=video.state_id).update(anonymization_validated=True)

    response = client.get(VIDEO_ITEM.format(video.pk))

    assert response.status_code == 409
    assert "anonymized_without_artifact" in response.json()["violations"]


def test_missing_record_is_not_found(client: Client) -> None:
    assert client.get(VIDEO_ITEM.format(999999)).status_code == 404


def test_other_center_item_is_hidden(client: Client) -> None:
    video = _video(Center.objects.create(name=f"workflow-other-{uuid4().hex[:8]}"))
    assert client.get(VIDEO_ITEM.format(video.pk)).status_code == 404


def test_stage_counts_match_contract_and_respect_centers(
    client: Client, center: Center
) -> None:
    _video(center)
    _video(Center.objects.create(name=f"workflow-other-{uuid4().hex[:8]}"))

    response = client.get(STAGES)

    assert response.status_code == 200
    body = response.json()
    assert [entry["stage"] for entry in body["stages"]] == [
        "validate",
        "annotate",
        "approve_export",
    ]
    validate = body["stages"][0]
    # Only the own-center import feeds the validation stage.
    assert (validate["count"], validate["transient"]) == (0, 1)
    assert body["unmappable"] == 0


@pytest.mark.parametrize(
    "url",
    [VIDEO_ITEM.format(1), REPORT_ITEM.format(1), STAGES],
)
def test_anonymous_request_is_rejected(url: str) -> None:
    assert Client().get(url).status_code in {401, 403}


def test_role_is_required_outside_debug(client: Client, center: Center) -> None:
    video = _video(center)
    url = VIDEO_ITEM.format(video.pk)
    with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
        assert client.get(url).status_code == 403
        assert client.get(STAGES).status_code == 403
        user = User.objects.get(pk=client.session["_auth_user_id"])
        user.groups.add(Group.objects.create(name="video:read"))  # pyright: ignore[reportUnknownMemberType]
        assert client.get(url).status_code == 200
        assert client.get(STAGES).status_code == 200
