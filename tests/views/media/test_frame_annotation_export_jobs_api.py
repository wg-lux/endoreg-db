"""HTTP contract of browser-started frame annotation export jobs."""

from __future__ import annotations

from typing import Any
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import Center, FrameAnnotationExportJob, PortalUserInfo

JOBS_URL = "/api/media/videos/export-annotated/jobs/"
DISPATCH = "endoreg_db.services.media.frame_annotation_export_jobs._dispatch"


class FrameAnnotationExportJobApiTests(TestCase):
    def setUp(self) -> None:
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(
            name=f"api-export-{suffix}", center_key=f"api-export-{suffix}"
        )
        self.foreign_center = Center.objects.create(
            name=f"api-foreign-{suffix}", center_key=f"api-foreign-{suffix}"
        )
        self.user = User.objects.create_user(username=f"api-export-user-{suffix}")
        PortalUserInfo.objects.create(user=self.user).centers.add(self.center)
        self.client.force_login(self.user)

    def _payload(self, **overrides: object) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "output_dir": f"run_{uuid4().hex[:12]}",
            "output_path": "annotations.csv",
            "export_profile": "pts_dataset_v1",
            "center_key": self.center.center_key,
            "limit": None,
        }
        payload.update(overrides)
        return payload

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.client.post(
            JOBS_URL + path, data=payload, content_type="application/json"
        )

    def test_validate_returns_normalized_config_without_persisting(self) -> None:
        payload = self._payload()

        response = self._post("validate/", payload)

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["output_dir"] == payload["output_dir"]
        assert body["center_key"] == self.center.center_key
        assert body["config"]["export_profile"] == "pts_dataset_v1"
        assert "limit" not in body["config"]
        assert not FrameAnnotationExportJob.objects.exists()

    def test_invalid_payloads_are_rejected_with_400(self) -> None:
        invalid_payloads: tuple[dict[str, object], ...] = (
            {"unexpected": True},
            {"unexpected": None},
            {"only_validated": None},
            {"load_base_data": None},
            {"export_frames": None},
            {"transcode_fps": None},
            {"output_dir": "nested/run"},
            {"load_base_data": True},
            {"center_key": None},
            {"limit": -1},
            {"limit": {}},
            {"limit": []},
        )
        for overrides in invalid_payloads:
            with self.subTest(overrides=overrides):
                response = self._post("validate/", self._payload(**overrides))
                assert response.status_code == 400, response.content
                assert "errors" in response.json()

    def test_start_rejects_invalid_nulls_without_persisting(self) -> None:
        invalid_payloads: tuple[dict[str, object], ...] = (
            {"unexpected": None},
            {"only_validated": None},
            {"limit": {}},
            {"limit": []},
        )
        for overrides in invalid_payloads:
            with self.subTest(overrides=overrides), patch(DISPATCH) as dispatch:
                response = self._post("start/", self._payload(**overrides))
                assert response.status_code == 400, response.content
                dispatch.assert_not_called()
        assert not FrameAnnotationExportJob.objects.exists()

    def test_scope_outside_user_centers_is_forbidden(self) -> None:
        for overrides in (
            {"center_key": self.foreign_center.center_key},
            {"center_key": None, "all_centers": True},
        ):
            with self.subTest(overrides=overrides):
                response = self._post("start/", self._payload(**overrides))
                assert response.status_code == 403, response.content
        assert not FrameAnnotationExportJob.objects.exists()

    def test_start_queues_job_and_rejects_active_duplicate(self) -> None:
        payload = self._payload()
        with patch(DISPATCH) as dispatch, self.captureOnCommitCallbacks(execute=True):
            response = self._post("start/", payload)

        assert response.status_code == 202, response.content
        body = response.json()
        assert body["status"] == "queued"
        assert body["center_key"] == self.center.center_key
        assert body["requested_by"] == self.user.get_username()
        dispatch.assert_called_once()

        with patch(DISPATCH):
            conflict = self._post("start/", payload)
        assert conflict.status_code == 409, conflict.content

    def test_list_and_detail_hide_jobs_outside_user_centers(self) -> None:
        admin = User.objects.create_user(
            username=f"api-export-admin-{uuid4().hex[:8]}", is_staff=True
        )
        own = FrameAnnotationExportJob.objects.create(
            requested_by=self.user,
            center=self.center,
            output_dir=f"run_{uuid4().hex[:8]}",
            config={"output_path": "a.csv", "center_key": self.center.center_key},
        )
        foreign = FrameAnnotationExportJob.objects.create(
            requested_by=admin,
            center=self.foreign_center,
            output_dir=f"run_{uuid4().hex[:8]}",
            config={"output_path": "a.csv"},
        )

        listed = self.client.get(JOBS_URL)
        assert listed.status_code == 200, listed.content
        body = listed.json()
        ids = {job["id"] for job in body["results"]}
        assert str(own.pk) in ids and str(foreign.pk) not in ids
        assert body["center_options"] == [
            {"center_key": self.center.center_key, "name": self.center.name}
        ]
        assert body["all_centers_allowed"] is False

        assert self.client.get(f"{JOBS_URL}{own.pk}/").status_code == 200
        assert self.client.get(f"{JOBS_URL}{foreign.pk}/").status_code == 404

    def test_anonymous_requests_cannot_start_exports(self) -> None:
        self.client.logout()

        response = self._post("start/", self._payload())

        assert response.status_code in {401, 403}, response.content
        assert not FrameAnnotationExportJob.objects.exists()

    def test_detail_poll_marks_a_worker_without_heartbeat_lost(self) -> None:
        job = FrameAnnotationExportJob.objects.create(
            requested_by=self.user,
            center=self.center,
            output_dir=f"run_{uuid4().hex[:8]}",
            config=self._payload(),
            status=FrameAnnotationExportJob.Status.RUNNING,
            heartbeat_at=timezone.now() - timedelta(hours=1),
        )
        response = self.client.get(f"{JOBS_URL}{job.pk}/")
        assert response.status_code == 200, response.content
        assert response.json()["status"] == "lost"
