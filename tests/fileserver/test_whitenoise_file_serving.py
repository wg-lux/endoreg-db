from django.test import LiveServerTestCase

from tests.media.video.helper import get_random_video_path_by_examination_alias
from ..helpers.data_loader import load_base_db_data
from endoreg_db.models import VideoFile
from ..helpers.default_objects import get_default_center, get_default_egd_pdf, get_default_video_file
import requests


class WhiteNoiseFileServingTest(LiveServerTestCase):
    def setUp(self):
        """
        Prepare test fixtures and required media files for the test case.
        
        Loads base database data and ensures a real video and PDF are available for tests. After setup the instance has:
        - video_path: filesystem path to a sample video used for creating or validating a VideoFile
        - center: the default center object
        - video_file: a VideoFile instance
        - video_url: the active URL for the VideoFile
        - pdf_file: a PDF file object
        - pdf_url: the URL for the PDF file
        
        This method also asserts that the VideoFile and PDF objects were created and that the video file exists on disk.
        """
        load_base_db_data()

        # Use the video test helper to get a real video file path and create a VideoFile instance
        self.video_path = get_random_video_path_by_examination_alias(examination_alias="egd", is_anonymous=False)
        self.center = get_default_center()
        # Create a VideoFile instance if not already present
        self.video_file = get_default_video_file()
        self.video_url = self.video_file.active_file_url

        self.assertIsNotNone(self.video_file, "VideoFile creation failed.")
        self.assertTrue(self.video_file.active_file_path.exists(), f"Video file {self.video_file.active_file_path} does not exist.")
        self.pdf_file = get_default_egd_pdf()
        self.assertIsNotNone(self.pdf_file, "PDF file creation failed.")
        self.pdf_url = self.pdf_file.file_url

    def tearDown(self):
        # Clean up the created VideoFile and its file
        """
        Remove the test-created VideoFile and its associated stored file if present.
        
        This tearDown hook deletes the VideoFile instance created during the test and also removes the underlying file from storage when the VideoFile has a primary key.
        """
        if self.video_file and self.video_file.pk:
            self.video_file.delete_with_file()

    def test_video_file_accessible_via_url(self):
        # Use the live server's URL, not a hardcoded one
        full_url = self.live_server_url + self.video_url  # self.url should be the relative media path, e.g. '/media/videos/uuid.mp4'
        print(f"DEBUG: Testing full URL: {full_url}")
        response = requests.head(full_url)
        print(f"DEBUG: Response status code: {response.status_code}")
        print(f"DEBUG: Response content-type: {response.headers.get('Content-Type')}")
        self.assertEqual(response.status_code, 200)
        # Optionally, check content type or partial content

    def test_pdf_file_accessible_via_url(self):
        """
        Verify that the PDF file is reachable through the live server URL.
        
        Performs an HTTP HEAD request to the PDF's full URL and asserts the response status code is 200.
        """
        if self.pdf_url is None:
            self.fail("PDF file URL is None.")
        full_url = self.live_server_url + self.pdf_url
        print(f"DEBUG: Testing full URL for PDF: {full_url}")
        response = requests.head(full_url)
        print(f"DEBUG: Response status code for PDF: {response.status_code}")
        print(f"DEBUG: Response content-type for PDF: {response.headers.get('Content-Type')}")
        self.assertEqual(response.status_code, 200)