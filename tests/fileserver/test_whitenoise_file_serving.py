from django.test import LiveServerTestCase

from tests.media.video.helper import get_random_video_path_by_examination_alias
from ..helpers.data_loader import load_data
from endoreg_db.models import VideoFile
from ..helpers.default_objects import get_default_center, get_default_egd_pdf
import requests


class WhiteNoiseFileServingTest(LiveServerTestCase):
    def setUp(self):
        load_data()

        # Use the video test helper to get a real video file path and create a VideoFile instance
        self.video_path = get_random_video_path_by_examination_alias(examination_alias="egd", is_anonymous=False)
        self.center = get_default_center()
        # Create a VideoFile instance if not already present
        self.video_file = VideoFile.create_from_file(self.video_path, center_name=self.center.name, delete_source=False)

        self.video_url = self.video_file.active_file_url

        self.assertIsNotNone(self.video_file, "VideoFile creation failed.")
        self.assertTrue(self.video_file.active_file_path.exists(), f"Video file {self.video_file.active_file_path} does not exist.")
        self.pdf_file = get_default_egd_pdf()
        self.assertIsNotNone(self.pdf_file, "PDF file creation failed.")
        self.pdf_url = self.pdf_file.file_url

    def tearDown(self):
        # Clean up the created VideoFile and its file
        if self.video_file and self.video_file.pk:
            self.video_file.delete_with_file()

    def test_video_file_accessible_via_url(self):
        # Use the live server's URL, not a hardcoded one
        full_url = self.live_server_url + self.video_url  # self.url should be the relative media path, e.g. '/media/videos/uuid.mp4'
        print(f"DEBUG: Testing full URL: {full_url}")
        response = requests.get(full_url)
        print(f"DEBUG: Response status code: {response.status_code}")
        print(f"DEBUG: Response content-type: {response.headers.get('Content-Type')}")
        print(f"DEBUG: Response content length: {len(response.content)}")
        self.assertEqual(response.status_code, 200)
        # Optionally, check content type or partial content

    def test_pdf_file_accessible_via_url(self):
        full_url = self.live_server_url + self.pdf_url
        print(f"DEBUG: Testing full URL for PDF: {full_url}")
        response = requests.get(full_url)
        print(f"DEBUG: Response status code for PDF: {response.status_code}")
        print(f"DEBUG: Response content-type for PDF: {response.headers.get('Content-Type')}")
        print(f"DEBUG: Response content length for PDF: {len(response.content)}")
        self.assertEqual(response.status_code, 200)
