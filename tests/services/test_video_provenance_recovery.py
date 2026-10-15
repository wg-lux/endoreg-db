from __future__ import annotations

from fractions import Fraction
from django.forms.models import model_to_dict
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from lx_dtypes.models.contracts.video_provenance import (
    VideoProvenanceRecovery,
    VideoFrameTimeline,
)
from pydantic import ValidationError
from rest_framework.test import APIClient

from endoreg_db.models import PortalUserInfo, VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.schemas.hub_payloads import TransferProvenancePayload
from endoreg_db.services.hub import video_provenance as exchange
from endoreg_db.services.hub import video_provenance_recovery as service
from endoreg_db.services.media.operation_gate import create_video_stream_lease
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.hashs import get_file_hash
from tests.services.test_post_validation_generation_cleanup import video as video
from tests.services.test_frame_provenance import timeline

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def prepared(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> tuple[VideoFile, User, list[Path]]:
    video.raw_video_hash = get_file_hash(video.raw_file)
    state = video.get_or_create_state()
    state.anonymized = True
    state.save()
    video.save()
    user = User.objects.create_user(username="ordinary-operator")
    PortalUserInfo.objects.create(user=user).centers.add(video.center)
    paths: list[Path] = []

    def capture(path: Path, *_args: object, **_kwargs: object) -> VideoFrameTimeline:
        paths.append(path)
        assert not path.read_bytes().startswith(MAGIC)
        return timeline("a", [10, 20, 30]).model_copy(
            update={"content_hash": get_file_hash(path)}
        )

    monkeypatch.setattr(service, "capture_canonical_timeline", capture)
    monkeypatch.setattr(service, "probe_video_artifact", Mock())
    return video, user, paths


def test_preview_and_recovery_preserve_media_approvals_original_pts_and_audit(
    prepared: tuple[VideoFile, User, list[Path]],
) -> None:
    video, user, paths = prepared
    before_name = video.processed_file.name
    assert video.state is not None
    before_state = model_to_dict(video.state)
    preview = service.recover_video_provenance(
        video.pk,
        actor_id=user.pk,
        dry_run=True,
        recording_started_at="2026-10-13T10:00:00+02:00",
        recording_ended_at="2026-10-13T10:40:00+02:00",
    )
    video.refresh_from_db()
    assert "video_provenance_recovery" not in (video.meta or {})
    assert not AuditLedger.objects.filter(
        action="acknowledge_unresolved_provenance"
    ).exists()
    receipt = service.recover_video_provenance(
        video.pk,
        actor_id=user.pk,
        dry_run=False,
        recording_started_at="2026-10-13T10:00:00+02:00",
        recording_ended_at="2026-10-13T10:40:00+02:00",
    )
    assert receipt == preview
    assert receipt.original_timeline is not None
    assert receipt.original_timeline.presentation_timestamps == [10, 20, 30]
    assert receipt.electrical_matching_eligible is False
    video.refresh_from_db()
    assert video.processed_file.name == before_name
    assert Path(video.processed_file.path).read_bytes().startswith(MAGIC)
    assert model_to_dict(video.state) == before_state
    assert (
        AuditLedger.objects.get(action="acknowledge_unresolved_provenance").user == user
    )
    assert all(not path.exists() for path in paths)
    parsed = TransferProvenancePayload.model_validate_json(
        TransferProvenancePayload(video_provenance_recovery=receipt).model_dump_json()
    )
    assert parsed.video_provenance_recovery == receipt


def test_repeated_acknowledgment_is_noop_but_changed_evidence_is_audited(
    prepared: tuple[VideoFile, User, list[Path]],
) -> None:
    video, user, _ = prepared
    first = service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=False)
    video.refresh_from_db()
    modified = video.date_modified
    repeated = service.recover_video_provenance(
        video.pk, actor_id=user.pk, dry_run=False
    )
    video.refresh_from_db()
    assert repeated == first
    assert video.date_modified == modified
    assert (
        AuditLedger.objects.filter(action="acknowledge_unresolved_provenance").count()
        == 1
    )
    changed = service.recover_video_provenance(
        video.pk,
        actor_id=user.pk,
        dry_run=False,
        recording_started_at="2026-10-13T10:00:00Z",
    )
    assert changed != first
    assert (
        AuditLedger.objects.filter(action="acknowledge_unresolved_provenance").count()
        == 2
    )


def test_no_raw_source_is_explicitly_unresolved_not_invented(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, user, _ = prepared
    video.raw_file.name = ""
    video.save(update_fields=["raw_file"])
    receipt = service.recover_video_provenance(
        video.pk, actor_id=user.pk, dry_run=False
    )
    assert receipt.original_timeline is None
    monkeypatch.setattr(exchange, "export_video_provenance", Mock(return_value=receipt))
    with pytest.raises(ValueError, match="frame mapping is unresolved"):
        exchange.map_video_to_electrical_samples(
            video, samples=Mock(), max_error_seconds=Fraction(1)
        )


def test_recovery_cannot_relabel_changed_bytes_or_overwrite_verified_mapping(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, user, _ = prepared
    video.processed_file.save("changed.mp4", ContentFile(b"changed"))
    with pytest.raises(ValueError, match="hash does not match"):
        service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=False)
    monkeypatch.setattr(service, "provenance_chain", Mock(return_value=object()))
    with pytest.raises(ValueError, match="already exists"):
        service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=False)
    assert not AuditLedger.objects.filter(
        action="acknowledge_unresolved_provenance"
    ).exists()


def test_any_authenticated_center_user_can_recover_but_other_centers_cannot(
    prepared: tuple[VideoFile, User, list[Path]],
) -> None:
    video, user, _ = prepared
    client = APIClient()
    url = f"/api/media/videos/{video.pk}/provenance/recover/"
    payload = {"acknowledge_unresolved": True, "dry_run": True}
    assert client.post(url, payload, format="json").status_code in (401, 403)
    client.force_authenticate(user=user)
    assert client.post(url, payload, format="json").status_code == 200
    assert (
        client.post(
            url, {**payload, "acknowledge_unresolved": False}, format="json"
        ).status_code
        == 400
    )
    PortalUserInfo.objects.get(user=user).centers.clear()
    assert client.post(url, payload, format="json").status_code == 403


@pytest.mark.parametrize("blocker", ["lease", "pending", "running", "integrity"])
def test_recovery_returns_specific_blocker_without_mutating_media_or_audit(
    prepared: tuple[VideoFile, User, list[Path]], blocker: str
) -> None:
    video, user, paths = prepared
    expected_code = "active_processing"
    if blocker == "lease":
        create_video_stream_lease(video, file_type="processed")
        expected_code = "active_media_operation"
    elif blocker == "integrity":
        video.meta = {**(video.meta or {}), "integrity_status": "lost"}
        video.save(update_fields=["meta"])
        expected_code = "recovery_precondition_failed"
    else:
        VideoProcessingHistory.objects.create(
            video=video, operation="reprocessing", status=blocker
        )
    before_meta = video.meta
    before_state = model_to_dict(video.state)
    before_bytes = Path(video.processed_file.path).read_bytes()
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        f"/api/media/videos/{video.pk}/provenance/recover/",
        {"acknowledge_unresolved": True, "dry_run": False},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == expected_code
    assert response.json()["error"]
    video.refresh_from_db()
    assert video.meta == before_meta
    assert model_to_dict(video.state) == before_state
    assert Path(video.processed_file.path).read_bytes() == before_bytes
    assert not paths
    assert not AuditLedger.objects.filter(
        action="acknowledge_unresolved_provenance"
    ).exists()


@pytest.mark.parametrize(
    "start,end",
    [("2026-10-13T10:00:00", None), ("2026-10-13T10:00:00Z", "2026-10-13T09:00:00Z")],
)
def test_recovery_times_require_timezone_and_order(start: str, end: str | None) -> None:
    with pytest.raises(ValidationError):
        VideoProvenanceRecovery(
            recording_hash="a" * 64,
            processed_timeline=timeline("b", [0, 1]),
            recording_started_at=start,
            recording_ended_at=end,
        )


@pytest.mark.parametrize(
    "start,end",
    [
        ("invalid", None),
        ("2026-10-13T10:00:00", None),
        ("2026-10-13T10:00:00Z", "2026-10-13T09:00:00Z"),
    ],
)
def test_invalid_recording_times_rejected_before_media_io(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
    start: str,
    end: str | None,
) -> None:
    video, user, paths = prepared
    read = Mock(side_effect=AssertionError("Invalid dates must not read media"))
    monkeypatch.setattr(service, "get_file_hash", read)
    monkeypatch.setattr(service, "ensure_local_file", read)
    with pytest.raises(ValidationError):
        service.recover_video_provenance(
            video.pk,
            actor_id=user.pk,
            dry_run=False,
            recording_started_at=start,
            recording_ended_at=end,
        )
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        f"/api/media/videos/{video.pk}/provenance/recover/",
        {
            "acknowledge_unresolved": True,
            "dry_run": False,
            "recording_started_at": start,
            "recording_ended_at": end,
        },
        format="json",
    )
    assert response.status_code == 400
    read.assert_not_called()
    assert not paths
    assert not AuditLedger.objects.filter(
        action="acknowledge_unresolved_provenance"
    ).exists()


def test_recording_time_conflicting_with_saved_end_rejected_before_media_io(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, user, _ = prepared
    service.recover_video_provenance(
        video.pk,
        actor_id=user.pk,
        dry_run=False,
        recording_ended_at="2026-10-13T10:00:00Z",
    )
    read = Mock(side_effect=AssertionError("Invalid merged dates must not read media"))
    monkeypatch.setattr(service, "get_file_hash", read)
    monkeypatch.setattr(service, "ensure_local_file", read)
    with pytest.raises(ValidationError):
        service.recover_video_provenance(
            video.pk,
            actor_id=user.pk,
            dry_run=False,
            recording_started_at="2026-10-13T11:00:00Z",
        )
    read.assert_not_called()
    assert (
        AuditLedger.objects.filter(action="acknowledge_unresolved_provenance").count()
        == 1
    )


def test_receiver_checks_actual_timeline_and_rejects_stale_receipt(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, user, _ = prepared
    receipt = service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=True)
    exchange.accept_video_provenance_recovery(video, receipt.model_dump(mode="json"))
    video.save(update_fields=["meta"])
    monkeypatch.setattr(
        exchange,
        "provenance_chain",
        Mock(side_effect=AssertionError("must not infer mapping")),
    )
    monkeypatch.setattr(
        "endoreg_db.services.media.export_downloads.validate_video_download", Mock()
    )
    assert exchange.export_video_provenance(video) == receipt
    video.processed_video_hash = "f" * 64
    with pytest.raises(ValueError, match="different media"):
        exchange.accept_video_provenance_recovery(video, receipt)


def test_unresolved_recovery_does_not_bypass_clinical_export_gates(
    prepared: tuple[VideoFile, User, list[Path]],
) -> None:
    video, user, _ = prepared
    service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=False)
    video.refresh_from_db()
    with pytest.raises(ValueError):
        exchange.export_video_provenance(video)


def test_receiver_rejects_changed_presentation_timestamps(
    prepared: tuple[VideoFile, User, list[Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, user, _ = prepared
    receipt = service.recover_video_provenance(video.pk, actor_id=user.pk, dry_run=True)
    exchange.accept_video_provenance_recovery(video, receipt)
    capture = Mock(return_value=receipt.processed_timeline)
    monkeypatch.setattr(
        "endoreg_db.services.video_storage.canonical_timelines.capture_canonical_timeline",
        capture,
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_storage.probes.probe_video_artifact", Mock()
    )
    exchange.validate_received_video_timeline(video)
    capture.return_value = receipt.processed_timeline.model_copy(
        update={"presentation_timestamps": [10, 20, 31]}
    )
    with pytest.raises(ValueError, match="timestamps disagree"):
        exchange.validate_received_video_timeline(video)
