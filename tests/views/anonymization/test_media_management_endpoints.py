from __future__ import annotations

from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import Center, VideoFile


class MediaManagementEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"mm-center-{suffix}")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"mm-video-{uuid4().hex}",
            original_file_name="mm-video.mp4",
        )

    def test_media_management_status_endpoint(self):
        response = self.client.get("/api/media-management/status/")
        assert response.status_code == 200, response.content
        payload = response.json()
        assert "videos" in payload
        assert "pdfs" in payload
        assert "cleanup_opportunities" in payload
        assert "total_files" in payload

    def test_media_management_cleanup_dry_run_endpoint(self):
        response = self.client.delete(
            "/api/media-management/cleanup/?type=unfinished&force=false"
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert "summary" in payload
        assert payload["summary"]["dry_run"] is True

    def test_media_management_force_remove_missing_file(self):
        response = self.client.delete("/api/media-management/force-remove/999999/")
        assert response.status_code == 404, response.content
        assert response.json()["detail"] == "File not found"

    def test_media_management_reset_status_for_video(self):
        response = self.client.post(
            f"/api/media-management/reset-status/{self.video.pk}/"
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["file_type"] == "video"
        assert payload["file_id"] == self.video.pk
        assert payload["new_status"] == "not_started"
