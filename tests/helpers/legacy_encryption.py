"""Version-one LXENC fixtures; production writers only emit version two."""

from __future__ import annotations

import base64
import json
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Frozen v1 wire constants: deliberately independent of the current encoder.
MAGIC = b"LXENC01\n"
WRAP_AAD = b"lx-annotate:dek-wrap:v1"
HEADER_LENGTH_STRUCT = struct.Struct(">I")
CHUNK_LENGTH_STRUCT = struct.Struct(">I")


def legacy_v1_encrypted(payload: bytes, key: bytes, *, chunk_size: int = 32) -> bytes:
    """Return version-one bytes exactly as written before LXENC version two."""
    dek, wrap_nonce, nonce_prefix = os.urandom(32), os.urandom(12), os.urandom(8)
    encoded = json.dumps(
        {
            "algorithm": "AESGCM-chunked-v1",
            "chunk_size": chunk_size,
            "nonce_prefix": base64.urlsafe_b64encode(nonce_prefix).decode(),
            "version": 1,
            "wrap_nonce": base64.urlsafe_b64encode(wrap_nonce).decode(),
            "wrapped_dek": base64.urlsafe_b64encode(
                AESGCM(key).encrypt(wrap_nonce, dek, WRAP_AAD)
            ).decode(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    data = MAGIC + HEADER_LENGTH_STRUCT.pack(len(encoded)) + encoded
    for counter, start in enumerate(range(0, len(payload), chunk_size)):
        ciphertext = AESGCM(dek).encrypt(
            nonce_prefix + counter.to_bytes(4, "big"),
            payload[start : start + chunk_size],
            encoded,
        )
        data += CHUNK_LENGTH_STRUCT.pack(len(ciphertext)) + ciphertext
    return data
