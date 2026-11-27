"""A real Full Export can be restored in a local database."""

import csv
import io
import json
import subprocess
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from endoreg_db.export.frames.export_frames_with_labels import (
    AnnotationExporterClient,
    export_config,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.datasets import annotation_import
from endoreg_db.services.datasets.annotation_archive import open_export
from endoreg_db.services.media.state import update_media_state
from endoreg_db.services.streaming.hls_media import get_ready_hls_artifact
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.services.video_storage.workflow import persist_video_source_timeline
from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.rust_backend import is_lx_encrypted_file
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.video.executable_discovery import resolve_ffmpeg_executable


@pytest.fixture
def full_export() -> tuple[Center, Path, str]:
    center = Center.objects.create(name="offline-annotation-study")
    workspace = ensure_directory(get_runtime_paths().migration_staging / uuid4().hex)
    media = workspace / "source.mp4"
    ffmpeg = resolve_ffmpeg_executable()
    if ffmpeg is None:
        pytest.skip("ffmpeg is not available")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=5:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(media),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    digest = get_file_hash(media)
    video = VideoFile(
        center=center,
        raw_video_hash=digest,
        processed_video_hash=digest,
        suffix=".mp4",
    )
    save_local_file(video.processed_file, media, name=f"{uuid4().hex}.mp4")
    video.save()
    persist_video_source_timeline(
        video, media, artifact_kind=VideoArtifactKind.PROCESSED
    )
    frame = video.frames.get(frame_number=1)
    label = Label.objects.create(name="offline-export-lesion")
    information_source = InformationSource.objects.create(name="offline-export-review")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        source=information_source,
        start_frame_number=1,
        end_frame_number=3,
    )
    ImageClassificationAnnotation.objects.bulk_create(
        [
            ImageClassificationAnnotation(
                frame=frame,
                label=label,
                information_source=information_source,
                annotator=reviewer,
                value=value,
                float_value=score,
            )
            for reviewer, value, score in (("one", True, 0.8), ("two", False, None))
        ]
    )
    update_media_state(
        video.get_or_create_state(),
        changes={
            "processing_started": True,
            "anonymized": True,
            "sensitive_meta_processed": True,
            "anonymization_validated": True,
            "processed_file_sha256": digest,
        },
    )
    run = workspace / "full_export"
    AnnotationExporterClient().run_export(
        export_config(
            output_dir=run,
            output_path="annotations.csv",
            output_format="csv",
            export_profile="pts_dataset_v1",
            use_export_flags=False,
            export_frames=True,
            export_videos=True,
            video_id=video.pk,
        )
    )
    VideoFile.objects.filter(pk=video.pk).delete()
    return center, run, digest


def _convert(center: Center, path: Path, *, apply: bool = False) -> dict[str, object]:
    output = io.StringIO()
    call_command(
        "import_annotation_export",
        str(path),
        center_key=center.center_key,
        apply=apply,
        stdout=output,
    )
    return json.loads(output.getvalue())


@pytest.mark.django_db(transaction=True)
@override_settings(ENDOREG_DEPLOYMENT_ROLE="standalone")
def test_full_export_roundtrip_and_zip_replay(
    full_export: tuple[Center, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center, run, digest = full_export
    assert _convert(center, run) == {
        "applied": False,
        "videos": 1,
        "annotations": 2,
        "segments": 1,
        "reused_videos": 0,
    }
    assert not VideoFile.objects.filter(raw_video_hash=digest).exists()
    archive_path = run.with_suffix(".zip")
    with ZipFile(archive_path, "w") as archive:
        for member in sorted(run.rglob("*")):
            if member.is_file():
                archive.write(member, member.relative_to(run.parent).as_posix())

    def streaming_unavailable(_video_id: int) -> None:
        raise VideoStorageNormalizationError("streaming temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(annotation_import, "materialize_video_hls", streaming_unavailable)
        with pytest.raises(CommandError, match="streaming temporarily unavailable"):
            _convert(center, archive_path, apply=True)
    video = VideoFile.objects.get(raw_video_hash=digest)
    assert _convert(center, archive_path, apply=True)["reused_videos"] == 1
    assert video.processed_video_hash == digest
    assert not video.raw_file
    assert is_lx_encrypted_file(Path(video.processed_file.path))
    assert get_file_hash(video.processed_file) == digest
    assert video.frames.count() == 5
    assert not video.frames.filter(is_extracted=True).exists()
    restored = ImageClassificationAnnotation.objects.filter(frame__video=video)
    reviewers = set(restored.values_list("annotator", flat=True))
    assert len(reviewers) == 2
    assert all(
        isinstance(name, str) and name.startswith("offline_export:")
        for name in reviewers
    )
    assert set(restored.values_list("value", "float_value")) == {
        (True, 0.8),
        (False, None),
    }
    segment = LabelVideoSegment.objects.get(video_file=video)
    assert (segment.start_frame_number, segment.end_frame_number) == (1, 3)
    assert (
        get_ready_hls_artifact(video=video, artifact_kind=VideoArtifactKind.PROCESSED)
        is not None
    )
    assert _convert(center, run, apply=True)["reused_videos"] == 1
    assert restored.count() == 2
    other = Center.objects.create(name="other-offline-study")
    with pytest.raises(CommandError, match="another center"):
        _convert(other, run, apply=True)


@pytest.mark.django_db(transaction=True)
@override_settings(ENDOREG_DEPLOYMENT_ROLE="standalone")
@pytest.mark.parametrize("tamper", ["coordinate", "media"])
def test_tampered_frame_coordinate_is_rejected(
    full_export: tuple[Center, Path, str],
    tamper: str,
) -> None:
    center, run, digest = full_export
    if tamper == "coordinate":
        table = run / "annotations.csv"
        with table.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headings = reader.fieldnames
            rows = list(reader)
        assert headings is not None
        rows[0]["presentation_timestamp"] = "0"
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headings)
            writer.writeheader()
            writer.writerows(rows)
        error = "canonical ordinal"
    else:
        [media] = (run / "videos").glob("*.mp4")
        media.write_bytes(b"changed")
        error = "video hash differs from manifest"
    with pytest.raises(CommandError, match=error):
        _convert(center, run, apply=True)
    assert not VideoFile.objects.filter(raw_video_hash=digest).exists()


@pytest.mark.no_db
def test_zip_rejects_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("../video.mp4", b"unsafe")
    with pytest.raises(ValueError), open_export(path):
        pytest.fail("Unsafe archive was opened")
