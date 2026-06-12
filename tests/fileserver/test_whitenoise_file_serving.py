from __future__ import annotations

from django.test import TestCase

from tests.media.video.helper import get_random_video_path_by_examination_alias

from ..helpers.data_loader import load_base_db_data
from ..helpers.default_objects import (
    get_default_center,
    get_default_egd_pdf,
    get_default_video_file,
)


class WhiteNoiseFileServingTest(TestCase):
    video_url: str
    pdf_url: str

    def setUp(self) -> None:
        load_base_db_data()

        self.video_path = get_random_video_path_by_examination_alias(
            examination_alias="egd", is_anonymous=False
        )
        self.center = get_default_center()
        self.video_file = get_default_video_file()

        video_url = self.video_file.active_file_url
        if not isinstance(video_url, str):
            raise AssertionError("Video URL should be available as a string.")
        self.video_url = video_url

        self.assertIsNotNone(self.video_file, "VideoFile creation failed.")
        with self.video_file.ensure_local_raw_file() as local_path:
            self.assertTrue(
                local_path.exists(),
                f"Video file {self.video_file.raw_file.name} does not exist.",
            )

        self.pdf_file = get_default_egd_pdf()
        self.assertIsNotNone(self.pdf_file, "report file creation failed.")

        pdf_url = self.pdf_file.file_url
        if not isinstance(pdf_url, str):
            raise AssertionError("PDF URL should be available as a string.")
        self.pdf_url = pdf_url

    def tearDown(self) -> None:
        if self.video_file and self.video_file.pk:
            self.video_file.delete_with_file()

    def test_video_file_accessible_via_url(self) -> None:
        self.assertTrue(self.video_url.startswith("/api/media/videos/"))
        self.assertFalse(self.video_url.startswith("/protected_media/"))
        self.assertFalse(self.video_url.startswith("/media/"))

    def test_pdf_file_accessible_via_url(self) -> None:
        self.assertTrue(self.pdf_url.startswith("/api/media/pdfs/"))
        self.assertFalse(self.pdf_url.startswith("/protected_media/"))
        self.assertFalse(self.pdf_url.startswith("/media/"))
