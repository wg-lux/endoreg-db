"""Binding acceptance for the public media hash boundary; see docs/code_quality.md."""

from pathlib import Path
from collections.abc import Callable
from typing import cast
import hashlib
import json
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import FileField
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models import VideoFile
from endoreg_db.services.patient_identity import validated_media_identity

from endoreg_db.utils import file_operations, hashs
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile
from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile

pytestmark = pytest.mark.no_db

identity_payload_hash = cast(
    Callable[[JsonObject], str],
    getattr(validated_media_identity, "_canonical_payload_hash"),
)


def test_identity_audit_payload_hash_preserves_canonical_encoding() -> None:
    payload: JsonObject = {"source": "review", "media_pk": "42", "patient_hash": "abc"}
    reordered = dict(reversed(list(payload.items())))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert identity_payload_hash(payload) == hashlib.sha256(canonical).hexdigest()
    assert identity_payload_hash(reordered) == identity_payload_hash(payload)


@pytest.mark.parametrize(
    "payload", [b"", b"abc", "Grüße\0".encode(), bytes(range(256))]
)
def test_serialized_bytes_and_file_hashes_match(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert hashs.get_bytes_hash(payload) == expected
    assert hashs.get_file_hash(path) == expected


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
