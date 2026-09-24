"""Private, reloadable key-file manifests for explicitly staged rotations."""

from __future__ import annotations

import base64
import binascii
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class KeyringManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(strict=True, ge=1, le=1)
    active: Path
    retiring: tuple[Path, ...] = Field(default=(), max_length=8)
    allow_legacy_default_salt: bool = Field(default=False, strict=True)


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


def validate_secret_line(value: str) -> str:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError("Secret material must be a single nonblank line")
    return value


def read_secret_line(path: Path) -> str:
    """Read one UTF-8 secret line without normalizing its secret material."""
    raw = read_private_file(path, limit=4096)
    raw = (
        raw.removesuffix(b"\r\n") if raw.endswith(b"\r\n") else raw.removesuffix(b"\n")
    )
    try:
        return validate_secret_line(raw.decode("utf-8"))
    except UnicodeError:
        raise ValueError("Secret material must be UTF-8") from None


def validate_salt_material(value: str, *, allow_legacy: bool = False) -> str:
    validate_secret_line(value)
    if value == "default_salt" and not allow_legacy:
        raise ValueError(
            "default_salt is permitted only as explicitly enrolled retiring identity material"
        )
    return value


def validate_signing_key(value: str) -> str:
    validate_secret_line(value)
    if len(value) < 32 or value.startswith("django-insecure-"):
        raise ValueError(
            "Signing keys must be non-development secrets of at least 32 characters"
        )
    return value


def decode_master_key(value: str) -> bytes:
    validate_secret_line(value)
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Master key must contain base64 key material") from None
    if len(decoded) not in {16, 24, 32}:
        raise ValueError("Master keys must contain 16, 24 or 32 decoded bytes")
    return decoded


def load_keyring(
    path: Path, *, kind: Literal["master", "identity", "signing"]
) -> SecretKeyring:
    try:
        manifest = _parse_manifest(read_private_file(path))
    except (yaml.YAMLError, ValidationError):
        raise ValueError("Invalid private keyring manifest") from None
    paths = (manifest.active, *manifest.retiring)
    if len(set(paths)) != len(paths):
        raise ValueError("Keyring file references must be unique")
    if kind != "identity" and manifest.allow_legacy_default_salt:
        raise ValueError("Legacy salt enrollment applies only to identity salts")
    values: list[bytes] = []
    for index, secret_path in enumerate(paths):
        text = read_secret_line(secret_path)
        if kind == "master":
            value = decode_master_key(text)
        elif kind == "identity":
            value = validate_salt_material(
                text, allow_legacy=index > 0 and manifest.allow_legacy_default_salt
            ).encode("utf-8")
        else:
            value = validate_signing_key(text).encode("utf-8")
        values.append(value)
    if len(set(values)) != len(values):
        raise ValueError("Keyring generations must have distinct secret material")
    return SecretKeyring(values[0], tuple(values[1:]))


def _parse_manifest(payload: bytes) -> KeyringManifest:
    loader = yaml.SafeLoader(payload)
    try:
        node = loader.get_single_node()
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("Invalid private keyring manifest")
        names = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
        if (
            len(names) != len(node.value)
            or len(set(names)) != len(names)
            or "<<" in names
        ):
            raise ValueError(
                "Keyring manifest fields must be unique without YAML merges"
            )
        return KeyringManifest.model_validate(loader.construct_mapping(node, deep=True))
    finally:
        cast(Callable[[], None], getattr(loader, "dispose"))()


def configured_master_keyring() -> SecretKeyring | None:
    path = os.environ.get("LX_ANNOTATE_MASTER_KEYRING_FILE", "")
    return load_keyring(Path(path), kind="master") if path else None


def configured_master_keys() -> SecretKeyring | None:
    """Resolve one immutable key snapshot for Python and native readers."""
    ring = configured_master_keyring()
    if ring is not None:
        return ring
    value = os.environ.get("LX_ANNOTATE_MASTER_KEY", "")
    path = os.environ.get("LX_ANNOTATE_MASTER_KEY_FILE", "")
    if not value and path:
        value = read_secret_line(Path(path))
    return SecretKeyring(decode_master_key(value)) if value else None


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
        secret_path = os.environ.get("DJANGO_SECRET_KEY_FILE", "")
        value = (
            read_secret_line(Path(secret_path))
            if secret_path
            else os.environ.get("DJANGO_SECRET_KEY", "")
        )
        return (validate_signing_key(value), []) if value else None
    ring = load_keyring(Path(path), kind="signing")
    return ring.active.decode("utf-8"), [
        value.decode("utf-8") for value in ring.retiring
    ]
