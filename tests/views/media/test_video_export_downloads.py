from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.http import HttpResponseBase, StreamingHttpResponse
from django.test import TestCase, override_settings

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
from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimelineHistoryEntry,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.media.operation_gate import video_artifact_mutation
from endoreg_db.services.media.export_downloads import leased_video_bytes
from endoreg_db.services.media.operation_gate import create_video_stream_lease
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import (
    get_runtime_paths,
    resolve_existing_protected_media_path,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.exceptions import MediaOperationDeferred


def download_body(response: HttpResponseBase) -> bytes:
    streaming = cast(StreamingHttpResponse, response)
    return b"".join(cast(Iterable[bytes], streaming.streaming_content))


class VideoExportDownloadTests(TestCase):
    video: VideoFile
    state: VideoState
    frame: Frame
    user: User
    center: Center

    def setUp(self) -> None:
        suffix = uuid4().hex
        self.center = Center.objects.create(name=f"download-{suffix}")
        self.user = User.objects.create_user(username=f"download-{suffix}")
        self.user.groups.add(Group.objects.get_or_create(name="video:read")[0])  # pyright: ignore[reportUnknownMemberType]
        PortalUserInfo.objects.create(user=self.user).centers.add(self.center)
        self.client.force_login(self.user)
        self.state = VideoState.objects.create()
        timeline = VideoSourceTimelineEvidence(
            persisted_at=datetime.now(UTC),
            timestamp_mapping="ffprobe_pts",
            source=VideoArtifactProbe(
                codec_name="h264",
                pixel_format="yuv420p",
                width=640,
                height=480,
                size_bytes=12,
                timeline=VideoTimelineContract(
                    fps_num=25,
                    fps_den=1,
                    duration_seconds=2.0,
                    frame_count=8,
                    variable_frame_rate=True,
                    time_base_num=1,
                    time_base_den=90000,
                ),
            ),
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            state=self.state,
            raw_video_hash=suffix,
            meta={"source_timeline": timeline.model_dump(mode="json")},
        )
        self.video.processed_file.save(
            f"{suffix}.mp4", ContentFile(b"processed-video"), save=True
        )
        self.video.processed_video_hash = get_file_hash(self.video.processed_file)
        self.video.save(update_fields=["processed_video_hash"])
        self.state.refresh_from_db()
        self.state.anonymization_validated = True
        self.state.processed_file_sha256 = self.video.processed_video_hash
        self.state.save()
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=7,
            timestamp=1.6024888888888889,
            presentation_timestamp=144224,
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame,
            label=Label.objects.create(name=f"label-{suffix}"),
            value=True,
            information_source=InformationSource.objects.create(
                name=f"manual-{suffix}"
            ),
            annotator="=not_a_formula",
        )

    def url(self, path: str = "download/") -> str:
        return f"/api/media/videos/{self.video.pk}/{path}"

    @override_settings(DEBUG=False)
    def test_authenticated_download_streams_processed_bytes(self) -> None:
        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            response = self.client.get(self.url())
        assert response.status_code == 200, (
            response.content if not response.streaming else ""
        )
        assert (
            response["Content-Disposition"]
            == f'attachment; filename="video_{self.video.pk}_anonymisiert.mp4"'
        )
        assert response["Cache-Control"] == "private, no-store"
        assert download_body(response) == b"processed-video"
        assert self.video.processed_file.name is not None
        path = resolve_existing_protected_media_path(self.video.processed_file.name)
        assert path is not None
        assert path.read_bytes().startswith(b"LXENC01")

    def test_range_download_and_invalid_range(self) -> None:
        response = self.client.get(self.url(), HTTP_RANGE="bytes=2-5")
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 2-5/15"
        assert download_body(response) == b"oces"
        response = self.client.get(self.url(), HTTP_RANGE="bytes=100-200")
        assert response.status_code == 416

    def test_resume_from_another_generation_restarts_the_complete_download(
        self,
    ) -> None:
        for validator, status, body in (
            (f'"{self.video.processed_video_hash}"', 206, b"oces"),
            ('"previous-generation"', 200, b"processed-video"),
        ):
            response = self.client.get(
                self.url(), HTTP_RANGE="bytes=2-5", HTTP_IF_RANGE=validator
            )
            assert response.status_code == status
            assert response["ETag"] == f'"{self.video.processed_video_hash}"'
            assert download_body(response) == body

    def test_annotation_csv_contains_exact_coordinates(self) -> None:
        response = self.client.get(self.url("annotations.csv"))
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(download_body(response).decode())))
        assert len(rows) == 1
        assert rows[0]["frame_number"] == "7"
        assert rows[0]["presentation_timestamp"] == "144224"
        assert rows[0]["stream_time_base_den"] == "90000"
        assert rows[0]["artifact_sha256"] == self.video.processed_video_hash
        assert rows[0]["annotator"] == "'=not_a_formula"

    def test_missing_timestamp_fails_before_csv_success(self) -> None:
        self.frame.presentation_timestamp = None
        self.frame.save(update_fields=["presentation_timestamp"])
        assert self.client.get(self.url("annotations.csv")).status_code == 409

    def test_unvalidated_failed_and_missing_processed_media_are_blocked(self) -> None:
        self.state.anonymization_validated = False
        self.state.save()
        assert self.client.get(self.url()).status_code == 409
        self.state.anonymization_validated = True
        self.state.processing_error = True
        self.state.save()
        assert self.client.get(self.url()).status_code == 409
        self.state.processing_error = False
        self.state.save()
        VideoFile.objects.filter(pk=self.video.pk).update(processed_file="")
        assert self.client.get(self.url()).status_code == 409

    def test_hash_mismatch_is_blocked(self) -> None:
        VideoFile.objects.filter(pk=self.video.pk).update(processed_video_hash="0" * 64)
        assert self.client.get(self.url()).status_code == 409

    def test_readiness_proof_must_belong_to_the_processed_generation(self) -> None:
        self.state.processed_file_sha256 = "0" * 64
        self.state.save(update_fields=["processed_file_sha256"])
        assert self.client.get(self.url("annotations.csv")).status_code == 409

    def test_missing_state_is_rejected_without_creating_one(self) -> None:
        VideoFile.objects.filter(pk=self.video.pk).update(state=None)
        before = VideoState.objects.count()
        assert self.client.get(self.url()).status_code == 409
        assert VideoState.objects.count() == before

    def test_anonymous_download_is_blocked_even_in_debug(self) -> None:
        self.client.logout()
        for path in ("download/", "annotations.csv"):
            assert self.client.get(self.url(path)).status_code in {401, 403}

    @override_settings(DEBUG=False)
    def test_wrong_center_and_missing_role_are_blocked(self) -> None:
        with patch(
            "endoreg_db.views.access_control._allowed_center_ids",
            return_value=frozenset(),
        ):
            assert self.client.get(self.url()).status_code == 404
        self.user.groups.clear()  # pyright: ignore[reportUnknownMemberType]
        with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
            assert self.client.get(self.url()).status_code == 403

    def test_writer_blocks_download(self) -> None:
        with video_artifact_mutation(video_id=int(self.video.pk)):
            assert self.client.get(self.url()).status_code == 409

    def test_invalid_options_are_rejected(self) -> None:
        for query in ({"use_export_flags": "perhaps"}, {"file_type": "raw"}):
            assert self.client.get(self.url(), query).status_code == 400

    def test_original_and_processed_timestamp_sequences_remain_distinct(self) -> None:
        original_hash = "a" * 64
        entry = CanonicalTimelineHistoryEntry(
            artifact_kind="processed",
            before=CanonicalFrameTimeline(
                content_hash=original_hash,
                time_base_num=1,
                time_base_den=1000,
                presentation_timestamps=[5000, 5021, 5053],
            ),
            after=CanonicalFrameTimeline(
                content_hash=self.video.processed_video_hash,
                time_base_num=1,
                time_base_den=90000,
                presentation_timestamps=[0, 3600],
            ),
        )
        self.video.raw_video_hash = original_hash
        self.video.meta = {
            **(self.video.meta or {}),
            "canonical_timeline_history": [entry.model_dump(mode="json")],
        }
        self.video.save(update_fields=["raw_video_hash", "meta"])
        for timeline, expected in (
            ("original", ["5000", "5021", "5053"]),
            ("processed", ["0", "3600"]),
        ):
            response = self.client.get(
                self.url("timestamps.csv"), {"timeline": timeline}
            )
            assert response.status_code == 200
            rows = list(csv.DictReader(io.StringIO(download_body(response).decode())))
            assert [row["presentation_timestamp"] for row in rows] == expected
            assert [row["frame_index"] for row in rows] == [
                str(index) for index in range(len(expected))
            ]
        self.frame.refresh_from_db()
        assert self.frame.presentation_timestamp == 144224
        self.video.raw_video_hash = "b" * 64
        self.video.save(update_fields=["raw_video_hash"])
        assert (
            self.client.get(
                self.url("timestamps.csv"), {"timeline": "original"}
            ).status_code
            == 409
        )

    def test_missing_original_timestamps_are_not_reconstructed(self) -> None:
        response = self.client.get(self.url("timestamps.csv"), {"timeline": "original"})
        assert response.status_code == 409

    def test_download_lease_blocks_generation_replacement(self) -> None:
        response = self.client.get(self.url())
        assert response.status_code == 200
        with self.assertRaises(MediaOperationDeferred):
            with video_artifact_mutation(video_id=int(self.video.pk)):
                self.fail("Download lease must block canonical replacement")
        assert download_body(response) == b"processed-video"

    def test_long_download_renews_lease_and_detects_generation_change(self) -> None:
        with (
            patch(
                "endoreg_db.services.media.export_downloads.monotonic",
                side_effect=[0, 0, 11],
            ),
            patch(
                "endoreg_db.services.media.export_downloads.create_video_stream_lease",
                wraps=create_video_stream_lease,
            ) as renew,
        ):
            stream = leased_video_bytes(self.video, iter([b"first", b"second"]))
            assert next(stream) == b"first"
            VideoFile.objects.filter(pk=self.video.pk).update(
                processed_video_hash="0" * 64
            )
            with self.assertRaisesRegex(ValueError, "Videogeneration"):
                next(stream)
            assert renew.call_count == 2

    def test_wrong_key_rejects_encrypted_download(self) -> None:
        storage = EncryptedStorage(
            location=get_runtime_paths().storage, master_key=b"x" * 32
        )
        with patch.object(self.video.processed_file.field, "storage", storage):
            response = self.client.get(self.url())
        assert response.status_code == 409

    def test_tampered_ciphertext_rejects_download(self) -> None:
        assert self.video.processed_file.name is not None
        path = resolve_existing_protected_media_path(self.video.processed_file.name)
        assert path is not None
        ciphertext = bytearray(path.read_bytes())
        ciphertext[-1] ^= 1
        atomic_write_file(destination=path, content=[bytes(ciphertext)])
        assert self.client.get(self.url()).status_code == 409
