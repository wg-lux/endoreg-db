"""Private, reloadable key-file manifests for explicitly staged rotations."""

from __future__ import annotations

import base64
import binascii
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class KeyringManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    active: Path
    retiring: tuple[Path, ...] = Field(default=(), max_length=8)
    allow_legacy_default_salt: bool = False


@dataclass(frozen=True)
class SecretKeyring:
    active: bytes = field(repr=False)
    retiring: tuple[bytes, ...] = field(default=(), repr=False)

    @property
    def readers(self) -> tuple[bytes, ...]:
        return (self.active, *self.retiring)


def read_private_file(path: Path, *, limit: int = 65536) -> bytes:
    """Reject links, devices, oversized files, and group/world-readable secrets."""
    if not path.is_absolute():
        raise ValueError("Secret file paths must be absolute")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid not in {0, os.geteuid()}
            ):
                raise ValueError(
                    "Secret files must be private regular files owned by root or the service user"
                )
            payload = source.read(limit + 1)
    except OSError:
        raise ValueError("Cannot read private secret file") from None
    if not payload or len(payload) > limit:
        raise ValueError("Secret file is empty or exceeds its size limit")
    return payload


def load_keyring(
    path: Path, *, kind: Literal["master", "identity", "signing"]
) -> SecretKeyring:
    try:
        manifest = KeyringManifest.model_validate(
            yaml.safe_load(read_private_file(path))
        )
    except (yaml.YAMLError, ValidationError):
        raise ValueError("Invalid private keyring manifest") from None
    paths = (manifest.active, *manifest.retiring)
    if len(set(paths)) != len(paths):
        raise ValueError("Keyring file references must be unique")
    if kind != "identity" and manifest.allow_legacy_default_salt:
        raise ValueError("Legacy salt enrollment applies only to identity salts")
    values: list[bytes] = []
    for index, secret_path in enumerate(paths):
        raw = (
            read_private_file(secret_path, limit=4096)
            .removesuffix(b"\n")
            .removesuffix(b"\r")
        )
        if kind == "master":
            try:
                value = base64.b64decode(raw, altchars=b"-_", validate=True)
            except (ValueError, binascii.Error):
                raise ValueError(
                    "Master key file must contain base64 key material"
                ) from None
            if len(value) not in {16, 24, 32}:
                raise ValueError("Master keys must contain 16, 24 or 32 decoded bytes")
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                raise ValueError("Identity salt must be UTF-8") from None
            if not text or text != text.strip() or "\n" in text or "\r" in text:
                raise ValueError("Identity salt must be a single nonblank line")
            if (
                kind == "identity"
                and text == "default_salt"
                and not (index > 0 and manifest.allow_legacy_default_salt)
            ):
                raise ValueError(
                    "default_salt is permitted only as explicitly enrolled retiring identity material"
                )
            if kind == "signing" and (
                len(text) < 32 or text.startswith("django-insecure-")
            ):
                raise ValueError(
                    "Signing keys must be non-development secrets of at least 32 characters"
                )
            value = raw
        values.append(value)
    if len(set(values)) != len(values):
        raise ValueError("Keyring generations must have distinct secret material")
    return SecretKeyring(values[0], tuple(values[1:]))


def configured_master_keyring() -> SecretKeyring | None:
    path = os.environ.get("LX_ANNOTATE_MASTER_KEYRING_FILE", "")
    return load_keyring(Path(path), kind="master") if path else None


def configured_identity_keyring() -> SecretKeyring | None:
    path = os.environ.get("DJANGO_IDENTITY_SALT_KEYRING_FILE", "")
    return load_keyring(Path(path), kind="identity") if path else None


def configured_signing_keys() -> tuple[str, list[str]] | None:
    """Settings adapter for Django's supported rolling signing-key rotation.

    A compromised key must be omitted from retiring; overlap is for planned
    rotations only. Signing keys are intentionally never used for media.
    """
    path = os.environ.get("DJANGO_SIGNING_KEYRING_FILE", "")
    if not path:
        return None
    ring = load_keyring(Path(path), kind="signing")
    return ring.active.decode("utf-8"), [
        value.decode("utf-8") for value in ring.retiring
    ]
