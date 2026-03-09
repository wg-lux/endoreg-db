from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, VideoFile
from endoreg_db.views.video.video_fps import VideoFpsView


class VideoFpsViewTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = VideoFpsView.as_view()

        self.center = Center.objects.create(name="fps-view-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="fps-view-video-hash",
            original_file_name="fps_view.mp4",
            fps=25.0,
            frame_count=100,
        )

    def test_returns_video_fps(self):
        request = self.factory.get(f"/api/media/videos/{self.video.pk}/fps/")

        response = self.view(request, pk=self.video.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["video_id"], self.video.pk)
        self.assertEqual(response.data["fps"], 25.0)

    def test_uses_get_fps_method(self):
        request = self.factory.get(f"/api/media/videos/{self.video.pk}/fps/")

        with patch(
            "endoreg_db.views.video.video_fps.VideoFile.get_fps",
            return_value=29.97,
        ) as mocked_get_fps:
            response = self.view(request, pk=self.video.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["fps"], 29.97)
        mocked_get_fps.assert_called_once()

    def test_returns_422_when_fps_missing(self):
        request = self.factory.get(f"/api/media/videos/{self.video.pk}/fps/")

        with patch(
            "endoreg_db.views.video.video_fps.VideoFile.get_fps",
            side_effect=ValueError("fps unavailable"),
        ):
            response = self.view(request, pk=self.video.pk)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.data["error"],
            "Could not determine fps for the requested video.",
        )
        self.assertEqual(response.data["details"]["video_id"], self.video.pk)
