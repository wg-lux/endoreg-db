"""Binding acceptance for the public media hash boundary; see docs/code_quality.md."""

from pathlib import Path
import hashlib
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import FileField

from endoreg_db.models import VideoFile

from endoreg_db.utils import file_operations, hashs
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile
from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile


@pytest.mark.parametrize(
    "field_type", [VideoArtifactFieldFile, ReportArtifactFieldFile]
)
@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("payload", [b"", b"canonical media plaintext"])
def test_artifact_hash_delegates_to_central_plaintext_hash(
    tmp_path: Path,
    field_type: type[VideoArtifactFieldFile] | type[ReportArtifactFieldFile],
    encrypted: bool,
    payload: bytes,
) -> None:
    storage = (
        EncryptedStorage(location=tmp_path)
        if encrypted
        else FileSystemStorage(location=tmp_path)
    )
    name = storage.save("artifact.bin", ContentFile(payload))
    field = field_type(VideoFile(), FileField(storage=storage), name)

    assert file_operations.get_file_hash is hashs.get_file_hash
    with patch.object(
        file_operations, "get_file_hash", wraps=hashs.get_file_hash
    ) as shared_hash:
        assert field.get_hash() == hashlib.sha256(payload).hexdigest()
    shared_hash.assert_called_once_with(field)


@pytest.mark.parametrize(
    "field_type", [VideoArtifactFieldFile, ReportArtifactFieldFile]
)
def test_artifact_hash_propagates_storage_failure(
    tmp_path: Path,
    field_type: type[VideoArtifactFieldFile] | type[ReportArtifactFieldFile],
) -> None:
    storage = FileSystemStorage(location=tmp_path)
    field = field_type(VideoFile(), FileField(storage=storage), "missing.bin")

    with pytest.raises(FileNotFoundError):
        field.get_hash()
