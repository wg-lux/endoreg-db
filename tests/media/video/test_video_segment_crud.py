"""
Pytest-Tests für das LabelVideoSegmentViewSet CRUD-Interface
"""
import pytest
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth.models import User
from endoreg_db.models import VideoFile, Label, LabelVideoSegment


@pytest.mark.django_db
class TestLabelVideoSegmentCRUD:
    """Test-Suite für vollständige CRUD-Operationen mit LabelVideoSegmentViewSet"""
    
    def setup_method(self):
        """Setup für jeden Test"""
        self.client = APIClient()
        
        # Test-User erstellen (nur für Prod-Umgebung nötig)
        self.user = User.objects.create_user(
            username='testuser', 
            password='testpass'
        )
        
        # Test-Daten erstellen
        self.video = VideoFile.objects.create(
            original_filename='test_video.mp4',
            fps=25.0
        )
        
        self.label = Label.objects.create(
            name='Polyp',
            description='Test polyp label'
        )
    
    def test_create_segment_success(self):
        """Test: Erfolgreiches Erstellen eines neuen Segments"""
        # In DEV-Modus ist keine Authentifizierung nötig
        data = {
            "video_file": self.video.id,
            "label": self.label.id,
            "start_frame_number": 100,
            "end_frame_number": 200,
            "start_time": 4.0,
            "end_time": 8.0
        }
        
        response = self.client.post("/api/video-segments/", data, format="json")
        
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["start_frame_number"] == 100
        assert response.data["end_frame_number"] == 200
        
        # Verifiziere, dass das Segment in der DB gespeichert wurde
        segment = LabelVideoSegment.objects.get(id=response.data["id"])
        assert segment.video_file == self.video
        assert segment.label == self.label
    
    def test_list_segments_with_filtering(self):
        """Test: Liste aller Segmente mit optionaler Filterung"""
        # Erstelle Test-Segmente
        segment1 = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=150
        )
        
        segment2 = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=200,
            end_frame_number=250
        )
        
        # Test: Alle Segmente abrufen
        response = self.client.get("/api/video-segments/")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        
        # Test: Filter nach video_id
        response = self.client.get(f"/api/video-segments/?video_id={self.video.id}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        
        # Test: Filter nach label_id
        response = self.client.get(f"/api/video-segments/?label_id={self.label.id}")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
    
    def test_retrieve_single_segment(self):
        """Test: Einzelnes Segment abrufen"""
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=150
        )
        
        response = self.client.get(f"/api/video-segments/{segment.id}/")
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == segment.id
        assert response.data["start_frame_number"] == 100
    
    def test_update_segment_partial(self):
        """Test: Teilweise Aktualisierung eines Segments (PATCH)"""
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=150
        )
        
        # Nur end_frame_number aktualisieren
        data = {"end_frame_number": 200}
        
        response = self.client.patch(f"/api/video-segments/{segment.id}/", data, format="json")
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["end_frame_number"] == 200
        assert response.data["start_frame_number"] == 100  # Unverändert
        
        # Verifiziere in der DB
        segment.refresh_from_db()
        assert segment.end_frame_number == 200
    
    def test_update_segment_full(self):
        """Test: Vollständige Aktualisierung eines Segments (PUT)"""
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=150
        )
        
        data = {
            "video_file": self.video.id,
            "label": self.label.id,
            "start_frame_number": 300,
            "end_frame_number": 400,
            "start_time": 12.0,
            "end_time": 16.0
        }
        
        response = self.client.put(f"/api/video-segments/{segment.id}/", data, format="json")
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data["start_frame_number"] == 300
        assert response.data["end_frame_number"] == 400
    
    def test_delete_segment(self):
        """Test: Segment löschen"""
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=150
        )
        
        segment_id = segment.id
        
        response = self.client.delete(f"/api/video-segments/{segment_id}/")
        
        assert response.status_code == status.HTTP_204_NO_CONTENT
        
        # Verifiziere, dass das Segment gelöscht wurde
        assert not LabelVideoSegment.objects.filter(id=segment_id).exists()
    
    def test_create_segment_validation_error(self):
        """Test: Fehlerbehandlung bei ungültigen Daten"""
        # Fehlendes video_file
        data = {
            "label": self.label.id,
            "start_frame_number": 100,
            "end_frame_number": 50  # Ende vor Start - sollte Fehler verursachen
        }
        
        response = self.client.post("/api/video-segments/", data, format="json")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data