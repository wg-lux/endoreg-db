# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from django.core.files.base import ContentFile
from django.test import override_settings

from endoreg_db.models import UploadJob
from endoreg_db.services.hub import ingest
from endoreg_db.services.hub.upload_job_import_lease import (
    acquire_upload_job_import_lease,
)
from endoreg_db.utils.encryption.encrypted import EncryptedStorage


@pytest.mark.django_db
@pytest.mark.parametrize("failure", [None, "hash", "tamper", "consumer"])
def test_encrypted_fallback_verifies_plaintext_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    payload = b"synthetic source bytes" * 200
    storage = EncryptedStorage(location=tmp_path)
    name = storage.save("source.mp4", ContentFile(payload))
    source_path = tmp_path / name
    if failure == "tamper":
        ciphertext = bytearray(source_path.read_bytes())
        ciphertext[-1] ^= 1
        source_path.write_bytes(ciphertext)
    ciphertext_before = source_path.read_bytes()
    expected_hash = hashlib.sha256(payload).hexdigest()
    job = UploadJob.objects.create(
        file=name,
        content_hash="0" * 64 if failure == "hash" else expected_hash,
    )
    lease = acquire_upload_job_import_lease(upload_job_id=str(job.pk), owner="test")
    staging = tmp_path / "staging"
    monkeypatch.setattr(ingest.path_utils, "TRANSCODING_DIR", staging)
    monkeypatch.setattr(ingest, "ensure_local_file", _unavailable)
    with override_settings(MEDIA_ROOT=tmp_path):
        if failure is None:
            with ingest._ensure_upload_job_local_file(job, lease=lease) as path:
                assert path.parent == staging
                assert path.read_bytes() == payload
                assert path.stat().st_mode & 0o777 == 0o600
        elif failure == "consumer":
            with pytest.raises(OSError, match="consumer failed"):
                with ingest._ensure_upload_job_local_file(job, lease=lease):
                    raise OSError("consumer failed")
        else:
            with pytest.raises((OSError, InvalidTag, ValueError, RuntimeError)):
                with ingest._ensure_upload_job_local_file(job, lease=lease):
                    pytest.fail("Invalid source must not reach the consumer")
    assert not list(staging.glob("*"))
    assert source_path.read_bytes() == ciphertext_before
    job.refresh_from_db()
    assert job.content_hash == ("0" * 64 if failure == "hash" else expected_hash)


def _unavailable(_field_file: object) -> AbstractContextManager[Path]:
    raise OSError("storage unavailable")
