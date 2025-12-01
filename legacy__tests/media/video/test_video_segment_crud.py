"""
Pytest-Tests für das LabelVideoSegmentViewSet CRUD-Interface
"""

import os
from typing import cast

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from endoreg_db.models import VideoFile, Label, LabelVideoSegment
from endoreg_db.models.label.label_type import LabelType

from ...helpers.optimized_video_fixtures import get_segment_test_video

SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS


def create_test_video_segment(client, video, label, start_frame_number, end_frame_number) -> Response:
    """
    Hilfsfunktion zum Erstellen eines Test-Video-Segments.
    """
    data = {
        "video_id": video.pk,
        "label": label.pk,
        "start_frame_number": start_frame_number,
        "end_frame_number": end_frame_number,
    }
    
    response = client.post("/api/video-segments/", data, format="json")
    return cast(Response, response)

@pytest.mark.django_db
@pytest.mark.usefixtures("base_db_data")
class TestLabelVideoSegmentCRUD:
    """Test-Suite für vollständige CRUD-Operationen mit LabelVideoSegmentViewSet"""
    
    def setup_method(self):
        """Setup für jeden Test"""
        if SKIP_EXPENSIVE_TESTS or not RUN_VIDEO_TESTS:
            pytest.skip("Segment CRUD tests require RUN_VIDEO_TESTS and SKIP_EXPENSIVE_TESTS=false")

        self.client = APIClient()
        
        # Test-User erstellen (nur für Prod-Umgebung nötig)
        self.user = User.objects.create_user(
            username='testuser', 
            password='testpass'
        )

        self.label_type = LabelType.objects.create(
            name='Test Label Type',
            description='A test label type for video segments'
        )
        
        video = get_segment_test_video()

        if not isinstance(video, VideoFile):
            pytest.skip("Segment CRUD tests require a persisted VideoFile instance")

        self.video = cast(VideoFile, video)
        try:
            self.video.refresh_from_db()
        except Exception:
            pass

        LabelVideoSegment.objects.filter(video_file=self.video).delete()

        fps = getattr(self.video, "fps", None)
        if not fps and hasattr(self.video, "get_fps"):
            fps = self.video.get_fps()
        self.video_fps = float(fps or 0.0)
        assert self.video_fps > 0, "FPS must be greater than 0"
        self.start_frame_number = 10
        self.end_frame_number = self.start_frame_number + self.video_fps
        self.start_time = self.start_frame_number / self.video_fps
        self.end_time = self.end_frame_number / self.video_fps
        # self.video = VideoFile.objects.create(
        #     original_file_name='test_video.mp4',
        #     fps=25.0, 
        #     center=self.center,
        # )
        
        self.label = Label.objects.create(
            name='Polyp',
            description='Test polyp label',
            label_type=self.label_type,
        )
    
    def test_create_segment_success(self):
        """Test: Erfolgreiches Erstellen eines neuen Segments"""
        response = create_test_video_segment(
            self.client,
            self.video,
            self.label,
            int(self.start_frame_number),
            int(self.end_frame_number),
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data is not None
        assert response.data["start_frame_number"] == pytest.approx(self.start_frame_number)
        assert response.data["end_frame_number"] == pytest.approx(self.end_frame_number)

        # Verifiziere, dass das Segment in der DB gespeichert wurde
        segment = LabelVideoSegment.objects.get(id=response.data["id"])
        assert segment.video_file == self.video
        assert segment.label == self.label
    
    def test_list_segments_with_filtering(self):
        """Test: Liste aller Segmente mit optionaler Filterung"""
        # Erstelle Test-Segmente
        _segment1 = create_test_video_segment(
            self.client, 
            self.video, 
            self.label, 
            start_frame_number=0,
            end_frame_number=100
        )
        
        _segment2 = create_test_video_segment(
            self.client, 
            self.video, 
            self.label, 
            start_frame_number=110,
            end_frame_number=200
        )
        
        # Test: Alle Segmente abrufen
        response = cast(Response, self.client.get("/api/video-segments/"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data is not None
        assert len(response.data) == 2
        
        # Test: Filter nach video_id
        response = cast(Response, self.client.get(f"/api/video-segments/?video_id={self.video.pk}"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data is not None
        assert len(response.data) == 2
        
    def test_retrieve_single_segment(self):
        """Test: Einzelnes Segment abrufen"""
        segment_response = create_test_video_segment(
            self.client, 
            self.video, 
            self.label, 
            start_frame_number=self.start_frame_number,
            end_frame_number=self.end_frame_number
        )
        assert segment_response.data is not None
        segment_id = segment_response.data["id"]
        
        response = cast(Response, self.client.get(f"/api/video-segments/{segment_id}/"))
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data is not None
        assert response.data["id"] == segment_id
        assert response.data["start_frame_number"] == self.start_frame_number
    
    def test_update_segment_partial(self):
        """Test: Teilweise Aktualisierung eines Segments (PATCH)"""
        segment_response = create_test_video_segment(
            self.client, 
            self.video, 
            self.label, 
            start_frame_number=self.start_frame_number,
            end_frame_number=self.end_frame_number
        )
        assert segment_response.data is not None
        segment_id = segment_response.data["id"]
        
        # Nur end_frame_number aktualisieren
        data = {"end_frame_number": self.end_frame_number+self.video_fps}
        
        response = cast(Response, self.client.patch(f"/api/video-segments/{segment_id}/", data, format="json"))
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data is not None
        assert response.data["end_frame_number"] == self.end_frame_number+self.video_fps
        assert response.data["start_frame_number"] == self.start_frame_number  # Unverändert
        
        # Verifiziere in der DB
        lvs = LabelVideoSegment.objects.get(id=segment_id)
        assert lvs.end_frame_number == self.end_frame_number + self.video_fps

    
    def test_update_segment_full(self):
        """Test: Vollständige Aktualisierung eines Segments (PUT)"""
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=0,
            end_frame_number=50
        )
        
        data = {
            "video_file": self.video.pk,
            "label": self.label.pk,
            "start_frame_number": 50,
            "end_frame_number": 60,
        }
        
        response = cast(Response, self.client.put(f"/api/video-segments/{segment.pk}/", data, format="json"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data is not None
        assert response.data["start_frame_number"] == 50
        assert response.data["end_frame_number"] == 60
    
    # def test_delete_segment(self):
    #     """Test: Segment löschen"""
    #     segment = LabelVideoSegment.objects.create(
    #         video_file=self.video,
    #         label=self.label,
    #         start_frame_number=100,
    #         end_frame_number=150
    #     )
        
    #     segment_id = segment.id
        
    #     response = self.client.delete(f"/api/video-segments/{segment_id}/")
        
    #     assert response.status_code == status.HTTP_204_NO_CONTENT
        
    #     # Verifiziere, dass das Segment gelöscht wurde
    #     assert not LabelVideoSegment.objects.filter(id=segment_id).exists()
    
    def test_create_segment_validation_error(self):
        """Test: Fehlerbehandlung bei ungültigen Daten"""
        # Fehlendes video_file
        data = {
            "label": self.label.pk,
            "start_frame_number": 100,
            "end_frame_number": 50  # Ende vor Start - sollte Fehler verursachen
        }
        response = cast(Response, self.client.post("/api/video-segments/", data, format="json"))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data is not None
        # Accept DRF error dict inside 'details' key
        assert "non_field_errors" in response.data.get("details", {})
        # Optionally check error message
        error_msgs = response.data["details"]["non_field_errors"]
        assert any("end_frame_number must be greater than start_frame_number" in str(msg) for msg in error_msgs)