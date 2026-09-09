from __future__ import annotations

from typing import Protocol, cast

from django.contrib.auth.models import AbstractUser, Group, User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import (
    Center,
    Examiner,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    PortalUserInfo,
    VideoFile,
)
from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.views.video.ai import (
    FrameAnnotationBulkUpsertView,
    FrameAnnotationRandomTaskView,
    FrameAnnotationSkipView,
    FrameBoxAnnotationView,
)


class _GroupRelation(Protocol):
    def add(self, *groups: Group) -> None: ...


class _UserWithGroups(Protocol):
    groups: _GroupRelation


class AnnotationAccessControlsTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="annotation-access-center")
        self.other_center = Center.objects.create(name="annotation-access-other")
        self.video = self._video(self.center, "own")
        self.other_video = self._video(self.other_center, "other")
        self.frame = self._frame(self.video, 10)
        self.other_frame = self._frame(self.other_video, 20)
        self.label = Label.objects.create(name="annotation-access-label")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(
            name="prediction_annotation"
        )
        self.user = self._center_user("center-annotator", self.center)

    @staticmethod
    def _video(center: Center, suffix: str) -> VideoFile:
        return VideoFile.objects.create(
            center=center,
            video_hash=f"annotation-access-{suffix}",
            original_file_name=f"annotation_access_{suffix}.mp4",
            fps=25.0,
            frame_count=100,
        )

    @staticmethod
    def _frame(video: VideoFile, frame_number: int) -> Frame:
        return Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=f"frame_{frame_number:07d}.jpg",
            is_extracted=True,
        )

    @staticmethod
    def _center_user(username: str, center: Center) -> AbstractUser:
        user = User.objects.create_user(username=username)
        examiner = Examiner.objects.create(
            first_name="Annotation",
            last_name="Reviewer",
            center=center,
            hash=f"{username}-hash",
            is_real_person=False,
        )
        PortalUserInfo.objects.create(user=user, examiner=examiner)
        return cast(AbstractUser, user)

    def _request(self, method: str, path: str, data: object = None):
        factory_method = getattr(self.factory, method)
        request = factory_method(path, data, format="json")
        force_authenticate(request, user=self.user)
        return request

    def _bulk_payload(
        self,
        *,
        frame: Frame,
        annotator: str | None = None,
        source_name: str = "manual_annotation",
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "frame_id": frame.pk,
            "label_id": self.label.pk,
            "information_source_name": source_name,
            "value": True,
        }
        if annotator is not None:
            item["annotator"] = annotator
        return {"video_id": model_pk(frame.video), "annotations": [item]}

    def test_bulk_upsert_binds_standard_user_to_authenticated_identity(self) -> None:
        request = self._request(
            "post",
            "/api/media/annotations/frames/bulk-upsert/",
            self._bulk_payload(frame=self.frame),
        )

        response = FrameAnnotationBulkUpsertView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        annotation = ImageClassificationAnnotation.objects.get(frame=self.frame)
        self.assertEqual(annotation.annotator, str(getattr(self.user, "username")))

    def test_bulk_upsert_rejects_unprivileged_annotator_override(self) -> None:
        request = self._request(
            "post",
            "/api/media/annotations/frames/bulk-upsert/",
            self._bulk_payload(frame=self.frame, annotator="different-reviewer"),
        )

        response = FrameAnnotationBulkUpsertView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ImageClassificationAnnotation.objects.exists())

    def test_center_admin_may_use_explicit_annotator_scope(self) -> None:
        cast(_UserWithGroups, self.user).groups.add(
            Group.objects.create(name="center_scope:admin")
        )
        request = self._request(
            "post",
            "/api/media/annotations/frames/bulk-upsert/",
            self._bulk_payload(frame=self.frame, annotator="reviewer-two"),
        )

        response = FrameAnnotationBulkUpsertView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ImageClassificationAnnotation.objects.filter(
                frame=self.frame,
                annotator="reviewer-two",
            ).exists()
        )

    def test_bulk_upsert_allows_frames_from_other_centers(self) -> None:
        request = self._request(
            "post",
            "/api/media/annotations/frames/bulk-upsert/",
            self._bulk_payload(frame=self.other_frame),
        )

        response = FrameAnnotationBulkUpsertView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ImageClassificationAnnotation.objects.filter(
                frame=self.other_frame,
                annotator=str(getattr(self.user, "username")),
            ).exists()
        )

    def test_random_queue_may_return_frames_from_multiple_centers(self) -> None:
        request = self._request(
            "get",
            "/api/media/annotations/frames/random-task/",
            {"limit": 10, "information_source_name": self.manual_source.name},
        )

        response = FrameAnnotationRandomTaskView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {task["video_id"] for task in response.data["tasks"]},
            {self.video.pk, self.other_video.pk},
        )

    def test_skip_allows_frame_from_other_center(self) -> None:
        request = self._request(
            "post",
            "/api/media/annotations/frames/skip/",
            {"frame_id": self.other_frame.pk},
        )

        response = FrameAnnotationSkipView.as_view()(request)

        self.assertEqual(response.status_code, 200)

    def test_interactive_frame_endpoint_rejects_prediction_source(self) -> None:
        request = self._request(
            "post",
            "/api/media/annotations/frames/bulk-upsert/",
            self._bulk_payload(
                frame=self.frame,
                source_name=self.prediction_source.name,
            ),
        )

        response = FrameAnnotationBulkUpsertView.as_view()(request)

        self.assertEqual(response.status_code, 403)

    def test_box_endpoint_allows_cross_center_and_enforces_manual_provenance(
        self,
    ) -> None:
        cross_center_request = self._request(
            "get",
            "/api/media/annotations/frames/boxes/",
            {"frame_id": self.other_frame.pk},
        )
        cross_center_response = FrameBoxAnnotationView.as_view()(cross_center_request)

        prediction_request = self._request(
            "post",
            "/api/media/annotations/frames/boxes/",
            {
                "frame_id": self.frame.pk,
                "information_source_name": self.prediction_source.name,
                "annotations": [
                    {
                        "label_id": self.label.pk,
                        "x": 1,
                        "y": 1,
                        "width": 10,
                        "height": 10,
                        "image_width": 100,
                        "image_height": 100,
                    }
                ],
            },
        )
        prediction_response = FrameBoxAnnotationView.as_view()(prediction_request)

        self.assertEqual(cross_center_response.status_code, 200)
        self.assertEqual(prediction_response.status_code, 403)
