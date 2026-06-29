from __future__ import annotations

from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.test import TestCase
from endoreg_db.helpers.typing import m2m_add_relation
from endoreg_db.models import Center, VideoFile


class VideoMediaViewTests(TestCase):
    center: Center
    user: User
    video: VideoFile

    def setUp(self) -> None:
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"video-media-center-{suffix}")
        self.user = User.objects.create_user(username=f"video-media-user-{suffix}")
        video_read_group = Group.objects.create(name="video:read")
        m2m_add_relation(self.user.groups).add(video_read_group)  # type: ignore
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"video-media-{uuid4().hex}",
            original_file_name="video-media.mp4",
        )

    def test_list_videos_returns_results(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/api/media/videos/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["count"] == 1
        assert payload["results"][0]["id"] == self.video.pk
        assert payload["results"][0]["original_file_name"] == "video-media.mp4"
