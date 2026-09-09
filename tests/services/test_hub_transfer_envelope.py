from __future__ import annotations

# pyright: reportPrivateUsage=false

import base64
import hashlib
import io
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeMetadata

from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.services.hub import transfers
from endoreg_db.services.hub.transfer_envelope import (
    HubMediaEnvelopeError,
    _load_recipient_private_key,
    prepare_inbound_hub_envelope,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class _TrackedStream(io.BytesIO):
    requested_read_sizes: list[int | None]

    def __init__(self, *, content: bytes) -> None:
        super().__init__(content)
        self.requested_read_sizes = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.requested_read_sizes.append(size)
        return super().read(-1 if size is None else size)


class HubTransferEnvelopeTests(SimpleTestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.staging_directory = Path(self._temporary_directory.name) / "staging"
        self.private_key = X25519PrivateKey.generate()
        self.private_key_path = Path(self._temporary_directory.name) / "recipient.pem"
        self.private_key_path.write_bytes(
            self.private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.private_key_path.chmod(0o600)
        self.settings_override = override_settings(
            ENDOREG_HUB_TRANSFER_RECIPIENT_PRIVATE_KEY_FILES=(self.private_key_path,),
            ENDOREG_HUB_TRANSFER_REQUIRE_ROOT_OWNED_PRIVATE_KEYS=False,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.transfer_job = cast(
            TransferJob,
            SimpleNamespace(
                transfer_key="transfer-atomic-1",
                source_node=SimpleNamespace(node_key="site-node"),
                source_center=SimpleNamespace(center_key="site-center"),
                target_node=SimpleNamespace(node_key="gs-02"),
                resource_kind=TransferJob.ResourceKind.VIDEO.value,
                resource_hash="resource-hash-1",
                transfer_mode=(
                    TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value
                ),
                resource_rows={},
            ),
        )

    def _envelope(
        self,
        plaintext: bytes,
        *,
        recipient_private_key: X25519PrivateKey | None = None,
    ) -> tuple[HubMediaEnvelopeMetadata, bytes]:
        recipient_private_key = recipient_private_key or self.private_key
        recipient_public_key = recipient_private_key.public_key()
        recipient_key_id = _sha256(
            recipient_public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        plaintext_hash = _sha256(plaintext)
        cast(dict[str, object], self.transfer_job.resource_rows)["video_file"] = {
            "processed_video_hash": plaintext_hash
        }
        ephemeral_private_key = X25519PrivateKey.generate()
        ephemeral_public_key = ephemeral_private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        wrap_salt = os.urandom(16)
        wrap_nonce = os.urandom(12)
        payload_nonce = os.urandom(12)
        data_encryption_key = os.urandom(32)
        values: dict[str, object] = {
            "transfer_key": self.transfer_job.transfer_key,
            "source_node_key": self.transfer_job.source_node.node_key,
            "source_center_key": self.transfer_job.source_center.center_key,
            "target_node_key": self.transfer_job.target_node.node_key,
            "resource_kind": self.transfer_job.resource_kind,
            "resource_hash": self.transfer_job.resource_hash,
            "processed_media_hash": plaintext_hash,
            "plaintext_sha256": plaintext_hash,
            "plaintext_size": len(plaintext),
            "recipient_key_id": recipient_key_id,
            "ephemeral_public_key": _base64url(ephemeral_public_key),
            "wrap_salt": _base64url(wrap_salt),
            "wrap_nonce": _base64url(wrap_nonce),
            "wrapped_data_encryption_key": _base64url(b"0" * 48),
            "payload_nonce": _base64url(payload_nonce),
            "payload_tag": _base64url(b"0" * 16),
        }
        provisional = HubMediaEnvelopeMetadata.model_validate(values)
        wrapping_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=wrap_salt,
            info=b"lx-hub-media-envelope-wrap-v1",
        ).derive(ephemeral_private_key.exchange(recipient_public_key))
        values["wrapped_data_encryption_key"] = _base64url(
            AESGCM(wrapping_key).encrypt(
                wrap_nonce,
                data_encryption_key,
                provisional.authenticated_data(),
            )
        )
        envelope = HubMediaEnvelopeMetadata.model_validate(values)
        encryptor = Cipher(
            algorithms.AES(data_encryption_key),
            modes.GCM(payload_nonce),
        ).encryptor()
        encryptor.authenticate_additional_data(envelope.authenticated_data())
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        values["payload_tag"] = _base64url(encryptor.tag)
        return HubMediaEnvelopeMetadata.model_validate(values), ciphertext

    def _assert_staging_empty(self) -> None:
        self.assertFalse(
            self.staging_directory.exists() and any(self.staging_directory.iterdir())
        )

    def test_decrypts_authenticated_payload_in_bounded_chunks_and_cleans_stage(
        self,
    ) -> None:
        plaintext = b"authenticated-video" * 100
        envelope, ciphertext = self._envelope(plaintext)
        ciphertext_stream = _TrackedStream(content=ciphertext)

        with (
            patch(
                "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
                self.staging_directory,
            ),
            patch("endoreg_db.services.hub.transfer_envelope._CHUNK_SIZE", 17),
            prepare_inbound_hub_envelope(
                transfer_job=self.transfer_job,
                ciphertext_stream=ciphertext_stream,
                ciphertext_size=len(ciphertext),
                envelope_json=envelope.model_dump_json(),
                media_role="processed",
            ) as prepared,
        ):
            recovered = bytearray()
            while chunk := prepared.plaintext_stream.read(13):
                recovered.extend(chunk)
            prepared.require_verified()
            self.assertEqual(bytes(recovered), plaintext)
            self.assertEqual(prepared.ciphertext_sha256, _sha256(ciphertext))

        self.assertGreater(len(ciphertext_stream.requested_read_sizes), 1)
        self.assertEqual(set(ciphertext_stream.requested_read_sizes), {17})
        self._assert_staging_empty()

    def test_legacy_plaintext_service_entrypoint_rejects_before_staging(self) -> None:
        with (
            patch.object(transfers, "_write_uploaded_file_to_temp") as stage_upload,
            self.assertRaisesRegex(ValueError, "Plaintext.*prohibited"),
        ):
            transfers.attach_transfer_media(
                transfer_job=self.transfer_job,
                uploaded_file=SimpleUploadedFile("plaintext.mp4", b"plaintext"),
                media_role="processed",
            )

        stage_upload.assert_not_called()

    def test_replaced_generation_cleanup_runs_only_after_commit(self) -> None:
        storage = SimpleNamespace()
        field_definition = SimpleNamespace(storage=storage)
        current_field = SimpleNamespace(
            field=field_definition,
            name="processed/current-generation.mp4",
        )
        instance = SimpleNamespace(processed_file=current_field)
        callbacks: list[Callable[[], object]] = []

        def capture_on_commit(
            callback: Callable[[], object],
            *,
            robust: bool = False,
        ) -> None:
            self.assertTrue(robust)
            callbacks.append(callback)

        with (
            patch.object(
                transfers.transaction,
                "on_commit",
                side_effect=capture_on_commit,
            ),
            patch.object(transfers, "safe_delete_field_file") as delete_generation,
        ):
            transfers._delete_replaced_generation_after_commit(
                instance=instance,
                field_name="processed_file",
                replaced_name="processed/previous-generation.mp4",
            )
            delete_generation.assert_not_called()
            self.assertEqual(
                current_field.name,
                "processed/current-generation.mp4",
            )

            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            deleted_field = delete_generation.call_args.args[0]
            self.assertEqual(
                deleted_field.name,
                "processed/previous-generation.mp4",
            )
            self.assertEqual(
                current_field.name,
                "processed/current-generation.mp4",
            )

    def test_ciphertext_tamper_fails_authentication_and_cleans_stage(self) -> None:
        plaintext = b"authenticated-video"
        envelope, ciphertext = self._envelope(plaintext)
        tampered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]

        with patch(
            "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
            self.staging_directory,
        ):
            with self.assertRaisesRegex(HubMediaEnvelopeError, "authentication failed"):
                with prepare_inbound_hub_envelope(
                    transfer_job=self.transfer_job,
                    ciphertext_stream=io.BytesIO(tampered),
                    ciphertext_size=len(tampered),
                    envelope_json=envelope.model_dump_json(),
                    media_role="processed",
                ) as prepared:
                    prepared.plaintext_stream.read()

        self._assert_staging_empty()

    def test_wrong_recipient_key_fails_and_cleans_stage(self) -> None:
        plaintext = b"authenticated-video"
        wrong_key = X25519PrivateKey.generate()
        envelope, ciphertext = self._envelope(
            plaintext,
            recipient_private_key=wrong_key,
        )

        with patch(
            "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
            self.staging_directory,
        ):
            with self.assertRaisesRegex(HubMediaEnvelopeError, "not available"):
                with prepare_inbound_hub_envelope(
                    transfer_job=self.transfer_job,
                    ciphertext_stream=io.BytesIO(ciphertext),
                    ciphertext_size=len(ciphertext),
                    envelope_json=envelope.model_dump_json(),
                    media_role="processed",
                ):
                    self.fail("A wrong recipient key must fail before yielding")

        self._assert_staging_empty()

    def test_authenticated_metadata_tamper_is_rejected(self) -> None:
        plaintext = b"authenticated-video"
        envelope, ciphertext = self._envelope(plaintext)
        tampered_values = envelope.model_dump(mode="json")
        tampered_values["payload_nonce"] = _base64url(os.urandom(12))
        tampered = HubMediaEnvelopeMetadata.model_validate(tampered_values)

        with patch(
            "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
            self.staging_directory,
        ):
            with self.assertRaisesRegex(
                HubMediaEnvelopeError,
                "data-encryption key authentication failed",
            ):
                with prepare_inbound_hub_envelope(
                    transfer_job=self.transfer_job,
                    ciphertext_stream=io.BytesIO(ciphertext),
                    ciphertext_size=len(ciphertext),
                    envelope_json=tampered.model_dump_json(),
                    media_role="processed",
                ):
                    self.fail("Authenticated metadata tampering must fail")

        self._assert_staging_empty()

    def test_truncated_ciphertext_is_rejected_before_staging(self) -> None:
        plaintext = b"authenticated-video"
        envelope, ciphertext = self._envelope(plaintext)

        with patch(
            "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
            self.staging_directory,
        ):
            with self.assertRaisesRegex(HubMediaEnvelopeError, "size does not match"):
                with prepare_inbound_hub_envelope(
                    transfer_job=self.transfer_job,
                    ciphertext_stream=io.BytesIO(ciphertext[:-1]),
                    ciphertext_size=len(ciphertext) - 1,
                    envelope_json=envelope.model_dump_json(),
                    media_role="processed",
                ):
                    self.fail("Truncated ciphertext must fail before yielding")

        self._assert_staging_empty()

    def test_identity_binding_mismatches_are_rejected_before_staging(self) -> None:
        plaintext = b"authenticated-video"
        envelope, ciphertext = self._envelope(plaintext)
        for field_name, value in (
            ("transfer_key", "other-transfer"),
            ("source_node_key", "other-site"),
            ("source_center_key", "other-center"),
            ("target_node_key", "other-hub"),
            ("resource_hash", "other-resource"),
        ):
            with self.subTest(field_name=field_name):
                tampered_values = envelope.model_dump(mode="json")
                tampered_values[field_name] = value
                tampered = HubMediaEnvelopeMetadata.model_validate(tampered_values)
                with self.assertRaisesRegex(HubMediaEnvelopeError, field_name):
                    with prepare_inbound_hub_envelope(
                        transfer_job=self.transfer_job,
                        ciphertext_stream=io.BytesIO(ciphertext),
                        ciphertext_size=len(ciphertext),
                        envelope_json=tampered.model_dump_json(),
                        media_role="processed",
                    ):
                        self.fail("Identity mismatch must fail before yielding")
        self._assert_staging_empty()

    @override_settings(ENDOREG_HUB_TRANSFER_REQUIRE_ROOT_OWNED_PRIVATE_KEYS=True)
    def test_private_key_must_be_root_owned_by_default(self) -> None:
        fake_stat = SimpleNamespace(
            st_uid=1000,
            st_mode=stat.S_IFREG | 0o600,
        )
        with patch.object(Path, "stat", return_value=fake_stat):
            with self.assertRaisesRegex(HubMediaEnvelopeError, "owned by the root"):
                _load_recipient_private_key(self.private_key_path)
