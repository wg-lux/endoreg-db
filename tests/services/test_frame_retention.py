from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.db import connection, transaction

from endoreg_db.helpers.typing import m2m_add_relation
from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.frames import frame_retention
from endoreg_db.services.frames.frame_retention import (
    prune_unused_validated_outside_frames,
)

FRAME_COUNT = 10
THREAD_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _RetentionVideo:
    video: VideoFile
    frames: dict[int, Frame]
    outside_label: Label
    source: InformationSource

    def frame_path(self, frame_number: int) -> Path:
        return self.frames[frame_number].file_path

    def segment(
        self,
        *,
        label: Label | None,
        start: int,
        end: int,
        validated: bool,
    ) -> LabelVideoSegment:
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=label,
            source=self.source,
            start_frame_number=start,
            end_frame_number=end,
        )
        state, _created = segment.get_or_create_state()
        state.is_validated = validated
        state.save()
        return segment

    def annotate(self, frame_number: int) -> ImageClassificationAnnotation:
        return ImageClassificationAnnotation.objects.create(
            frame_id=self.frames[frame_number].pk,
            label=self.outside_label,
            information_source=self.source,
            value=True,
        )

    def extracted_frame_numbers(self) -> set[int]:
        return set(
            Frame.objects.filter(video=self.video, is_extracted=True).values_list(
                "frame_number", flat=True
            )
        )

    def frame_numbers_with_files(self) -> set[int]:
        return {
            frame_number
            for frame_number in self.frames
            if self.frame_path(frame_number).exists()
        }


def _create_retention_video(name: str) -> _RetentionVideo:
    center = Center.objects.create(name=f"{name}-center")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"{name}-video",
        original_file_name=f"{name}.mp4",
        fps=25.0,
        frame_count=FRAME_COUNT,
    )
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[int, Frame] = {}
    for frame_number in range(FRAME_COUNT):
        relative_path = f"frame_{frame_number:07d}.jpg"
        (frame_dir / relative_path).write_bytes(b"frame")
        frames[frame_number] = Frame.objects.create(
            video=video,
            frame_number=frame_number,
            relative_path=relative_path,
            is_extracted=True,
        )
    outside_label, _ = Label.objects.get_or_create(name="outside")
    source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
    return _RetentionVideo(
        video=video,
        frames=frames,
        outside_label=outside_label,
        source=source,
    )


def _run_in_thread(
    target: Callable[[], None], errors: list[Exception]
) -> threading.Thread:
    def runner() -> None:
        try:
            target()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


@pytest.mark.django_db
def test_prunes_only_frames_inside_validated_outside_segments() -> None:
    setup = _create_retention_video("retention-basic")
    setup.segment(label=setup.outside_label, start=2, end=6, validated=True)

    pruned = prune_unused_validated_outside_frames(setup.video)

    assert pruned == 4
    assert setup.extracted_frame_numbers() == {0, 1, 6, 7, 8, 9}
    assert setup.frame_numbers_with_files() == {0, 1, 6, 7, 8, 9}


@pytest.mark.django_db
def test_unvalidated_outside_segment_prunes_nothing() -> None:
    setup = _create_retention_video("retention-unvalidated")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=False)

    assert prune_unused_validated_outside_frames(setup.video) == 0
    assert setup.extracted_frame_numbers() == set(range(FRAME_COUNT))


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overlap_label_name", "overlap_validated"),
    [
        ("polyp", True),
        ("polyp", False),
        ("outside", False),
        (None, True),
    ],
)
def test_frames_overlapping_any_other_segment_are_preserved(
    overlap_label_name: str | None,
    overlap_validated: bool,
) -> None:
    setup = _create_retention_video("retention-overlap")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)
    overlap_label = (
        None
        if overlap_label_name is None
        else Label.objects.get_or_create(name=overlap_label_name)[0]
    )
    setup.segment(label=overlap_label, start=3, end=5, validated=overlap_validated)

    pruned = prune_unused_validated_outside_frames(setup.video)

    assert pruned == FRAME_COUNT - 2
    assert setup.extracted_frame_numbers() == {3, 4}
    assert setup.frame_numbers_with_files() == {3, 4}


@pytest.mark.django_db
def test_annotated_and_dataset_backed_frames_are_preserved() -> None:
    setup = _create_retention_video("retention-annotated")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)
    setup.annotate(1)
    dataset_annotation = setup.annotate(2)
    dataset = AIDataSet.objects.create(
        name="retention-dataset",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    m2m_add_relation(dataset.image_annotations).add(dataset_annotation)

    pruned = prune_unused_validated_outside_frames(setup.video)

    assert pruned == FRAME_COUNT - 2
    assert setup.extracted_frame_numbers() == {1, 2}
    assert setup.frame_numbers_with_files() == {1, 2}


@pytest.mark.django_db
def test_missing_file_is_marked_absent_without_counting_as_pruned() -> None:
    setup = _create_retention_video("retention-missing")
    setup.segment(label=setup.outside_label, start=0, end=2, validated=True)
    setup.frame_path(0).unlink()

    pruned = prune_unused_validated_outside_frames(setup.video)

    assert pruned == 1
    assert setup.extracted_frame_numbers() == set(range(2, FRAME_COUNT))


@pytest.mark.django_db
def test_failed_unlink_leaves_orphan_file_on_absent_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _create_retention_video("retention-unlink-failure")
    setup.segment(label=setup.outside_label, start=0, end=2, validated=True)
    failing_path = setup.frame_path(0)
    real_unlink = frame_retention.safe_unlink_file

    def unlink_or_fail(path: Path, *, missing_ok: bool = True) -> None:
        if path == failing_path:
            raise PermissionError("read-only frame directory")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(frame_retention, "safe_unlink_file", unlink_or_fail)

    pruned = prune_unused_validated_outside_frames(setup.video)

    # The database already records the frame as absent; the stale file is inert.
    assert pruned == 1
    assert setup.extracted_frame_numbers() == set(range(2, FRAME_COUNT))
    assert failing_path.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_prunes_nothing(limit: int) -> None:
    setup = _create_retention_video(f"retention-limit-{abs(limit)}")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)

    assert prune_unused_validated_outside_frames(setup.video, limit=limit) == 0
    assert setup.extracted_frame_numbers() == set(range(FRAME_COUNT))


@pytest.mark.django_db
def test_limit_bounds_one_pruning_batch() -> None:
    setup = _create_retention_video("retention-batch")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)

    assert prune_unused_validated_outside_frames(setup.video, limit=3) == 3
    assert setup.extracted_frame_numbers() == set(range(3, FRAME_COUNT))


@pytest.mark.django_db(transaction=True)
def test_uncommitted_annotation_never_leaves_extracted_frame_without_file() -> None:
    """Deferred foreign keys hide the insert; the frame must end up consistently absent."""
    setup = _create_retention_video("retention-race-writer-first")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)
    contested_frame = 4
    inserted = threading.Event()
    pruning_done = threading.Event()
    errors: list[Exception] = []

    def write_annotation() -> None:
        with transaction.atomic():
            setup.annotate(contested_frame)
            inserted.set()
            # Keep the insert uncommitted while pruning runs.
            assert pruning_done.wait(THREAD_TIMEOUT_SECONDS)

    writer = _run_in_thread(write_annotation, errors)
    assert inserted.wait(THREAD_TIMEOUT_SECONDS)
    pruned = prune_unused_validated_outside_frames(setup.video)
    pruning_done.set()
    writer.join(THREAD_TIMEOUT_SECONDS)

    assert not writer.is_alive()
    assert errors == []
    assert pruned == FRAME_COUNT
    contested = Frame.objects.get(pk=setup.frames[contested_frame].pk)
    assert ImageClassificationAnnotation.objects.filter(frame=contested).exists()
    assert contested.is_extracted is False
    assert setup.extracted_frame_numbers() == setup.frame_numbers_with_files() == set()


@pytest.mark.django_db(transaction=True)
def test_frames_are_persisted_absent_before_files_are_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readers never see an extracted frame whose file is already deleted."""
    setup = _create_retention_video("retention-race-prune-first")
    setup.segment(label=setup.outside_label, start=0, end=FRAME_COUNT, validated=True)
    contested_frame = FRAME_COUNT - 1
    pruning = threading.Event()
    release_pruning = threading.Event()
    annotation_written = threading.Event()
    errors: list[Exception] = []
    real_unlink = frame_retention.safe_unlink_file

    def paused_unlink(path: Path, *, missing_ok: bool = True) -> None:
        if not pruning.is_set():
            pruning.set()
            assert release_pruning.wait(THREAD_TIMEOUT_SECONDS)
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(frame_retention, "safe_unlink_file", paused_unlink)
    pruned_counts: list[int] = []

    def prune() -> None:
        pruned_counts.append(prune_unused_validated_outside_frames(setup.video))

    def write_annotation() -> None:
        setup.annotate(contested_frame)
        annotation_written.set()

    pruner = _run_in_thread(prune, errors)
    assert pruning.wait(THREAD_TIMEOUT_SECONDS)
    assert setup.extracted_frame_numbers() == set()
    writer = _run_in_thread(write_annotation, errors)

    # The annotation commit waits for the frame row lock held while unlinking.
    assert not annotation_written.wait(0.5)
    release_pruning.set()
    pruner.join(THREAD_TIMEOUT_SECONDS)
    writer.join(THREAD_TIMEOUT_SECONDS)

    assert not pruner.is_alive() and not writer.is_alive()
    assert errors == []
    assert pruned_counts == [FRAME_COUNT]
    contested = Frame.objects.get(pk=setup.frames[contested_frame].pk)
    assert contested.is_extracted is False
    assert not setup.frame_path(contested_frame).exists()
    assert ImageClassificationAnnotation.objects.filter(frame=contested).exists()
