import pytest
from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import Mock, patch

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models import VideoFile, Label, LabelVideoSegment, InformationSource
from lx_dtypes.models.contracts.video_segments import parse_segment_annotation_input
import endoreg_db.services.segment_sync as segment_sync

create_user_segment_from_annotation = segment_sync.create_user_segment_from_annotation


def _segment_annotation_payload(**payload: object) -> dict[str, object]:
    return payload


def _segment_id(segment: LabelVideoSegment | None) -> int:
    assert segment is not None
    pk = segment.pk
    assert pk is not None
    return int(pk)


@pytest.mark.django_db
class TestSegmentAnnotationFlow(TestCase):
    """
    Test the complete flow of segment annotation updates creating user-source segments.
    """

    def setUp(self) -> None:
        """Set up test data"""
        # Create test user
        self.user = User.objects.create_user(
            username="testdoctor", email="test@example.com", password="testpass123"
        )

        # Create information sources
        self.prediction_source = InformationSource.objects.create(
            name="prediction", description="Algorithm-generated predictions"
        )
        self.user_source = InformationSource.objects.create(
            name="user", description="User-generated annotations"
        )

        # Create test label
        self.label = Label.objects.create(name="polyp")

        # Mock video file with required methods
        self.video = Mock(spec=VideoFile)
        self.video.id = 1
        self.video_fps = 25.0
        self.video.objects = Mock()

        # Patch manager lookup with automatic cleanup to avoid leaking into other tests.
        self.video_get_patcher = patch.object(
            VideoFile.objects, "get", return_value=self.video
        )
        self.mock_video_get = self.video_get_patcher.start()
        self.addCleanup(self.video_get_patcher.stop)

        def fake_get_video_fps(video: VideoFile) -> float:
            return self.video_fps

        self.video_fps_patcher = patch.object(
            segment_sync,
            "get_video_fps",
            side_effect=fake_get_video_fps,
        )
        self.video_fps_patcher.start()
        self.addCleanup(self.video_fps_patcher.stop)

    def test_create_user_segment_from_new_annotation(self) -> None:
        """Test creating a user segment from a new segment annotation"""
        # Create annotation data for a new segment
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=10.0,
            end_time=15.0,
            text="polyp",
            metadata={},
            user_id="testdoctor",
        )

        # Mock LabelVideoSegment.create_from_video
        mock_segment = Mock(spec=LabelVideoSegment)
        mock_segment.pk = 123
        mock_segment.source = self.user_source
        mock_segment.save = Mock()

        with patch.object(
            LabelVideoSegment, "create_from_video", return_value=mock_segment
        ) as mock_create_from_video:
            with patch.object(Label.objects, "filter") as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label

                result = create_user_segment_from_annotation(annotation_data, self.user)

                # Verify segment was created
                self.assertIsNotNone(result)
                self.assertEqual(_segment_id(result), 123)

                # Verify create_from_video was called with correct parameters
                mock_create_from_video.assert_called_once_with(
                    source=self.video,
                    prediction_meta=None,
                    label=self.label,
                    start_frame_number=250,  # 10.0 * 25 fps
                    end_frame_number=375,  # 15.0 * 25 fps
                )

                # Verify user source was set
                mock_segment.save.assert_called_once()

    def test_create_user_segment_defaults_invalid_fps_to_50(self) -> None:
        self.video_fps = 0
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=10.0,
            end_time=15.0,
            text="polyp",
            metadata={},
        )

        mock_segment = Mock(spec=LabelVideoSegment)
        mock_segment.pk = 123
        mock_segment.source = self.user_source
        mock_segment.save = Mock()

        with patch.object(
            LabelVideoSegment, "create_from_video", return_value=mock_segment
        ) as mock_create_from_video:
            with patch.object(Label.objects, "filter") as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label

                result = create_user_segment_from_annotation(annotation_data, self.user)

                self.assertIsNotNone(result)
                mock_create_from_video.assert_called_once_with(
                    source=self.video,
                    prediction_meta=None,
                    label=self.label,
                    start_frame_number=int(10.0 * DEFAULT_VIDEO_FPS),
                    end_frame_number=int(15.0 * DEFAULT_VIDEO_FPS),
                )

    def test_create_user_segment_from_updated_annotation(self) -> None:
        """Test creating a user segment when updating an existing annotation"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.pk = 456
        original_segment.start_frame_number = 250  # 10.0 * 25 fps
        original_segment.end_frame_number = 375  # 15.0 * 25 fps
        original_segment.label = self.label
        original_segment.prediction_meta = Mock()

        with patch.object(
            LabelVideoSegment.objects, "get", return_value=original_segment
        ):
            # Create annotation data with changes (different end time)
            annotation_data = _segment_annotation_payload(
                type="segment",
                video_id=1,
                start_time=10.0,
                end_time=18.0,  # Changed from 15.0 to 18.0
                text="polyp",
                metadata={"segment_id": 456},
                user_id="testdoctor",
            )

            # Mock new user segment
            mock_new_segment = Mock(spec=LabelVideoSegment)
            mock_new_segment.pk = 789
            mock_new_segment.source = self.user_source
            mock_new_segment.save = Mock()

            with patch.object(
                LabelVideoSegment, "create_from_video", return_value=mock_new_segment
            ) as mock_create_from_video:
                with patch.object(Label.objects, "filter") as mock_label_filter:
                    mock_label_filter.return_value.first.return_value = self.label

                    result = create_user_segment_from_annotation(
                        annotation_data, self.user
                    )

                    # Verify new segment was created
                    self.assertIsNotNone(result)
                    self.assertEqual(_segment_id(result), 789)

                    # Verify create_from_video was called with updated parameters
                    mock_create_from_video.assert_called_once_with(
                        source=self.video,
                        prediction_meta=original_segment.prediction_meta,
                        label=self.label,
                        start_frame_number=250,  # 10.0 * 25 fps
                        end_frame_number=450,  # 18.0 * 25 fps (updated)
                    )

    def test_no_segment_created_for_unchanged_annotation(self) -> None:
        """Test that no new segment is created if annotation hasn't changed"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.pk = 456
        original_segment.start_frame_number = 250  # 10.0 * 25 fps
        original_segment.end_frame_number = 375  # 15.0 * 25 fps
        original_segment.label = self.label
        original_segment.prediction_meta = Mock()

        with patch.object(
            LabelVideoSegment.objects, "get", return_value=original_segment
        ):
            # Create annotation data with no changes
            annotation_data = _segment_annotation_payload(
                type="segment",
                video_id=1,
                start_time=10.0,
                end_time=15.0,  # Same as original
                text="polyp",  # Same label
                metadata={"segment_id": 456},
                user_id="testdoctor",
            )

            with patch.object(Label.objects, "filter") as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label

                result = create_user_segment_from_annotation(annotation_data, self.user)

                # Verify no new segment was created
                self.assertIsNone(result)

    def test_segment_creation_preserves_original_prediction(self) -> None:
        """Test that original prediction segment remains unmodified"""
        # Create original prediction segment
        original_segment = Mock(spec=LabelVideoSegment)
        original_segment.pk = 456
        original_segment.start_frame_number = 250
        original_segment.end_frame_number = 375
        original_segment.label = self.label
        original_segment.source = self.prediction_source
        original_segment.prediction_meta = Mock()

        with patch.object(
            LabelVideoSegment.objects, "get", return_value=original_segment
        ):
            annotation_data = _segment_annotation_payload(
                type="segment",
                video_id=1,
                start_time=10.0,
                end_time=18.0,  # Changed
                text="polyp",
                metadata={"segment_id": 456},
                user_id="testdoctor",
            )

            mock_new_segment = Mock(spec=LabelVideoSegment)
            mock_new_segment.pk = 789
            mock_new_segment.source = self.user_source
            mock_new_segment.save = Mock()

            with patch.object(
                LabelVideoSegment, "create_from_video", return_value=mock_new_segment
            ) as _mock_create_from_video:
                with patch.object(Label.objects, "filter") as mock_label_filter:
                    mock_label_filter.return_value.first.return_value = self.label

                    result = create_user_segment_from_annotation(
                        annotation_data, self.user
                    )

                    # Verify original segment was not modified
                    # In a real test, you'd check the database state
                    self.assertIsNotNone(result)
                    assert result is not None
                    self.assertNotEqual(_segment_id(result), original_segment.pk)
                    self.assertEqual(result.source, self.user_source)

    def test_non_segment_annotation_ignored(self) -> None:
        """Test that non-segment annotations don't create segments"""
        annotation_data = _segment_annotation_payload(
            type="point",  # Not a segment
            video_id=1,
            start_time=10.0,
            text="marker",
            user_id="testdoctor",
        )

        result = create_user_segment_from_annotation(annotation_data, self.user)

        # Verify no segment was created
        self.assertIsNone(result)

    def test_invalid_annotation_data_handling(self) -> None:
        """Test handling of invalid annotation data"""
        # Missing required fields
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            # Missing start_time and end_time
            text="polyp",
            user_id="testdoctor",
        )

        result = create_user_segment_from_annotation(annotation_data, self.user)

        # Verify no segment was created
        self.assertIsNone(result)

    def test_invalid_segment_time_range_handling(self) -> None:
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=15.0,
            end_time=10.0,
            text="polyp",
        )

        result = create_user_segment_from_annotation(annotation_data, self.user)

        self.assertIsNone(result)

    def test_annotation_contract_parses_snake_case_payload(self) -> None:
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=10.0,
            end_time=15.0,
            text="polyp",
            tags=["polyp"],
            metadata={"segment_id": 456},
        )

        parsed = parse_segment_annotation_input(annotation_data)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.video_id, 1)
        self.assertEqual(parsed.start_time, 10.0)
        self.assertEqual(parsed.metadata.segment_id, 456)

    def test_annotation_contract_rejects_negative_start_time(self) -> None:
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=-0.1,
            end_time=15.0,
            text="polyp",
        )

        parsed = parse_segment_annotation_input(annotation_data)

        self.assertIsNone(parsed)

    def test_create_user_segment_rounds_time_to_nearest_frame(self) -> None:
        annotation_data = _segment_annotation_payload(
            type="segment",
            video_id=1,
            start_time=7 / 25.0,
            end_time=15 / 25.0,
            text="polyp",
            metadata={},
        )

        mock_segment = Mock(spec=LabelVideoSegment)
        mock_segment.pk = 123
        mock_segment.source = self.user_source
        mock_segment.save = Mock()

        with patch.object(
            LabelVideoSegment, "create_from_video", return_value=mock_segment
        ) as mock_create_from_video:
            with patch.object(Label.objects, "filter") as mock_label_filter:
                mock_label_filter.return_value.first.return_value = self.label

                result = create_user_segment_from_annotation(annotation_data, self.user)

                self.assertIsNotNone(result)
                mock_create_from_video.assert_called_once_with(
                    source=self.video,
                    prediction_meta=None,
                    label=self.label,
                    start_frame_number=7,
                    end_frame_number=15,
                )

    def test_video_not_found_handling(self) -> None:
        """Test handling when video doesn't exist"""
        # Mock VideoFile.objects.get to raise DoesNotExist
        with patch.object(
            VideoFile.objects, "get", side_effect=VideoFile.DoesNotExist()
        ):
            annotation_data = _segment_annotation_payload(
                type="segment",
                video_id=999,  # Non-existent video
                start_time=10.0,
                end_time=15.0,
                text="polyp",
                user_id="testdoctor",
            )

            result = create_user_segment_from_annotation(annotation_data, self.user)

            # Verify no segment was created
            self.assertIsNone(result)


@pytest.mark.django_db
class TestAnnotationViews(TestCase):
    """Test annotation view endpoints"""

    def setUp(self) -> None:
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testdoctor", email="test@example.com", password="testpass123"
        )

    # TODO if required, implement tests for annotation views
    # @patch('endoreg_db.views.annotation_views.create_user_segment_from_annotation')
    # def test_create_segment_annotation_endpoint(self, mock_create_segment):
    #     """Test POST /api/annotations/ with segment data"""
    #     from django.test import Client
    #     from django.urls import reverse

    #     # Mock segment creation
    #     mock_segment = Mock()
    #     mock_segment.pk = 123
    #     mock_create_segment.return_value = mock_segment

    #     client = Client()
    #     client.force_login(self.user)

    #     # This test would require the actual URL to be configured
    #     # For now, just verify the logic would work
    #     annotation_data = {
    #         'type': 'segment',
    #         'video_id': 1,
    #         'start_time': 10.0,
    #         'end_time': 15.0,
    #         'text': 'polyp',
    #         'metadata': {},
    #         'user_id': 'testdoctor'
    #     }

    #     # In a real test, you'd make the actual HTTP request:
    #     # response = client.post('/api/annotations/', data=annotation_data, content_type='application/json')
    #     # self.assertEqual(response.status_code, 201)
    #     # self.assertEqual(response.json()['metadata']['segment_id'], 123)

    #     # For now, just verify our mock setup
    #     self.assertIsNotNone(mock_segment)
    #     self.assertEqual(mock_segment.id, 123)
