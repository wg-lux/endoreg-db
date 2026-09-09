"""Central-hub users retain processed training data after raw-video cleanup."""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch
from typing import Protocol, cast
from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.db.models.fields.files import FieldFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from endoreg_db.helpers.typing import m2m_add_relation
from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    PortalUserInfo,
    VideoFile,
    VideoState,
)
from endoreg_db.services.frames.materialize_training_frames import (
    materialize_frames_for_annotation_ids,
)
from endoreg_db.utils.file_operations import safe_rmtree
from endoreg_db.utils.storage.files import delete_field_file

pytestmark = pytest.mark.django_db


class _BackwardLoss(Protocol):
    def backward(self) -> None: ...


class _OptimizerStep(Protocol):
    def step(self) -> None: ...


@pytest.fixture
def retained_video(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[VideoFile, AIDataSet, LabelSet, int]:
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    monkeypatch.setenv(
        "LX_ANNOTATE_MASTER_KEY", base64.urlsafe_b64encode(b"0" * 32).decode()
    )
    monkeypatch.delenv("LX_ANNOTATE_MASTER_KEY_FILE", raising=False)
    # Generate a real, non-clinical video so decoding and training use actual media.
    media = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:r=25",
            "-frames:v",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "frag_keyframe+empty_moov",
            "-f",
            "mp4",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    center = Center.objects.create(name="Hub source", center_key="hub-source")
    digest = hashlib.sha256(media).hexdigest()
    state = VideoState.objects.create(
        anonymized=True,
        sensitive_meta_processed=True,
        anonymization_validated=True,
        processing_started=True,
        frames_extracted=True,
        outside_segments_removed=True,
        segment_annotations_created=True,
        segment_annotations_validated=True,
        ready_for_export=True,
        ready_for_export_at=timezone.now(),
        ready_for_export_by="test-suite",
        processed_file_sha256=digest,
    )
    video = VideoFile.objects.create(
        center=center,
        state=state,
        video_hash=digest,
        processed_video_hash=digest,
        fps=25.0,
        frame_count=2,
        duration=0.08,
        width=64,
        height=48,
        raw_file=ContentFile(media, name="hub-source.mp4"),
        processed_file=ContentFile(media, name="hub-processed.mp4"),
    )
    raw_name = video.raw_file.name
    assert raw_name is not None
    assert video.raw_file.storage.exists(raw_name)
    assert delete_field_file(video, "raw_file", missing_ok=False, save=True)
    video.refresh_from_db()
    assert not video.raw_file.name
    assert not video.raw_file.storage.exists(raw_name)
    processed_name = video.processed_file.name
    assert processed_name is not None
    assert video.processed_file.storage.exists(processed_name)
    frame = Frame.objects.create(
        video=video,
        frame_number=0,
        timestamp=0.0,
        relative_path="frame_0000000.jpg",
    )
    label = Label.objects.create(name="hub-training-label")
    label_set = LabelSet.objects.create(name="hub-training-labels", version=1)
    label_set.labels.add(label)
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=label,
        value=True,
        annotator="test-suite",
    )
    dataset = AIDataSet.objects.create(
        name="hub-training",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)
    return video, dataset, label_set, int(annotation.pk)


@pytest.mark.parametrize("membership", ["source", "other", "none"])
@override_settings(DEBUG=False, ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_hub_read_and_training_after_raw_deletion(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    membership: str,
) -> None:
    video, dataset, label_set, _ = retained_video
    user = User.objects.create_user(username=f"hub-reader-{membership}")
    m2m_add_relation(getattr(user, "groups")).add(
        Group.objects.get_or_create(name="video:read")[0]
    )
    portal = PortalUserInfo.objects.create(user=user)
    if membership == "source":
        portal.centers.add(video.center)
    elif membership == "other":
        portal.centers.add(Center.objects.create(name="Other", center_key="other"))
    client = APIClient()
    client.force_authenticate(user)
    listing = client.get("/api/media/videos/", secure=True)
    assert listing.status_code == 200, listing.content
    assert video.pk in {row["id"] for row in listing.json()["results"]}
    detail = client.get(f"/api/media/videos/{video.pk}/details/", secure=True)
    assert detail.status_code == 200, detail.content
    if membership != "source":
        assert "original_file_name" not in detail.json()
        assert "patient_first_name" not in detail.json()
    playback = client.get(f"/api/media/videos/{video.pk}/stream/", secure=True)
    assert playback.status_code == 302, playback.content
    assert "type=processed" in playback["Location"]
    decoded = client.get(
        f"/api/media/videos/{video.pk}/frames/0/decoded-stream/?type=processed",
        secure=True,
    )
    assert decoded.status_code == 200, decoded.content
    assert decoded["Content-Type"] == "image/jpeg"
    assert decoded.content.startswith(b"\xff\xd8")
    manifest = client.post(
        f"/api/settings/application/ai_datasets/{dataset.pk}/training_manifest/",
        {"label_set_id": label_set.pk},
        format="json",
        secure=True,
    )
    assert manifest.status_code == 200, manifest.content
    sample = manifest.json()["lx_ai_core_manifest"]["samples"][0]
    assert sample["frame_stream"] == {
        "video_id": video.pk,
        "frame_number": 0,
        "artifact_kind": "processed",
    }
    assert "path" not in sample
    assert "relative_path" not in sample["metadata"]


@override_settings(DEBUG=False, ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_training_decodes_retained_processed_video(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    tmp_path: Path,
) -> None:
    video, _, _, annotation_id = retained_video
    output = tmp_path / "training"
    try:
        frames = materialize_frames_for_annotation_ids(
            annotation_ids=[annotation_id],
            output_root=output,
            fps=25.0,
        )
        with Image.open(frames[annotation_id]) as image:
            assert image.size == (64, 48)
        assert not video.raw_file.name
    finally:
        safe_rmtree(output, missing_ok=True)
    assert not output.exists()


@pytest.mark.parametrize("role", ["standalone", "site_node", "local_study_server"])
@override_settings(DEBUG=False)
def test_raw_deletion_does_not_unscope_non_hub_reads(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    role: str,
) -> None:
    video, _, _, _ = retained_video
    user = User.objects.create_user(username="non-hub-reader")
    m2m_add_relation(getattr(user, "groups")).add(
        Group.objects.get_or_create(name="video:read")[0]
    )
    portal = PortalUserInfo.objects.create(user=user)
    portal.centers.add(Center.objects.create(name="Other", center_key="other"))
    client = APIClient()
    client.force_authenticate(user)
    with override_settings(ENDOREG_DEPLOYMENT_ROLE=role):
        response = client.get("/api/media/videos/", secure=True)
        assert response.status_code == 200
        assert response.json()["results"] == []
        detail = client.get(f"/api/media/videos/{video.pk}/details/", secure=True)
        assert detail.status_code in (403, 404)


@override_settings(DEBUG=False, ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_hub_access_does_not_grant_anonymous_or_raw_or_write_access(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    video, dataset, label_set, _ = retained_video
    client = APIClient()
    assert client.get("/api/media/videos/", secure=True).status_code in (401, 403)
    assert client.post(
        f"/api/settings/application/ai_datasets/{dataset.pk}/training_manifest/",
        {"label_set_id": label_set.pk},
        format="json",
        secure=True,
    ).status_code in (401, 403)
    user = User.objects.create_user(username="foreign-reader")
    m2m_add_relation(getattr(user, "groups")).add(
        Group.objects.get_or_create(name="video:write")[0]
    )
    client.force_authenticate(user)
    assert client.patch(
        f"/api/media/videos/{video.pk}/details/",
        {"export_segments_by_video": True},
        format="json",
        secure=True,
    ).status_code in (403, 404)
    assert client.get(
        f"/api/media/videos/{video.pk}/hls/playlist.m3u8?type=raw",
        secure=True,
    ).status_code in (403, 404)


@override_settings(DEBUG=False, ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_pytorch_training_reads_processed_frame_without_cache(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
) -> None:
    from endoreg_db.utils.ai.multilabel_dataset_builder import (
        build_dataset_for_training,
    )
    from endoreg_db.utils.ai.model_training.dataset import EndoMultiLabelDataset

    _, dataset, _, _ = retained_video
    data = build_dataset_for_training(dataset)
    training = EndoMultiLabelDataset(
        data["image_paths"],
        data["label_vectors"],
        data["label_masks"],
        frame_ids=data["frame_ids"],
    )
    image, labels, mask = training[0]
    assert tuple(image.shape) == (3, 224, 224)
    assert tuple(labels.shape) == (1,) and labels[0].item() == 1.0
    assert tuple(mask.shape) == (1,) and mask[0].item() == 1.0


@pytest.mark.parametrize(
    "gate",
    ["anonymization_validated", "segment_annotations_validated", "ready_for_export"],
)
@override_settings(DEBUG=False, ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_virtual_training_frames_require_validation(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    gate: str,
) -> None:
    from endoreg_db.services.aidataset_training_manifests import (
        build_frame_multilabel_training_manifest,
    )

    video, dataset, label_set, _ = retained_video
    state = video.state
    assert state is not None
    setattr(state, gate, False)
    state.ready_for_export = False
    state.save(update_fields=list({gate, "ready_for_export"}))
    with pytest.raises(
        ValueError, match="no extracted or validated processed frame annotations"
    ):
        build_frame_multilabel_training_manifest(dataset, label_set=label_set)


@pytest.mark.parametrize("damage", ["wrong_key", "tampered"])
@pytest.mark.parametrize("consumer", ["image", "stream", "report"])
def test_training_rejects_encrypted_media_damage_without_plaintext_leaks(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
    consumer: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from endoreg_db.utils.encryption.encrypted import EncryptedStorage
    from endoreg_db.services.frames.training_images import read_processed_training_image
    from endoreg_db.utils.file_operations import atomic_write_file

    video, _, _, annotation_id = retained_video
    frame = ImageClassificationAnnotation.objects.get(pk=annotation_id).frame
    # Raw filesystem reads here inspect ciphertext only, never a parser input.
    encrypted_path = Path(video.processed_file.path)
    ciphertext = encrypted_path.read_bytes()
    assert ciphertext.startswith(b"LXENC01")
    if damage == "wrong_key":
        frame.video.processed_file.storage = EncryptedStorage(
            location=video.processed_file.storage.path(""),
            master_key=b"1" * 32,
        )
    else:
        damaged = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        atomic_write_file(destination=encrypted_path, content=[damaged])
    before = set(Path("/tmp").glob("endoreg-fieldfile-*"))
    with pytest.raises(
        (ValueError, OSError, RuntimeError),
        match="decode failed" if consumer == "report" else "authentication|decrypt|key",
    ):
        if consumer == "image":
            read_processed_training_image(frame)
        elif consumer == "report":
            from endoreg_db.services.report_frame_export import (
                materialized_report_frame,
            )

            with materialized_report_frame(frame):
                pytest.fail("Damaged media must not produce a report image")
        else:
            from lx_ai_core.training import ProcessedFrameReference
            from endoreg_db.services.frames.training_images import (
                ProcessedTrainingFrameProvider,
            )

            provider = ProcessedTrainingFrameProvider(allowed_frame_ids=[int(frame.pk)])
            with patch.object(Frame.objects, "select_related") as query:
                query.return_value.get.return_value = frame
                with provider.open_frame(
                    ProcessedFrameReference(
                        video_id=video.pk, frame_number=frame.frame_number
                    )
                ) as stream:
                    stream.read(1)
    assert set(Path("/tmp").glob("endoreg-fieldfile-*")) == before

    if consumer == "report":
        assert "authentication failed" in capsys.readouterr().err


@pytest.mark.parametrize("consumer", ["image", "stream"])
def test_training_plaintext_is_private_and_removed_after_decoder_error(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
) -> None:
    from endoreg_db.services.frames import training_images
    from endoreg_db.utils.frame_stream import EncodedFrameSample

    _, _, _, annotation_id = retained_video
    frame = ImageClassificationAnnotation.objects.get(pk=annotation_id).frame
    seen: list[Path] = []

    def fail_decoder(
        path: Path,
        *,
        frame_number: int,
        timestamp: float | None,
    ) -> EncodedFrameSample:
        assert frame_number == 0
        assert timestamp == 0.0
        assert path.stat().st_mode & 0o777 == 0o600
        assert not path.read_bytes().startswith(b"LXENC01")
        seen.append(path)
        raise RuntimeError("decoder failure")

    monkeypatch.setattr(training_images, "read_video_path_frame_jpeg", fail_decoder)
    with pytest.raises(RuntimeError, match="decoder failure"):
        if consumer == "image":
            training_images.read_processed_training_image(frame)
        else:
            from lx_ai_core.training import ProcessedFrameReference

            provider = training_images.ProcessedTrainingFrameProvider(
                allowed_frame_ids=[int(frame.pk)]
            )
            with provider.open_frame(
                ProcessedFrameReference(
                    video_id=frame.video.pk, frame_number=frame.frame_number
                )
            ):
                pytest.fail("decoder error did not propagate")
    assert seen and all(not path.exists() for path in seen)


def test_core_training_streams_encrypted_retained_frames(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
) -> None:
    import torch
    from lx_ai_core.training import TrainingDatasetManifest
    from endoreg_db.services.aidataset_training_manifests import (
        build_frame_multilabel_training_manifest,
    )
    from endoreg_db.services.frames.training_images import streamed_training_dataset
    from endoreg_db.utils.frame_stream import read_video_path_frame_jpeg

    video, dataset, label_set, annotation_id = retained_video
    frame_id = int(ImageClassificationAnnotation.objects.get(pk=annotation_id).frame.pk)
    exported = build_frame_multilabel_training_manifest(
        dataset, label_set=label_set, check_frame_format=False
    )
    manifest = TrainingDatasetManifest.model_validate(exported.to_lx_ai_core_dict())
    before = set(Path("/tmp").glob("endoreg-fieldfile-*"))
    with patch(
        "endoreg_db.services.frames.training_images.read_video_path_frame_jpeg",
        wraps=read_video_path_frame_jpeg,
    ) as decode:
        training = streamed_training_dataset(manifest, allowed_frame_ids=[frame_id])
        decode.assert_not_called()
        model = torch.nn.Conv2d(3, 1, 1)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
        initial_bias = model.bias.detach().clone() if model.bias is not None else None
        for _ in range(2):
            item = training[0]
            assert tuple(item["image"].shape) == (3, 48, 64)
            assert torch.equal(item["labels"], torch.tensor([1.0]))
            optimizer.zero_grad()
            logits = model(item["image"].unsqueeze(0)).mean().reshape(1)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                logits, item["labels"]
            )
            cast(_BackwardLoss, loss).backward()
            cast(_OptimizerStep, optimizer).step()
        assert decode.call_count == 2
        assert model.bias is not None and initial_bias is not None
        assert not torch.equal(model.bias, initial_bias)
    assert set(Path("/tmp").glob("endoreg-fieldfile-*")) == before
    assert not video.raw_file.name


def test_stream_provider_rejects_unselected_frames_and_rechecks_validation(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
) -> None:
    from lx_ai_core.training import ProcessedFrameReference
    from endoreg_db.services.frames.training_images import (
        ProcessedTrainingFrameProvider,
    )

    video, _, _, annotation_id = retained_video
    frame_id = int(ImageClassificationAnnotation.objects.get(pk=annotation_id).frame.pk)
    reference = ProcessedFrameReference(video_id=video.pk, frame_number=0)
    unauthorized = ProcessedTrainingFrameProvider(allowed_frame_ids=[frame_id + 1])
    with pytest.raises(Frame.DoesNotExist):
        with unauthorized.open_frame(reference):
            pytest.fail("unselected frame was streamed")
    provider = ProcessedTrainingFrameProvider(allowed_frame_ids=[frame_id])
    with provider.open_frame(reference) as stream:
        assert stream.read(2) == b"\xff\xd8"
    state = video.state
    assert state is not None
    VideoState.objects.filter(pk=state.pk).update(ready_for_export=False)
    with pytest.raises(ValueError, match="validated, export-ready"):
        with provider.open_frame(reference):
            pytest.fail("revoked processed video was streamed")


def test_stream_provider_closes_after_consumer_error(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
) -> None:
    from lx_ai_core.training import ProcessedFrameReference
    from endoreg_db.services.frames.training_images import (
        ProcessedTrainingFrameProvider,
    )

    video, _, _, annotation_id = retained_video
    frame_id = int(ImageClassificationAnnotation.objects.get(pk=annotation_id).frame.pk)
    provider = ProcessedTrainingFrameProvider(allowed_frame_ids=[frame_id])
    before = set(Path("/tmp").glob("endoreg-fieldfile-*"))
    streams: list[BytesIO] = []
    with pytest.raises(RuntimeError, match="consumer failed"):
        with provider.open_frame(
            ProcessedFrameReference(video_id=video.pk, frame_number=0)
        ) as stream:
            assert isinstance(stream, BytesIO)
            streams.append(stream)
            assert set(Path("/tmp").glob("endoreg-fieldfile-*")) == before
            raise RuntimeError("consumer failed")
    assert len(streams) == 1 and streams[0].closed


def test_stream_provider_does_not_yield_after_cleanup_failure(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lx_ai_core.training import ProcessedFrameReference
    from endoreg_db.services.frames import training_images

    video, _, _, annotation_id = retained_video
    frame_id = int(ImageClassificationAnnotation.objects.get(pk=annotation_id).frame.pk)
    original = training_images.materialized_plaintext_field_file
    paths: list[Path] = []

    @contextmanager
    def failed_cleanup(field_file: FieldFile, *, suffix: str) -> Generator[Path]:
        with original(field_file, suffix=suffix) as path:
            paths.append(path)
            yield path
        raise OSError("cleanup failure")

    monkeypatch.setattr(
        training_images, "materialized_plaintext_field_file", failed_cleanup
    )
    provider = training_images.ProcessedTrainingFrameProvider(
        allowed_frame_ids=[frame_id]
    )
    with pytest.raises(OSError, match="cleanup failure"):
        with provider.open_frame(
            ProcessedFrameReference(video_id=video.pk, frame_number=0)
        ):
            pytest.fail("stream was exposed before cleanup completed")
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("renderer_fails", [False, True])
def test_report_frame_export_decrypts_and_cleans_up(
    retained_video: tuple[VideoFile, AIDataSet, LabelSet, int],
    renderer_fails: bool,
) -> None:
    from endoreg_db.services.report_frame_export import materialized_report_frame

    video, _, _, annotation_id = retained_video
    frame = ImageClassificationAnnotation.objects.get(pk=annotation_id).frame
    assert Path(video.processed_file.path).read_bytes().startswith(b"LXENC01")
    before = set(Path("/tmp").glob("endoreg-report-frame-*"))
    paths: list[Path] = []

    def consume() -> None:
        with materialized_report_frame(frame) as path:
            paths.append(path)
            assert path.read_bytes().startswith(b"\xff\xd8")
            assert path.stat().st_mode & 0o777 == 0o600
            if renderer_fails:
                raise RuntimeError("renderer failed")

    if renderer_fails:
        with pytest.raises(RuntimeError, match="renderer failed"):
            consume()
    else:
        consume()
    assert paths and all(not path.exists() for path in paths)
    assert set(Path("/tmp").glob("endoreg-report-frame-*")) == before
    frame.refresh_from_db()
    assert not frame.is_extracted
