from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from django.test.utils import override_settings
from lx_dtypes.models.contracts.video_frame_export import export_config

from endoreg_db.models import (
    Center,
    Frame,
    FrameAnnotationExportJob,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    PortalUserInfo,
    SensitiveMeta,
    VideoFile,
    VideoProcessingHistory,
    VideoState,
)
from endoreg_db.schemas.frame_annotation_export import (
    FRAME_ANNOTATION_EXPORT_MANIFEST_NAME,
    FrameAnnotationExportManifest,
)
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.annotations.segment_validation_workflow import (
    blackening_history_config,
)
from endoreg_db.services.video_storage.contracts import evidence_as_json
from endoreg_db.services.video_storage.recording_time import recording_time_evidence
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.file_operations import (
    advisory_file_lock,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths


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


def _export_root() -> Path:
    return get_runtime_paths().frame_annotation_export.resolve()


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
            raw_video_hash=f"export-contract-video-{suffix}",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
        assert config.output_dir == _export_root() / "data/export"
        assert config.export_profile == "pts_dataset_v1"
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
            raw_video_hash=f"foreign-video-{uuid4().hex[:8]}",
        )
        captured: dict[str, export_config] = {}

        with patch(
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
                "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
                "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
                "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
        assert config.output_dir == _export_root() / "data/from-api"
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
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

    def test_video_specific_export_rejects_cleanup_history_despite_validated_flags(
        self,
    ) -> None:
        self.state.segment_annotations_created = True
        self.state.segment_annotations_validated = True
        self.state.save()
        for history_status in (
            VideoProcessingHistory.STATUS_PENDING,
            VideoProcessingHistory.STATUS_RUNNING,
            VideoProcessingHistory.STATUS_FAILURE,
        ):
            with self.subTest(history_status=history_status):
                VideoProcessingHistory.objects.create(
                    video=self.video,
                    operation=VideoProcessingHistory.OPERATION_REPROCESSING,
                    status=history_status,
                    config=blackening_history_config(only_validated=False),
                )
                captured: dict[str, export_config] = {}
                with patch(
                    "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
                    return_value=_FakeExporterClient(captured),
                ):
                    response = self.client.post(
                        "/api/media/videos/export-annotated/",
                        data=self._valid_payload(use_export_flags=False),
                        content_type="application/json",
                    )
                assert response.status_code == 409, response.content
                assert "config" not in captured

    def test_video_specific_export_rejects_validated_flags_without_cleanup(
        self,
    ) -> None:
        self.state.segment_annotations_created = True
        self.state.segment_annotations_validated = True
        self.state.outside_segments_removed = False
        self.state.save()
        captured: dict[str, export_config] = {}
        with patch(
            "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
            return_value=_FakeExporterClient(captured),
        ):
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=self._valid_payload(use_export_flags=False),
                content_type="application/json",
            )
        assert response.status_code == 409, response.content
        assert "config" not in captured

    def test_api_rejects_export_locations_outside_export_root(self) -> None:
        unsafe_locations: list[dict[str, object]] = [
            {"output_dir": "/tmp/endoreg-export"},
            {"output_dir": "data/../../outside"},
            {"output_path": "/tmp/annotations.csv"},
            {"output_path": "../annotations.csv"},
            {"output_path": "export_manifest.json"},
        ]
        for overrides in unsafe_locations:
            with self.subTest(overrides=overrides):
                captured: dict[str, export_config] = {}
                with patch(
                    "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
                    return_value=_FakeExporterClient(captured),
                ):
                    response = self.client.post(
                        "/api/media/videos/export-annotated/",
                        data=self._valid_payload(**overrides),
                        content_type="application/json",
                    )
                assert response.status_code == 400, response.content
                assert "Invalid export location" in str(response.json())
                assert "config" not in captured

    def test_api_rejects_symlink_escape_from_export_root(self) -> None:
        export_root = ensure_directory(_export_root())
        link_name = f"escape-{uuid4().hex[:8]}"
        with TemporaryDirectory() as outside:
            (export_root / link_name).symlink_to(outside, target_is_directory=True)
            self.addCleanup(safe_unlink_file, export_root / link_name, missing_ok=True)
            captured: dict[str, export_config] = {}
            with patch(
                "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
                return_value=_FakeExporterClient(captured),
            ):
                response = self.client.post(
                    "/api/media/videos/export-annotated/",
                    data=self._valid_payload(output_dir=f"{link_name}/nested"),
                    content_type="application/json",
                )
        assert response.status_code == 400, response.content
        assert "Invalid export location" in str(response.json())
        assert "config" not in captured

    def test_api_cannot_replace_active_job_lock_or_its_aliases(self) -> None:
        root = ensure_directory(_export_root())
        suffix = uuid4().hex[:8]
        victim = root / ".locks" / f"reserved-{suffix}.lock"
        self.addCleanup(safe_unlink_file, victim, missing_ok=True)
        directory_alias = root / f"lock-directory-{suffix}"
        directory_alias.symlink_to(root / ".locks", target_is_directory=True)
        self.addCleanup(safe_unlink_file, directory_alias, missing_ok=True)
        run = ensure_directory(root / f"lock-file-{suffix}")
        self.addCleanup(safe_rmtree, run, missing_ok=True)
        file_alias = run / "annotations.csv"
        file_alias.symlink_to(victim)
        with advisory_file_lock(lock_path=victim, timeout_seconds=0):
            original_inode = victim.stat().st_ino
            original_content = victim.read_bytes()
            for output_dir, output_path in (
                (".locks", victim.name),
                (directory_alias.name, victim.name),
                (run.name, file_alias.name),
            ):
                with self.subTest(output_dir=output_dir):
                    captured: dict[str, export_config] = {}
                    with patch(
                        "endoreg_db.services.media.export_annotated.AnnotationExporterClient",
                        return_value=_FakeExporterClient(captured),
                    ):
                        response = self.client.post(
                            "/api/media/videos/export-annotated/",
                            data=self._valid_payload(
                                output_dir=output_dir, output_path=output_path
                            ),
                            content_type="application/json",
                        )
                    assert response.status_code == 400, response.content
                    assert "config" not in captured
                    assert victim.stat().st_ino == original_inode
                    assert victim.read_bytes() == original_content


def _run_media_tool(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout


def _rgb_frame_digests(ffmpeg_executable: str, source: Path) -> list[str]:
    output = _run_media_tool(
        [
            ffmpeg_executable,
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "framemd5",
            "-",
        ]
    )
    return [
        line.rsplit(",", 1)[-1].strip()
        for line in output.splitlines()
        if line and not line.startswith("#")
    ]


def _variable_frame_rate_clip_with_offset(
    ffmpeg_executable: str, work_dir: Path
) -> Path:
    parts: list[Path] = []
    for rate in (30, 24):
        part = work_dir / f"part_{rate}.mp4"
        _run_media_tool(
            [
                ffmpeg_executable,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=96x64:rate={rate}",
                "-frames:v",
                "12",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "5",
                "-video_track_timescale",
                "90000",
                "-y",
                str(part),
            ]
        )
        parts.append(part)
    concat_list = work_dir / "parts.txt"
    concat_list.write_text("".join(f"file '{part}'\n" for part in parts))
    clip = work_dir / "processed.mp4"
    _run_media_tool(
        [
            ffmpeg_executable,
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-output_ts_offset",
            "7.3",
            "-y",
            str(clip),
        ]
    )
    return clip


@pytest.mark.ffmpeg
@pytest.mark.integration
class ExportAnnotatedPresentationTimestampEndToEndTests(TestCase):
    """Run the lx-annotate export request through the real exporter and FFmpeg."""

    def test_frontend_payload_exports_named_video_manifest_and_identical_frames(
        self,
    ) -> None:
        self._assert_real_export(dataset_job=False)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    def test_dataset_job_dispatches_real_task_and_exports_identical_artifacts(
        self,
    ) -> None:
        self._assert_real_export(dataset_job=True)

    def _assert_real_export(self, *, dataset_job: bool) -> None:
        ffmpeg_executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
        ffprobe_executable = ffmpeg_wrapper.resolve_ffprobe_executable()
        if ffmpeg_executable is None or ffprobe_executable is None:
            self.skipTest("ffmpeg and ffprobe are not available")

        with TemporaryDirectory() as work:
            clip = _variable_frame_rate_clip_with_offset(ffmpeg_executable, Path(work))
            clip_bytes = clip.read_bytes()
            reference_digests = _rgb_frame_digests(ffmpeg_executable, clip)
            timestamps = [
                int(value.strip(","))
                for value in _run_media_tool(
                    [
                        ffprobe_executable,
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "frame=best_effort_timestamp",
                        "-of",
                        "csv=p=0",
                        str(clip),
                    ]
                ).split()
            ]
        assert len(set(timestamps)) == 24

        suffix = uuid4().hex[:8]
        center = Center.objects.create(
            name=f"pts-e2e-center-{suffix}", center_key=f"pts-e2e-center-{suffix}"
        )
        state = VideoState.objects.create(
            anonymization_validated=True,
            outside_segments_removed=True,
        )
        timeline = VideoTimelineContract(
            fps_num=27,
            fps_den=1,
            duration_seconds=0.9,
            frame_count=24,
            variable_frame_rate=True,
            time_base_num=1,
            time_base_den=90_000,
        )
        evidence = VideoSourceTimelineEvidence(
            persisted_at=datetime.now(UTC),
            source=VideoArtifactProbe(
                codec_name="h264",
                pixel_format="yuv420p",
                width=96,
                height=64,
                size_bytes=len(clip_bytes),
                timeline=timeline,
            ),
            timestamp_mapping="ffprobe_pts",
        )
        video = VideoFile.objects.create(
            center=center,
            state=state,
            raw_video_hash=f"pts-e2e-video-{suffix}",
            # The import name identifies the patient and must not be exported.
            original_file_name="Mustermann_Max_coloscopy.mp4",
            sensitive_meta=SensitiveMeta.objects.create(
                center=center,
                patient_first_name="Max",
                patient_last_name="Mustermann",
            ),
            fps=timeline.fps,
            duration=timeline.duration_seconds,
            frame_count=timeline.frame_count,
            meta={
                "source_timeline": evidence_as_json(evidence),
                "recording_time": recording_time_evidence(
                    recording_hash=f"pts-e2e-video-{suffix}",
                    recording_started_at=datetime(2024, 2, 15, 9, 30, 5, tzinfo=UTC),
                    duration_seconds=timeline.duration_seconds,
                ).model_dump(mode="json"),
            },
        )
        video.processed_file.save(
            f"pts-e2e-{suffix}.mp4", ContentFile(clip_bytes), save=False
        )
        stored_name = video.processed_file.name
        assert stored_name is not None
        # The export's media lease correctly blocks field-level deletion here.
        self.addCleanup(video.processed_file.storage.delete, stored_name)
        video.processed_video_hash = get_file_hash(video.processed_file)
        video.save()

        label = Label.objects.create(name=f"pts-e2e-label-{suffix}")
        source = InformationSource.objects.create(name=f"pts-e2e-source-{suffix}")
        selected_indices = [0, 5, 11, 12, 17, 23]
        frames: dict[int, Frame] = {}
        for index in selected_indices:
            frames[index] = Frame.objects.create(
                video=video,
                frame_number=index,
                relative_path=f"frame_{index:07d}.png",
                timestamp=timestamps[index] / 90_000,
                presentation_timestamp=timestamps[index],
            )
            ImageClassificationAnnotation.objects.create(
                frame=frames[index],
                label=label,
                value=True,
                information_source=source,
            )
        segment = LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            source=source,
            start_frame_number=0,
            end_frame_number=24,
        )
        user = User.objects.create_user(username=f"pts-e2e-user-{suffix}")
        PortalUserInfo.objects.create(user=user).centers.add(center)
        self.client.force_login(user)

        output_dir = f"video_{video.pk}_annotated"
        self.addCleanup(safe_rmtree, _export_root() / output_dir, missing_ok=True)
        payload = {
            "output_path": "annotations.json",
            "output_dir": output_dir,
            "output_format": "json",
            "export_profile": "pts_dataset_v1",
            "video_id": video.pk,
            "segment_ids": [segment.pk],
            "use_export_flags": False,
            "export_videos": True,
            "export_frames": True,
            "use_frame_pk_paths": True,
            "transcode_frames": True,
            "transcode_quality": 2,
            "transcode_ext": "png",
        }
        if dataset_job:
            payload["center_key"] = center.center_key
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/media/videos/export-annotated/jobs/start/",
                    data=payload,
                    content_type="application/json",
                )
            assert response.status_code == 202, response.content
            job_id = response.json()["id"]
            job = FrameAnnotationExportJob.objects.get(pk=job_id)
            assert job.celery_task_id
            detail = self.client.get(
                f"/api/media/videos/export-annotated/jobs/{job_id}/"
            )
            assert detail.status_code == 200, detail.content
            assert detail.json()["status"] == "completed", detail.content
            result = detail.json()["result"]
            for key in ("output_path", "frame_output_dir", "video_output_dir"):
                relative = Path(result[key])
                assert not relative.is_absolute()
                result[key] = str(_export_root() / relative)
        else:
            response = self.client.post(
                "/api/media/videos/export-annotated/",
                data=payload,
                content_type="application/json",
            )
            assert response.status_code == 200, response.content
            result = response.json()
        exported_annotations = Path(result["output_path"])
        assert exported_annotations == _export_root() / output_dir / "annotations.json"
        assert result["row_count"] == len(selected_indices)
        assert result["exported_frame_count"] == len(selected_indices)
        assert result["exported_video_count"] == 1

        rows = json.loads(exported_annotations.read_text())
        assert [row["presentation_timestamp"] for row in rows] == [
            timestamps[index] for index in selected_indices
        ]
        assert {row["stream_time_base_den"] for row in rows} == {90_000}
        assert {row["artifact_sha256"] for row in rows} == {video.processed_video_hash}

        frame_dir = Path(result["frame_output_dir"]) / f"video_{video.pk}"
        for row, index in zip(rows, selected_indices, strict=True):
            assert row["frame_id"] == frames[index].pk
            image = frame_dir / row["frame_relative_path"]
            assert _rgb_frame_digests(ffmpeg_executable, image) == [
                reference_digests[index]
            ]

        exported_video = Path(result["video_output_dir"]) / "2024-02-15_09-30-05.mp4"
        assert get_file_hash(exported_video) == video.processed_video_hash
        assert [path.name for path in exported_video.parent.iterdir()] == [
            exported_video.name
        ]

        manifest = FrameAnnotationExportManifest.model_validate_json(
            (
                exported_annotations.parent / FRAME_ANNOTATION_EXPORT_MANIFEST_NAME
            ).read_text()
        )
        assert manifest.annotation_file_name == "annotations.json"
        assert "mustermann" not in manifest.model_dump_json().casefold()
        [video_entry] = manifest.videos
        assert video_entry.video_file_name == exported_video.name
        assert video_entry.video_file_name_source == "recording_started_at"
        assert video_entry.video_file_exported is True
        assert video_entry.recording_started_at == "2024-02-15T09:30:05+00:00"
        # This fixture has no decoder ancestry, so identity cannot be proven.
        assert video_entry.recording_timestamp_identity == "unverified"
        assert video_entry.artifact_sha256 == video.processed_video_hash
        [segment_entry] = manifest.segments
        assert segment_entry.segment_id == segment.pk
        assert (segment_entry.start_frame_number, segment_entry.end_frame_number) == (
            0,
            23,
        )
        # Inclusive boundaries are exactly the first and last annotated row timestamps.
        assert (
            segment_entry.start_presentation_timestamp
            == rows[0]["presentation_timestamp"]
        )
        assert (
            segment_entry.end_presentation_timestamp
            == rows[-1]["presentation_timestamp"]
        )
        assert (
            segment_entry.start_presentation_time_seconds
            == rows[0]["presentation_time_seconds"]
        )
        assert (
            segment_entry.end_presentation_time_seconds
            == rows[-1]["presentation_time_seconds"]
        )
        assert (
            segment_entry.stream_time_base_num,
            segment_entry.stream_time_base_den,
        ) == (rows[0]["stream_time_base_num"], rows[0]["stream_time_base_den"])
