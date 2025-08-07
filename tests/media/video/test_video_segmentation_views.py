"""
Tests for video segmentation views.

Tests both VideoLabelView (segment loading) and VideoStreamView (video streaming)
to ensure they work correctly for both Vue SPA and React dashboard frontends.
"""
import pytest
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch

from endoreg_db.helpers.data_loader import load_base_db_data
from .test_helpers import setup_realistic_test_data, create_test_label
from endoreg_db.models import VideoFile, Label, LabelVideoSegment, Center

class VideoSegmentationViewTests(TestCase):
    """Test video segmentation API endpoints."""

    def setUp(self):
        load_base_db_data()
        self.client = APIClient()
        test_data = setup_realistic_test_data()
        self.video:VideoFile = test_data["video"]
        self.assertIsInstance(self.video, VideoFile)
        self.center = self.video.center

        self.labels = test_data["labels"]
        self.segment_nbi = test_data["segments"][0]
        self.segment_polyp = test_data["segments"][1]
        self.segment_snare = test_data["segments"][2]
        self.label_nbi = self.labels[0]
        self.label_polyp = self.labels[1]
        self.label_snare = self.labels[2]

    def test_label_view_success_for_valid_labels(self):
        """Test successful retrieval of label segments for all label types."""
        test_labels = self.labels
        
        for label_obj in test_labels:
            label_name = label_obj.name
            with self.subTest(label=label_name):
                url = f"/api/lvs/by-label-name/{label_name}/by-video-id/{self.video.id}/"
                
                response = self.client.get(url)
                
                # Should return 200 (either with data or empty)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                data = response.json()
                if isinstance(data, list):
                    data = data[0]
                
                # Verify response structure
                self.assertEqual(data["label_name"], label_name)
                self.assertEqual(data["label"], label_obj.id)
                # self.assertIn("time_segments", data) #TODO Check if this is needed
                self.assertIn("frame_predictions", data) #TODO Check if this is needed
                
                # self.assertGreater(len(data["time_segments"]), 0) #TODO check what lx-annotate expects

    def test_label_view_returns_404_for_missing_label(self):
        """Test 404 response when label doesn't exist."""
        url = f"/api/lvs/by-label-name/NON_EXISTENT_TEST_LABEL/by-video-id/{self.video.id}/"
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        data = response.json()
        self.assertIn("error", data)

    def test_video_label_view_missing_video_returns_404(self):
        """Test 404 response when video doesn't exist."""
        url = f"/api/lvs/by-label-name/{self.label_nbi.name}/by-video-id/99999/"
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        data = response.json()
        self.assertIn("error", data)

    def test_video_label_view_no_segments_returns_empty_data(self):
        """Test empty response when no segments exist for label."""
        label_appendix = Label.objects.get(name="appendix")
        url = f"/api/lvs/by-label-name/{label_appendix.name}/by-video-id/{self.video.id}/"

        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data, [])

    def test_stream_view_byte_range_intact(self):
        """Test video streaming with byte range requests - ensures regression protection."""
        # Mock the video file to have an active_file_path
        # with patch.object(self.video, 'active_file_path', self.temp_video_file.name):
        url = f"/api/media/videos/{self.video.id}/stream/"
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertIn('Accept-Ranges', response)
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertIn('Content-Length', response)
        # Content-Range is only present in partial content responses
        self.assertIn('Access-Control-Allow-Origin', response)

    @override_settings(DEBUG=True)
    def test_video_segments_creation_works_in_dev_mode(self):
        """Test that video segment creation works in development mode (AllowAny permissions)."""
        segment_data = {
            'video_id': self.video.id,
            'start_time': 1.0,
            'end_time': 2.0,
            'label_name': self.label_snare.name
        }
        
        url = "/api/video-segments/"
        response = self.client.post(url, data=segment_data, format='json')
        
        # Should work in development mode
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['label_name'], self.label_snare.name)
        self.assertEqual(data['video_id'], self.video.id)

#TODO Needs to be fixed since our app checks the .env file specifically for DJANGO_DEBUG
    # @override_settings(DEBUG=False)
    # def test_video_segments_creation_requires_auth_in_prod_mode(self):
    #     """Test that video segment creation requires authentication in production mode."""
    #     segment_data = {
    #         'video_id': self.video.id,
    #         'start_time': 1,
    #         'end_time': 2,
    #         'label_name': self.label_snare.name
    #     }
        
    #     url = "/api/video-segments/"
    #     response = self.client.post(url, data=segment_data, format='json')
        
    #     # Should require authentication in production mode
    #     self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        
    def test_both_frontends_vue_and_react_compatibility(self):
        """Test that both Vue SPA and React dashboard can use the APIs."""
        # Test with Vue SPA headers
        vue_headers = {
            'HTTP_ACCEPT': 'application/json',
            'HTTP_USER_AGENT': 'Mozilla/5.0 (Vue.js SPA)'
        }
        
        # url = f"/api/videos/{self.video.id}/labels/nbi/"
        url = f"/api/lvs/by-label-name/{self.label_nbi.name}/by-video-id/{self.video.id}/"
        vue_response = self.client.get(url, **vue_headers)
        
        # Test with React dashboard headers  
        react_headers = {
            'HTTP_ACCEPT': 'application/json',
            'HTTP_USER_AGENT': 'Mozilla/5.0 (React Dashboard)'
        }
        
        react_response = self.client.get(url, **react_headers)
        
        # Both should return identical data
        self.assertEqual(vue_response.status_code, status.HTTP_200_OK)
        self.assertEqual(react_response.status_code, status.HTTP_200_OK)
        self.assertEqual(vue_response.json(), react_response.json())
        
        # Verify response schema matches expected format for both clients
        for response in [vue_response, react_response]:
            data_list = response.json()
            for data in data_list:
                self.assertIn("label_id", data)
                self.assertIn("time_segments", data)
                self.assertIn("frame_predictions", data)
                # Verify segment structure
                if data["time_segments"]:
                    segment = data["time_segments"]
                    required_fields = ["segment_id", "segment_start", "segment_end", 
                                        "start_time", "end_time", "frames"]
                    for field in required_fields:
                        self.assertIn(field, segment)
                self.assertIn("label_id", data)
                self.assertIn("time_segments", data)
                self.assertIn("frame_predictions", data)
                
                # Verify segment structure
                if data["time_segments"]:
                    segment = data["time_segments"]
                    required_fields = ["segment_id", "segment_start", "segment_end", 
                                    "start_time", "end_time", "frames"]
                    for field in required_fields:
                        self.assertIn(field, segment)

    def get_lvs_response(self):
        url = f"/api/lvs/by-label-name/{self.label_nbi.name}/by-video-id/{self.video.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        if isinstance(data, list):
            data = data[0]
        return data

    def test_lvs_top_level_schema(self):
        """Verify top-level schema for LVS response."""
        data = self.get_lvs_response()
        expected_top_level = {"label", "time_segments", "frame_predictions"}
        keys = data.keys()
        for key in expected_top_level:
            self.assertIn(key, keys, f"Expected key '{key}' not found in response")

    def test_lvs_segment_schema(self):
        """Verify segment schema for LVS response."""
        data = self.get_lvs_response()
        segment = data["time_segments"]
        expected_segment_fields = {
            "segment_id", "segment_start", "segment_end", 
            "start_time", "end_time", "frames"
        }
        for field in expected_segment_fields:
            self.assertIn(field, segment, msg=f"Expected field '{field}' not found in segment")

    def test_lvs_frame_schema(self):
        """Verify frame schema for LVS response."""
        data = self.get_lvs_response()
        segment = data["time_segments"]
        frame_data = list(segment["frames"])[0]
        expected_frame_fields = {"frame_filename", "frame_file_path", "predictions", "manual_annotations"}
        for field in expected_frame_fields:
            self.assertIn(field, frame_data, f"Expected field '{field}' not found in frame data")

    def test_lvs_prediction_schema(self):
        """Verify prediction schema for LVS response."""
        data = self.get_lvs_response()
        segment = data["time_segments"]
        frame_data = list(segment["frames"])[0]
        prediction = frame_data["predictions"]
        expected_prediction_fields = {"frame_number", "label", "confidence"}
        
        #TODO create predictions and annotations and check if they are in the response
        # for field in expected_prediction_fields:
        #     self.assertIn(field, prediction, f"Expected field '{field}' not found in prediction")

    # def test_error_logging_without_500_responses(self):
    #     """Test that path errors are logged as warnings but don't cause 500 responses."""
    #     # Create a video with a problematic frame_dir that might cause path issues
    #     video = VideoFile.objects.create(
    #         original_file_name="problematic_video.mp4",
    #         fps=25.0,
    #         frame_dir=123,  # Invalid type that could cause issues
    #         center=self.center  # Add required center
    #     )
        
        
        # with self.assertLogs('endoreg_db.views.video_segmentation_views', level='WARNING') as cm:
        #     url = f"/api/videos/{video.id}/labels/nbi/"
        #     response = self.client.get(url)
            
        #     # Should return 200, not 500
        #     self.assertEqual(response.status_code, status.HTTP_200_OK)
            
        #     # Check that warning was logged
        #     warning_logged = any('Could not construct frame path' in record for record in cm.output)
        #     self.assertTrue(warning_logged, "Expected warning about frame path construction")

    # @patch('endoreg_db.views.video_segmentation_views.Path')
    # def test_video_label_view_handles_path_errors(self, mock_path):
    #     """Test that path construction errors are handled gracefully."""
    #     # Mock Path to raise an exception
    #     mock_path.side_effect = Exception("Path construction failed")
        
    #     url = f"/api/videos/{self.video.id}/labels/nbi/"
    #     response = self.client.get(url)
        
    #     # Should still return 200 but with empty frame_file_path
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
    #     data = response.json()
        
    #     # Verify segments exist but frame paths are empty
    #     self.assertEqual(len(data["time_segments"]), 1)
    #     segment = data["time_segments"][0]
    #     frame_data = list(segment["frames"].values())[0]
    #     self.assertEqual(frame_data["frame_file_path"], "")

    # def test_permission_layer_intact_for_read_operations(self):
    #     """Test that permission layer allows read operations in both dev and prod."""
    #     # Test VideoLabelView (which should allow read access)
    #     url = f"/api/videos/{self.video.id}/labels/nbi/"
    #     response = self.client.get(url)
        
    #     # Should work fine regardless of auth status for read operations
    #     self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    #     # Test video streaming (which should also allow anonymous access)
    #     with patch.object(self.video, 'active_file_path', self.temp_video_file.name):
    #         stream_url = f"/api/videostream/{self.video.id}/"
    #         stream_response = self.client.get(stream_url)
    #         self.assertEqual(stream_response.status_code, status.HTTP_200_OK)

    # def test_no_type_error_logs_for_path_operations(self):
    #     """Test that no TypeError logs appear for path operations with string concatenation."""
    #     with self.assertLogs('endoreg_db.views.video_segmentation_views', level='INFO') as cm:
    #         # This should not generate any TypeError logs
    #         url = f"/api/videos/{self.video.id}/labels/nbi/"
    #         response = self.client.get(url)
            
    #         self.assertEqual(response.status_code, status.HTTP_200_OK)
            
    #         # Verify no TypeError mentions in logs
    #         for log_record in cm.output:
    #             self.assertNotIn("TypeError", log_record)
    #             self.assertNotIn("unsupported operand type", log_record)


# Integration test to verify end-to-end functionality
class VideoSegmentationIntegrationTests(TestCase):
    """Integration tests for complete video segmentation workflow."""
    
    def setUp(self):
        """Set up integration test environment."""
        load_base_db_data()
        self.client = APIClient()
        test_data = setup_realistic_test_data()
        self.video = test_data["video"]
        self.labels = test_data["labels"]
        self.segments = test_data["segments"]
        self.label_nbi = self.labels[0]
        self.label_polyp = self.labels[1]
        self.label_snare = self.labels[2]

    @override_settings(DEBUG=True)
    def test_complete_segment_workflow_in_dev_mode(self):
        """Test complete workflow: create segment -> retrieve segments -> stream video."""
        # Step 1: Create a segment
        create_data = {
            'video_id': self.video.id,
            'start_time': 0,
            'end_time': 1,
            'label_name': self.label_nbi.name
        }
        
        create_response = self.client.post('/api/video-segments/', data=create_data, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        
        # Step 2: Retrieve segments via VideoLabelView
        label_response = self.client.get(f'/api/lvs/by-label-name/{self.label_nbi.name}/by-video-id/{self.video.id}/')
        self.assertEqual(label_response.status_code, status.HTTP_200_OK)
        
        label_data = label_response.json()
        self.assertGreaterEqual(len(label_data), 1)
        
        # Step 3: Verify segment data structure
        segment = label_data[0]['time_segments']
        self.assertEqual(segment['start_time'], 0.0)
        self.assertEqual(segment['end_time'], 1.0)
        
        # Step 4: Test video streaming still works
        stream_response = self.client.get(f'/api/media/videos/{self.video.id}/stream/')
        self.assertEqual(stream_response.status_code, status.HTTP_200_OK)

    #TODO @coderabbitai create a follow up issue for @Hamzaukw
    # def test_error_boundaries_preserve_api_stability(self):
    #     """Test that various error conditions don't break API contract."""
    #     # Test with non-existent video
    #     response1 = self.client.get('/api/videos/99999/labels/test_label/')
    #     self.assertEqual(response1.status_code, status.HTTP_404_NOT_FOUND)
        
    #     # Test with non-existent label
    #     response2 = self.client.get(f'/api/videos/{self.video.id}/labels/nonexistent/')
    #     self.assertEqual(response2.status_code, status.HTTP_404_NOT_FOUND)
        
    #     # Test with malformed video ID
    #     response3 = self.client.get('/api/videos/abc/labels/test_label/')
    #     self.assertEqual(response3.status_code, status.HTTP_404_NOT_FOUND)
        
    #     # All should return proper JSON error responses
    #     for response in [response1, response2, response3]:
    #         self.assertEqual(response['Content-Type'], 'application/json')
    #         data = response.json()
    #         self.assertIn('error', data)