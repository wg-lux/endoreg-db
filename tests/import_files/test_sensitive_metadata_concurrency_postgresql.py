"""Real PostgreSQL transaction evidence for sensitive metadata persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Literal
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)
from endoreg_db.models import Center, RawPdfFile, SensitiveMeta, VideoFile


def _write_candidate(
    media_kind: Literal["video", "pdf"], media_id: int, barrier: Barrier
) -> tuple[int, int, int]:
    connections.close_all()
    try:
        media = (
            VideoFile.objects.get(pk=media_id)
            if media_kind == "video"
            else RawPdfFile.objects.get(pk=media_id)
        )
        barrier.wait(timeout=20)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
            meta = persist_sensitive_meta_candidate(
                instance=media,
                candidate=LxSensitiveMeta.model_validate(
                    {
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "dob": "1980-12-10",
                        "examination_date": "2026-09-21",
                    }
                ),
            )
            assert meta.pseudo_patient_id is not None
            assert meta.pseudo_examination_id is not None
            return meta.pk, meta.pseudo_patient_id, meta.pseudo_examination_id
    finally:
        connections.close_all()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("media_kind", ["video", "pdf"])
@pytest.mark.parametrize("same_media", [True, False])
def test_concurrent_metadata_imports_keep_one_patient_and_examination(
    base_db_data: bool,
    media_kind: Literal["video", "pdf"],
    same_media: bool,
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip(
            "Requires PostgreSQL row locking; SQLite is not concurrency evidence"
        )
    center = Center.objects.create(name=f"metadata-concurrency-{uuid4().hex}")
    media_ids: list[int] = []
    for _ in range(1 if same_media else 4):
        media = (
            VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
            if media_kind == "video"
            else RawPdfFile.objects.create(center=center, pdf_hash=uuid4().hex)
        )
        media_ids.append(media.pk)
    jobs = media_ids * 4 if same_media else media_ids
    barrier = Barrier(len(jobs))

    with ThreadPoolExecutor(max_workers=len(jobs)) as workers:
        futures = [
            workers.submit(_write_candidate, media_kind, media_id, barrier)
            for media_id in jobs
        ]
        results = [future.result(timeout=40) for future in futures]

    assert len({patient_id for _, patient_id, _ in results}) == 1
    assert len({exam_id for _, _, exam_id in results}) == 1
    assert len({meta_id for meta_id, _, _ in results}) == len(media_ids)
    assert SensitiveMeta.objects.filter(center=center).count() == len(media_ids)
    for media_id, (meta_id, _, _) in zip(jobs, results, strict=True):
        persisted_media = (
            VideoFile.objects.get(pk=media_id)
            if media_kind == "video"
            else RawPdfFile.objects.get(pk=media_id)
        )
        assert persisted_media.sensitive_meta is not None
        assert persisted_media.sensitive_meta.pk == meta_id


@pytest.mark.django_db(transaction=True)
def test_metadata_transaction_failure_leaves_no_dangling_relation(
    base_db_data: bool,
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL transaction evidence")
    center = Center.objects.create(name=f"metadata-rollback-{uuid4().hex}")
    video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
    before = SensitiveMeta.objects.count()

    with pytest.raises(RuntimeError, match="abort import"):
        with transaction.atomic():
            persist_sensitive_meta_candidate(
                instance=video,
                candidate=LxSensitiveMeta.model_validate({"first_name": "Ada"}),
            )
            raise RuntimeError("abort import")

    video.refresh_from_db()
    assert video.sensitive_meta is None
    assert SensitiveMeta.objects.count() == before
