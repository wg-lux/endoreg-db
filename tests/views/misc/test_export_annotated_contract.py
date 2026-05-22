from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import override_settings

from endoreg_db.models import Center, VideoFile, VideoState
from endoreg_db.utils.filesystem.file_operations import atomic_write_file


class ExportAnnotatedContractTests(TestCase):
    def test_api_default_export_without_config_is_safe(self):
        captured = {}

        class FakeClient:
            def run_export(self, config):
                captured["config"] = config
                return SimpleNamespace(
                    success=True,
                    output_path=Path("frames.csv"),
                    row_count=0,
                    exported_video_count=0,
                    exported_frame_count=0,
                    video_output_dir=None,
                    frame_output_dir=None,
                )

        with patch(
            "endoreg_db.views.video.export_annotated.annotation_exporter_client",
            return_value=FakeClient(),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data={"output_dir": "data/export"},
                content_type="application/json",
            )

        assert response.status_code == 200, response.content
        config = captured["config"]
        assert config.export_frames is True
        assert config.export_videos is False
        assert config.use_export_flags is True

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_config_file_scope_is_checked_after_loading(self):
        user = User.objects.create_user(username="export-user")
        self.client.force_login(user)

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "export.yaml"
            atomic_write_file(
                destination=config_path,
                content=[
                    b"output_path: frames.csv\n"
                    b"all_centers: true\n"
                    b"export_videos: true\n"
                    b"only_validated: false\n"
                ],
            )

            with patch(
                "endoreg_db.views.video.export_annotated.annotation_exporter_client",
            ) as client_factory:
                response = self.client.post(
                    "/api/media/videos/export-annotated/",
                    data={"config_path": str(config_path)},
                    content_type="application/json",
                )

        assert response.status_code == 403, response.content
        client_factory.assert_not_called()
        assert "all_centers" in response.json()["error"]

    def test_api_boolean_strings_are_normalized_before_export_config(self):
        captured = {}

        class FakeClient:
            def run_export(self, config):
                captured["config"] = config
                return SimpleNamespace(
                    success=True,
                    output_path=Path("frames.csv"),
                    row_count=0,
                    exported_video_count=0,
                    exported_frame_count=0,
                    video_output_dir=None,
                    frame_output_dir=None,
                )

        with patch(
            "endoreg_db.views.video.export_annotated.annotation_exporter_client",
            return_value=FakeClient(),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data={
                    "output_dir": "data/export",
                    "all_centers": "false",
                    "export_videos": "false",
                    "only_validated": "true",
                    "transcode_frames": "false",
                },
                content_type="application/json",
            )

        assert response.status_code == 200, response.content
        config = captured["config"]
        assert config.all_centers is False
        assert config.export_videos is False
        assert config.only_validated is True
        assert config.transcode_frames is False

    def test_video_specific_export_rejects_non_final_segment_cleanup(self):
        center = Center.objects.create(name="Export Segment Cleanup Center")
        state = VideoState.objects.create(
            anonymization_validated=True,
            outside_segments_removed=True,
            segment_annotations_created=True,
            segment_annotations_validated=False,
        )
        video = VideoFile.objects.create(
            center=center,
            state=state,
            video_hash="export-segment-cleanup-video",
            original_file_name="export-segment-cleanup.mp4",
        )

        with patch(
            "endoreg_db.views.video.export_annotated.annotation_exporter_client",
        ) as client_factory:
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data={
                    "output_dir": "data/export",
                    "video_id": video.pk,
                    "use_export_flags": False,
                },
                content_type="application/json",
            )

        assert response.status_code == 409, response.content
        client_factory.assert_not_called()
        assert "cleanup_required" in response.json()["error"]
