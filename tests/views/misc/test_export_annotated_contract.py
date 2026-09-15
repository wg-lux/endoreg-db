from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import override_settings

from lx_dtypes.models.contracts.video_frame_export import export_config
from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    PortalUserInfo,
    VideoFile,
    VideoState,
)
from endoreg_db.utils.file_operations import atomic_write_file


class _ExportResultLike(SimpleNamespace):
    success: bool
    output_path: Path
    row_count: int
    exported_video_count: int
    exported_frame_count: int
    video_output_dir: Path | None
    frame_output_dir: Path | None


def _success_result() -> _ExportResultLike:
    return _ExportResultLike(
        success=True,
        output_path=Path("frames.csv"),
        row_count=0,
        exported_video_count=0,
        exported_frame_count=0,
        video_output_dir=None,
        frame_output_dir=None,
    )


class _FakeExporterClient:
    def __init__(self, captured: dict[str, export_config]) -> None:
        self._captured = captured

    def run_export(self, config: export_config) -> _ExportResultLike:
        self._captured["config"] = config
        return _success_result()


class ExportAnnotatedContractTests(TestCase):
    def setUp(self) -> None:
        suffix = uuid4().hex[:8]

        self.center = Center.objects.create(
            name=f"export-contract-center-{suffix}",
        )
        self.state = VideoState.objects.create(
            anonymization_validated=True,
            outside_segments_removed=True,
            segment_annotations_created=False,
            segment_annotations_validated=False,
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            state=self.state,
            video_hash=f"export-contract-video-{suffix}",
            original_file_name="export-contract.mp4",
            fps=25.0,
            frame_count=1,
        )
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=1,
            relative_path="frame_0000001.jpg",
            is_extracted=True,
        )
        self.label = Label.objects.create(
            name=f"export-contract-label-{suffix}",
        )
        self.source = InformationSource.objects.create(
            name=f"export-contract-source-{suffix}",
        )
        self.annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="export-contract-test",
        )
        self.user = User.objects.create_user(username=f"export-user-{suffix}")
        portal_info = PortalUserInfo.objects.create(user=self.user)
        portal_info.centers.add(self.center)
        self.client.force_login(self.user)

    def _valid_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "output_path": "frames.csv",
            "output_dir": "data/export",
            "output_format": "csv",
            "export_profile": "pts_dataset_v1",
            "video_id": self.video.pk,
            "label_id": self.label.pk,
            "information_source_name": self.source.name,
            "only_true": True,
            "limit": 100,
            "load_base_data": False,
            "export_videos": False,
            "export_frames": True,
            "transcode_frames": False,
            "transcode_fps": 50.0,
            "transcode_quality": 2,
            "transcode_ext": "jpg",
            "transcode_overwrite": False,
            "use_frame_pk_paths": False,
            "use_export_flags": True,
            "segment_ids": [],
            "center_key": "",
            "all_centers": False,
            "only_validated": True,
        }
        payload.update(overrides)
        return payload

    def test_api_rejects_export_without_required_video_scope(self) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data={"output_dir": "data/export"},
                content_type="application/json",
            )

        assert response.status_code == 400, response.content
        assert "config" not in captured

        response_text = str(response.json())
        assert "video_id" in response_text

    def test_api_accepts_valid_strict_export_payload(self) -> None:
        captured: dict[str, export_config] = {}

        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(),
                content_type="application/json",
            )

        assert response.status_code == 200, response.content

        config = captured["config"]
        assert config.output_path == Path("frames.csv")
        assert config.output_dir == Path("data/export")
        assert config.export_profile == "pts_dataset_v1"
        assert config.output_path is not None
        assert config.output_dir is not None
        assert Path(config.output_dir) / Path(config.output_path) == Path(
            "data/export/frames.csv"
        )
        assert config.video_id == self.video.pk
        assert config.label_id == self.label.pk
        assert config.information_source_name == self.source.name
        assert config.only_true is True
        assert config.limit == 100
        assert config.export_frames is True
        assert config.export_videos is False
        assert config.use_export_flags is True
        assert config.segment_ids == []
        assert config.center_key is None

    def test_api_rejects_video_outside_authenticated_center_scope(self) -> None:
        foreign_center = Center.objects.create(name=f"foreign-{uuid4().hex[:8]}")
        foreign_video = VideoFile.objects.create(
            center=foreign_center,
            state=VideoState.objects.create(),
            video_hash=f"foreign-video-{uuid4().hex[:8]}",
        )
        captured: dict[str, export_config] = {}

        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(video_id=foreign_video.pk),
                content_type="application/json",
            )

        assert response.status_code == 403, response.content
        assert "config" not in captured

    def test_api_accepts_segment_scoped_pts_payload_without_duplicate_filters(
        self,
    ) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data={
                    "output_dir": "data/export",
                    "output_path": "annotations.json",
                    "output_format": "json",
                    "export_profile": "pts_dataset_v1",
                    "video_id": self.video.pk,
                    "segment_ids": [17, 18],
                    "use_export_flags": False,
                    "export_frames": True,
                    "export_videos": False,
                },
                content_type="application/json",
            )

        assert response.status_code == 200, response.content
        config = captured["config"]
        assert config.segment_ids == [17, 18]
        assert config.label_id is None
        assert config.information_source_name is None
        assert config.only_true is None
        assert config.limit is None

    def test_api_boolean_strings_are_normalized_before_export_config(self) -> None:
        captured: dict[str, export_config] = {}

        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    all_centers="false",
                    export_videos="false",
                    export_frames="true",
                    only_validated="true",
                    only_true="true",
                    transcode_frames="false",
                    transcode_overwrite="false",
                    use_frame_pk_paths="false",
                    use_export_flags="true",
                ),
                content_type="application/json",
            )

        assert response.status_code == 200, response.content

        config = captured["config"]
        assert config.all_centers is False
        assert config.export_videos is False
        assert config.export_frames is True
        assert config.only_validated is True
        assert config.only_true is True
        assert config.transcode_frames is False
        assert config.transcode_overwrite is False
        assert config.use_frame_pk_paths is False
        assert config.use_export_flags is True

    def test_api_rejects_null_required_fields_before_client_is_created(self) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    video_id=None,
                    label_id=None,
                    information_source_name=None,
                    only_true=None,
                    limit=None,
                    output_dir=None,
                    use_frame_pk_paths=None,
                ),
                content_type="application/json",
            )

        assert response.status_code == 400, response.content
        assert "config" not in captured

        response_text = str(response.json())
        assert "video_id" in response_text
        assert "output_dir" in response_text
        assert "use_frame_pk_paths" in response_text

    def test_api_rejects_empty_required_string_fields(self) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    information_source_name="",
                    transcode_ext="",
                ),
                content_type="application/json",
            )

        assert response.status_code == 400, response.content
        assert "config" not in captured

        response_text = str(response.json())
        assert "transcode_ext" in response_text

    def test_api_rejects_invalid_non_positive_ids_and_limit(self) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    video_id=0,
                    label_id=0,
                    limit=0,
                ),
                content_type="application/json",
            )

        assert response.status_code == 400, response.content
        assert "config" not in captured

        response_text = str(response.json())
        assert "video_id" in response_text
        assert "label_id" in response_text
        assert "limit" in response_text

    def test_api_rejects_ambiguous_center_scope(self) -> None:
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    center_key=self.center.center_key,
                    all_centers=True,
                ),
                content_type="application/json",
            )

        assert response.status_code == 400, response.content
        assert "config" not in captured
        assert "center_key or all_centers" in str(response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_config_file_scope_is_checked_after_loading(self) -> None:
        user = User.objects.create_user(username="export-user")
        self.client.force_login(user)
        captured: dict[str, export_config] = {}

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "export.yaml"
            atomic_write_file(
                destination=config_path,
                content=[
                    (
                        "output_path: frames.csv\n"
                        "output_dir: data/export\n"
                        f"video_id: {self.video.pk}\n"
                        f"label_id: {self.label.pk}\n"
                        f"information_source_name: {self.source.name}\n"
                        "only_true: true\n"
                        "limit: 100\n"
                        "use_frame_pk_paths: false\n"
                        "segment_ids: []\n"
                        "center_key: ''\n"
                        "all_centers: true\n"
                        "export_videos: true\n"
                        "only_validated: false\n"
                    ).encode("utf-8")
                ],
            )

            with patch(
                "endoreg_db.services.export_annotated.annotation_exporter_client",
                return_value=_FakeExporterClient(captured),
            ):
                response = self.client.post(
                    "/api/media/videos/export-annotated/",
                    data={"config_path": str(config_path)},
                    content_type="application/json",
                )

        assert response.status_code == 403, response.content
        assert "config" not in captured
        assert "all_centers" in response.json()["error"]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_config_file_scope_all_centers_is_allowed_for_staff(self) -> None:
        user = User.objects.create_user(
            username="export-staff-user",
            is_staff=True,
        )
        self.client.force_login(user)

        captured: dict[str, export_config] = {}

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "export.yaml"
            atomic_write_file(
                destination=config_path,
                content=[
                    (
                        "output_path: frames.csv\n"
                        "output_dir: data/export\n"
                        f"video_id: {self.video.pk}\n"
                        f"label_id: {self.label.pk}\n"
                        f"information_source_name: {self.source.name}\n"
                        "only_true: true\n"
                        "limit: 100\n"
                        "use_frame_pk_paths: false\n"
                        "segment_ids: []\n"
                        "center_key: ''\n"
                        "all_centers: true\n"
                        "export_videos: false\n"
                        "export_frames: true\n"
                        "only_validated: true\n"
                    ).encode("utf-8")
                ],
            )

            with patch(
                "endoreg_db.services.export_annotated.annotation_exporter_client",
                return_value=_FakeExporterClient(captured),
            ):
                response = self.client.post(
                    "/api/media/videos/export-annotated/",
                    data={"config_path": str(config_path)},
                    content_type="application/json",
                )

        assert response.status_code == 200, response.content

        config = captured["config"]
        assert config.all_centers is True
        assert config.center_key is None
        assert config.video_id == self.video.pk
        assert config.label_id == self.label.pk
        assert config.information_source_name == self.source.name

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_config_file_can_be_overridden_by_api_payload(self) -> None:
        captured: dict[str, export_config] = {}

        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "export.yaml"
            atomic_write_file(
                destination=config_path,
                content=[
                    (
                        "output_path: from-config.csv\n"
                        "output_dir: data/from-config\n"
                        f"video_id: {self.video.pk}\n"
                        f"label_id: {self.label.pk}\n"
                        f"information_source_name: {self.source.name}\n"
                        "only_true: false\n"
                        "limit: 10\n"
                        "use_frame_pk_paths: false\n"
                        "segment_ids: []\n"
                        f"center_key: {self.center.center_key}\n"
                        "all_centers: false\n"
                    ).encode("utf-8")
                ],
            )

            with patch(
                "endoreg_db.services.export_annotated.annotation_exporter_client",
                return_value=_FakeExporterClient(captured),
            ):
                response = self.client.post(
                    "/api/media/videos/export-annotated/",
                    data={
                        "config_path": str(config_path),
                        "output_path": "from-api.csv",
                        "output_dir": "data/from-api",
                        "only_true": True,
                        "limit": 50,
                    },
                    content_type="application/json",
                )

        assert response.status_code == 200, response.content

        config = captured["config"]
        assert config.output_path == Path("from-api.csv")
        assert config.output_dir == Path("data/from-api")
        assert config.output_path is not None
        assert config.output_dir is not None
        assert Path(config.output_dir) / Path(config.output_path) == Path(
            "data/from-api/from-api.csv"
        )
        assert config.only_true is True
        assert config.limit == 50

    def test_video_specific_export_rejects_non_final_segment_cleanup(self) -> None:
        self.state.segment_annotations_created = True
        self.state.segment_annotations_validated = False
        self.state.save(
            update_fields=[
                "segment_annotations_created",
                "segment_annotations_validated",
            ]
        )

        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    use_export_flags=False,
                ),
                content_type="application/json",
            )

        assert response.status_code == 409, response.content
        assert "config" not in captured
        assert "cleanup_required" in response.json()["error"]

    def test_video_specific_export_allows_final_segment_cleanup(self) -> None:
        self.state.segment_annotations_created = True
        self.state.segment_annotations_validated = True
        self.state.save(
            update_fields=[
                "segment_annotations_created",
                "segment_annotations_validated",
            ]
        )

        captured: dict[str, export_config] = {}

        with patch(
            "endoreg_db.services.export_annotated.annotation_exporter_client",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(
                    use_export_flags=False,
                ),
                content_type="application/json",
            )

        assert response.status_code == 200, response.content
        assert captured["config"].video_id == self.video.pk
