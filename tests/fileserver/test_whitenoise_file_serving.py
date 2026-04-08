from django.test import TestCase

from tests.media.video.helper import get_random_video_path_by_examination_alias

from ..helpers.data_loader import load_base_db_data
from ..helpers.default_objects import (
    get_default_center,
    get_default_egd_pdf,
    get_default_video_file,
)


class WhiteNoiseFileServingTest(TestCase):
    def setUp(self):
        load_base_db_data()

        # Use the video test helper to get a real video file path and create a VideoFile instance
        self.video_path = get_random_video_path_by_examination_alias(
            examination_alias="egd", is_anonymous=False
        )
        self.center = get_default_center()
        # Create a VideoFile instance if not already present
        self.video_file = get_default_video_file()
        self.video_url = self.video_file.active_file_url

        self.assertIsNotNone(self.video_file, "VideoFile creation failed.")
        self.assertTrue(
            self.video_file.active_file_path.exists(),
            f"Video file {self.video_file.active_file_path} does not exist.",
        )
        self.pdf_file = get_default_egd_pdf()
        self.assertIsNotNone(self.pdf_file, "report file creation failed.")
        self.pdf_url = self.pdf_file.file_url

    def tearDown(self):
        # Clean up the created VideoFile and its file
        if self.video_file and self.video_file.pk:
            self.video_file.delete_with_file()

    def test_video_file_accessible_via_url(self):
        self.assertIsNotNone(self.video_url)
        self.assertTrue(self.video_url.startswith("/api/media/videos/"))
        self.assertFalse(self.video_url.startswith("/protected_media/"))
        self.assertFalse(self.video_url.startswith("/media/"))

    def test_pdf_file_accessible_via_url(self):
        self.assertIsNotNone(self.pdf_url)
        self.assertTrue(self.pdf_url.startswith("/api/media/pdfs/"))
        self.assertFalse(self.pdf_url.startswith("/protected_media/"))
        self.assertFalse(self.pdf_url.startswith("/media/"))
