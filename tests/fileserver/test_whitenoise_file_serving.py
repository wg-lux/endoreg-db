import os
from django.test import LiveServerTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.urls import reverse
from tests.media.video.helper import get_random_video_path_by_examination_alias
from ..helpers.data_loader import load_data
from endoreg_db.models import VideoFile
from ..helpers.default_objects import get_default_center
import requests

class WhiteNoiseFileServingTest(LiveServerTestCase):
    def setUp(self):
        load_data()

        # Use the video test helper to get a real video file path and create a VideoFile instance
        self.video_path = get_random_video_path_by_examination_alias(examination_alias='egd', is_anonymous=False)
        self.center = get_default_center()
        # Create a VideoFile instance if not already present
        self.video_file = VideoFile.create_from_file(
            self.video_path,
            center_name=self.center.name,
            delete_source=False
        )
        self.assertIsNotNone(self.video_file, "VideoFile creation failed.")
        self.assertTrue(self.video_file.active_file_path.exists(), f"Video file {self.video_file.active_file_path} does not exist.")
        self.relative_path = self.video_file.active_file_path.relative_to(settings.MEDIA_ROOT)
        self.url = settings.MEDIA_URL + str(self.relative_path)
        print(f"DEBUG: File should exist at: {self.video_file.active_file_path}")
        print(f"DEBUG: Requesting URL: {self.url}")

    def tearDown(self):
        # Clean up the created VideoFile and its file
        if self.video_file and self.video_file.pk:
            self.video_file.delete_with_file()

    def test_video_file_accessible_via_url(self):
        # Use requests to make a real HTTP request to the live server
        full_url = self.live_server_url + self.url
        print(f"DEBUG: Testing full URL: {full_url}")
        response = requests.get(full_url)
        print(f"DEBUG: Response status code: {response.status_code}")
        print(f"DEBUG: Response content-type: {response.headers.get('Content-Type')}")
        print(f"DEBUG: Response content length: {len(response.content)}")
        self.assertEqual(response.status_code, 200)
        # Optionally, check content type or partial content
