# pyright: reportPrivateUsage=false
"""Integration-style tests for video file I/O helpers using real assets."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch

import pytest
from django.core.files import File
from django.core.files.storage import default_storage

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile
from endoreg_db.services.video_files._io import (
    _ensure_local_processed_file,
    _ensure_local_raw_file,
)
from endoreg_db.utils import delete_field_file

pytestmark = pytest.mark.django_db


class _CenterRelation(Protocol):
    def add(self, *objs: Center | int) -> None: ...


class _WritableFieldFile(Protocol):
    def save(self, name: str, content: File[bytes], save: bool = True) -> None: ...


def _add_center(processor: EndoscopyProcessor, center: Center) -> None:
    cast(_CenterRelation, processor.centers).add(center)


def _field_file(field: object) -> _WritableFieldFile:
    return cast(_WritableFieldFile, field)


@pytest.fixture
def center():
    return Center.objects.create(
        name="test_center_file_io",
        display_name="Test Center File IO",
    )


@pytest.fixture
def processor(center: Center) -> EndoscopyProcessor:
    processor = EndoscopyProcessor.objects.create(
        name="test_processor_file_io",
        image_width=1920,
        image_height=1080,
        endoscope_image_x=0,
        endoscope_image_y=0,
        endoscope_image_width=1920,
        endoscope_image_height=1080,
        examination_date_x=0,
        examination_date_y=0,
        examination_date_width=100,
        examination_date_height=50,
        examination_time_x=0,
        examination_time_y=0,
        examination_time_width=100,
        examination_time_height=50,
        patient_first_name_x=0,
        patient_first_name_y=0,
        patient_first_name_width=100,
        patient_first_name_height=50,
        patient_last_name_x=0,
        patient_last_name_y=0,
        patient_last_name_width=100,
        patient_last_name_height=50,
        patient_dob_x=0,
        patient_dob_y=0,
        patient_dob_width=100,
        patient_dob_height=50,
        endoscope_type_x=0,
        endoscope_type_y=0,
        endoscope_type_width=100,
        endoscope_type_height=50,
        endoscope_sn_x=0,
        endoscope_sn_y=0,
        endoscope_sn_width=100,
        endoscope_sn_height=50,
    )
    _add_center(processor, center)
    return processor


@pytest.fixture
def video_with_files(
    center: Center,
    processor: EndoscopyProcessor,
    video_asset_file: Path,
):
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"hash-{uuid.uuid4()}",
    )

    raw_name = f"videos/{video.video_hash}_raw.mp4"
    processed_name = f"anonym_videos/{video.video_hash}_processed.mp4"

    with video_asset_file.open("rb") as raw_handle:
        _field_file(video.raw_file).save(raw_name, File(raw_handle), save=True)

    with video_asset_file.open("rb") as processed_handle:
        _field_file(video.processed_file).save(
            processed_name,
            File(processed_handle),
            save=True,
        )

    video.refresh_from_db()

    stored_raw = raw_name
    stored_processed = processed_name
    video_pk = video.pk

    try:
        yield video
    finally:
        if video_pk and VideoFile.objects.filter(pk=video_pk).exists():
            remaining = VideoFile.objects.get(pk=video_pk)
            delete_field_file(remaining, "raw_file", save=False)
            delete_field_file(remaining, "processed_file", save=False)
            remaining.delete()
        else:
            if stored_raw:
                default_storage.delete(stored_raw)
            if stored_processed:
                default_storage.delete(stored_processed)


def test_delete_with_file_removes_stored_assets(video_with_files: VideoFile):
    video = video_with_files
    raw_name = video.raw_file.name
    processed_name = video.processed_file.name
    pk_value = video.pk

    with patch.object(video, "delete_frames", return_value="ok"):
        video.delete_with_file()

    assert pk_value is not None
    assert raw_name is not None
    assert processed_name is not None
    assert not default_storage.exists(raw_name)
    assert not default_storage.exists(processed_name)
    assert not VideoFile.objects.filter(pk=pk_value).exists()


def test_delete_with_file_handles_pathless_storage(video_with_files: VideoFile):
    video = video_with_files
    raw_name = video.raw_file.name
    processed_name = video.processed_file.name
    pk_value = video.pk

    with (
        patch.object(video, "delete_frames", return_value="ok"),
        patch(
            "endoreg_db.services.video_files._io._get_raw_file_path",
            return_value=None,
        ),
        patch(
            "endoreg_db.services.video_files._io._get_processed_file_path",
            return_value=None,
        ),
    ):
        video.delete_with_file()

    assert pk_value is not None
    assert raw_name is not None
    assert processed_name is not None
    assert not default_storage.exists(raw_name)
    assert not default_storage.exists(processed_name)
    assert not VideoFile.objects.filter(pk=pk_value).exists()


def test_ensure_local_raw_file_downloads_without_path(video_with_files: VideoFile):
    video = video_with_files

    with patch("endoreg_db.utils.storage._resolve_local_path", return_value=None):
        with _ensure_local_raw_file(video) as local_path:
            assert local_path.exists()
            assert local_path.is_file()
            assert local_path.stat().st_size > 0
        assert not local_path.exists()


def test_ensure_local_processed_file_downloads_without_path(
    video_with_files: VideoFile,
):
    video = video_with_files

    with patch("endoreg_db.utils.storage._resolve_local_path", return_value=None):
        with _ensure_local_processed_file(video) as local_path:
            assert local_path.exists()
            assert local_path.is_file()
            assert local_path.stat().st_size > 0
        assert not local_path.exists()
