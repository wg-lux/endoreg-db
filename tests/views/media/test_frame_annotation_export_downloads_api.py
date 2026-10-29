"""HTTP contract for copying completed export runs to the user's machine."""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterable
from typing import cast
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import User
from django.http import StreamingHttpResponse
from django.test import TestCase

from endoreg_db.models import Center, FrameAnnotationExportJob, PortalUserInfo
from endoreg_db.services.media.frame_annotation_export_jobs import (
    frame_annotation_export_root,
)

JOBS_URL = "/api/media/videos/export-annotated/jobs/"


def _body(response: object) -> bytes:
    streaming_response = cast(StreamingHttpResponse, response)
    return b"".join(cast(Iterable[bytes], streaming_response.streaming_content))


class FrameAnnotationExportDownloadApiTests(TestCase):
    def setUp(self) -> None:
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(
            name=f"api-download-{suffix}", center_key=f"api-download-{suffix}"
        )
        self.user = User.objects.create_user(username=f"api-download-user-{suffix}")
        PortalUserInfo.objects.create(user=self.user).centers.add(self.center)
        self.client.force_login(self.user)
        self.run_name = f"run_{uuid4().hex[:12]}"
        run_dir = frame_annotation_export_root() / self.run_name
        (run_dir / "frames").mkdir(parents=True)
        (run_dir / "annotations.csv").write_text("frame,label\n")
        (run_dir / "frames" / "frame_1.jpg").write_bytes(b"jpeg")
        self.job = FrameAnnotationExportJob.objects.create(
            requested_by=self.user,
            center=self.center,
            output_dir=self.run_name,
            config={
                "output_dir": self.run_name,
                "output_path": "annotations.csv",
                "center_key": self.center.center_key,
            },
        )
        FrameAnnotationExportJob.objects.filter(pk=self.job.pk).update(
            status=FrameAnnotationExportJob.Status.COMPLETED
        )

    def _url(self, suffix: str) -> str:
        return f"{JOBS_URL}{self.job.pk}/{suffix}"

    def test_lists_run_files(self) -> None:
        response = self.client.get(self._url("files/"))

        assert response.status_code == 200, response.content
        assert response.json() == {
            "job_id": str(self.job.pk),
            "output_dir": self.run_name,
            "files": [
                {"path": "annotations.csv", "size": 12},
                {"path": "frames/frame_1.jpg", "size": 4},
            ],
        }
        assert response["Cache-Control"] == "private, no-store"

    def test_streams_one_nested_file(self) -> None:
        response = self.client.get(self._url("files/frames/frame_1.jpg"))

        assert response.status_code == 200
        assert _body(response) == b"jpeg"
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "private, no-store"

    def test_nginx_offload_hands_file_to_protected_location(self) -> None:
        with mock.patch.dict(os.environ, {"SERVE_WITH_NGINX": "true"}):
            response = self.client.get(self._url("files/frames/frame_1.jpg"))

        assert response.status_code == 200
        assert response.content == b""
        assert response["X-Accel-Redirect"] == (
            f"/protected_media/frame_annotation_exports/{self.run_name}"
            "/frames/frame_1.jpg"
        )
        assert response["Content-Type"] == "image/jpeg"
        assert response["Content-Disposition"] == ('attachment; filename="frame_1.jpg"')
        assert response["Cache-Control"] == "private, no-store"

    def test_streams_archive(self) -> None:
        response = self.client.get(self._url("archive.zip"))

        assert response.status_code == 200
        assert response["Content-Disposition"] == (
            f'attachment; filename="{self.run_name}.zip"'
        )
        with zipfile.ZipFile(io.BytesIO(_body(response))) as zf:
            assert zf.namelist() == [
                f"{self.run_name}/annotations.csv",
                f"{self.run_name}/frames/frame_1.jpg",
            ]

    def test_rejects_unknown_traversing_and_missing_files(self) -> None:
        for suffix, expected in (
            ("files/missing.csv", 404),
            ("files/frames", 404),
            ("files/frames/../annotations.csv", 400),
        ):
            with self.subTest(suffix=suffix):
                assert self.client.get(self._url(suffix)).status_code == expected

    def test_unfinished_job_conflicts(self) -> None:
        FrameAnnotationExportJob.objects.filter(pk=self.job.pk).update(
            status=FrameAnnotationExportJob.Status.RUNNING
        )
        for suffix in ("files/", "files/annotations.csv", "archive.zip"):
            with self.subTest(suffix=suffix):
                assert self.client.get(self._url(suffix)).status_code == 409

    def test_foreign_center_user_gets_not_found(self) -> None:
        other = User.objects.create_user(username=f"api-other-{uuid4().hex[:8]}")
        other_center = Center.objects.create(
            name=f"api-other-{uuid4().hex[:8]}", center_key=f"api-o-{uuid4().hex[:8]}"
        )
        PortalUserInfo.objects.create(user=other).centers.add(other_center)
        self.client.force_login(other)

        for suffix in ("files/", "files/annotations.csv", "archive.zip"):
            with self.subTest(suffix=suffix):
                assert self.client.get(self._url(suffix)).status_code == 404
